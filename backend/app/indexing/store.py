"""Persistence of indexing results (Phase 3, Milestone 5).

The index of a repository is a *snapshot at one commit*. ``apply_snapshot`` moves it
to a new commit inside the caller's transaction:

* deleted files lose their chunks, symbols and dependencies;
* new/changed files (different blob SHA) have their rows replaced;
* unchanged files keep their rows; only ``commit_sha`` is advanced;
* dependency targets are re-resolved against the new set of paths;
* embeddings no longer referenced by any chunk are removed.

Either the whole snapshot is applied or (on rollback) the previous snapshot stays
intact. Embeddings are stored separately (``chunk_embeddings``) and saved after
every API batch, so interrupted jobs and unchanged chunks are never re-embedded.
"""

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import Float, and_, bindparam, delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.indexing.chunking import Chunk
from app.indexing.dependencies import PathIndex, resolve_import
from app.indexing.parsing import ParseResult
from app.models import (
    ChunkEmbedding,
    CodeChunk,
    CodeSymbol,
    Dependency,
    IndexedFile,
    IndexingJob,
    Repository,
)

_KEY_BATCH = 500


class RepositoryGoneError(Exception):
    """The repository was disconnected while indexing ran."""


class EmbeddingDimensionMismatchError(Exception):
    """The database vector column does not match GEMINI_EMBEDDING_DIMENSIONS."""


@dataclass
class FileIndexData:
    path: str
    blob_sha: str
    language: str
    size_bytes: int
    parse: ParseResult
    chunks: Sequence[Chunk]
    embedding_keys: Sequence[Optional[str]]  # aligned with chunks


@dataclass
class SkippedFile:
    """A file excluded after content screening (kept to avoid re-downloading it)."""

    path: str
    blob_sha: str
    language: str
    size_bytes: int
    reason: str


@dataclass
class SnapshotResult:
    files_total: int
    chunks_total: int
    files_upserted: int
    files_deleted: int
    embeddings_removed: int


@dataclass
class SearchHit:
    chunk: CodeChunk
    file_path: str
    score: float  # cosine similarity in [-1, 1]


def _dialect(session: AsyncSession) -> str:
    return session.get_bind().dialect.name


def _batched(items: Sequence, size: int) -> Iterable[Sequence]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


# ---------------------------------------------------------------------- reads

async def load_file_hashes(session: AsyncSession, repository_id: uuid.UUID) -> Dict[str, str]:
    rows = await session.execute(
        select(IndexedFile.file_path, IndexedFile.file_hash).where(IndexedFile.repository_id == repository_id)
    )
    return {path: blob for path, blob in rows.all()}


async def existing_embedding_keys(session: AsyncSession, repository_id: uuid.UUID,
                                  keys: Iterable[str]) -> Set[str]:
    wanted = sorted(set(keys))
    found: Set[str] = set()
    for batch in _batched(wanted, _KEY_BATCH):
        rows = await session.execute(
            select(ChunkEmbedding.embedding_key).where(
                ChunkEmbedding.repository_id == repository_id,
                ChunkEmbedding.embedding_key.in_(batch),
            )
        )
        found.update(rows.scalars().all())
    return found


async def verify_embedding_dimensions(session: AsyncSession, dimensions: int) -> None:
    """Fail fast if the vector column was created for a different dimension."""
    if _dialect(session) != "postgresql":
        return
    column_type = (
        await session.execute(text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = 'chunk_embeddings'::regclass AND attname = 'embedding'"
        ))
    ).scalar()
    if column_type != f"vector({dimensions})":
        raise EmbeddingDimensionMismatchError(
            f"chunk_embeddings.embedding is {column_type}, but GEMINI_EMBEDDING_DIMENSIONS={dimensions}. "
            "Restore the previous setting, or add a migration for the new dimension and re-index."
        )


# ---------------------------------------------------------------------- writes

async def save_embeddings(session: AsyncSession, repository_id: uuid.UUID, model: str, dimensions: int,
                          rows: Sequence[Tuple[str, List[float]]]) -> int:
    """Insert embeddings, ignoring keys that already exist. Returns rows attempted."""
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    values = [
        {"id": uuid.uuid4(), "repository_id": repository_id, "embedding_key": key, "model": model,
         "dimensions": dimensions, "embedding": vector, "created_at": now, "updated_at": now}
        for key, vector in rows
    ]
    if _dialect(session) == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    statement = dialect_insert(ChunkEmbedding).on_conflict_do_nothing(
        index_elements=["repository_id", "embedding_key"]
    )
    await session.execute(statement, values)
    return len(values)


async def _delete_file_rows(session: AsyncSession, file_ids: Sequence[uuid.UUID]) -> None:
    """Remove a file's derived rows explicitly (SQLite does not enforce FK cascades)."""
    for batch in _batched(list(file_ids), _KEY_BATCH):
        await session.execute(delete(Dependency).where(Dependency.source_file_id.in_(batch)))
        await session.execute(
            update(Dependency).where(Dependency.target_file_id.in_(batch)).values(target_file_id=None)
        )
        await session.execute(delete(CodeChunk).where(CodeChunk.file_id.in_(batch)))
        await session.execute(delete(CodeSymbol).where(CodeSymbol.file_id.in_(batch)))


def _symbol_rows(repository_id: uuid.UUID, file_id: uuid.UUID, parse: ParseResult) -> List[dict]:
    return [
        {
            "id": uuid.uuid4(), "file_id": file_id, "repository_id": repository_id,
            "name": s.name[:255], "symbol_type": s.kind, "start_line": s.start_line,
            "end_line": s.end_line, "signature": s.signature, "docstring": s.docstring,
            "qualified_name": s.qualified_name[:512], "parent_name": (s.parent or None) and s.parent[:512],
            "start_byte": s.start_byte, "end_byte": s.end_byte, "is_exported": s.is_exported,
            "is_async": s.is_async, "decorators": list(s.decorators) or None, "bases": list(s.bases) or None,
        }
        for s in parse.symbols
    ]


def _chunk_rows(repository_id: uuid.UUID, file_id: uuid.UUID, commit_sha: str,
                chunks: Sequence[Chunk], keys: Sequence[Optional[str]]) -> List[dict]:
    return [
        {
            "id": c.id, "file_id": file_id, "repository_id": repository_id,
            "symbol_name": c.symbol_name and c.symbol_name[:255], "symbol_type": c.chunk_type,
            "start_line": c.start_line, "end_line": c.end_line, "content": c.content,
            "commit_sha": commit_sha, "qualified_name": c.qualified_name and c.qualified_name[:512],
            "parent_symbol": c.parent_symbol and c.parent_symbol[:512], "start_byte": c.start_byte,
            "end_byte": c.end_byte, "content_hash": c.content_hash, "embedding_key": key,
            "token_count": c.token_count, "context": c.context, "part_index": c.part_index,
            "part_count": c.part_count,
        }
        for c, key in zip(chunks, keys)
    ]


def _dependency_rows(repository_id: uuid.UUID, file_id: uuid.UUID, parse: ParseResult) -> List[dict]:
    rows = []
    for imp in parse.imports:
        local = imp.alias or imp.imported or imp.module
        rows.append({
            "id": uuid.uuid4(), "repository_id": repository_id, "source_file_id": file_id,
            "source_name": local[:255], "target_name": imp.module[:255], "relationship_type": "IMPORTS",
            "imported_name": imp.imported and imp.imported[:255], "line": imp.line,
        })
    return rows


async def _insert(session: AsyncSession, model, rows: List[dict]) -> None:
    if not rows:
        return
    now = datetime.now(timezone.utc)
    for row in rows:
        row.setdefault("created_at", now)
        row.setdefault("updated_at", now)
    for batch in _batched(rows, 1000):
        await session.execute(insert(model), list(batch))


async def refresh_dependency_targets(session: AsyncSession, repository_id: uuid.UUID) -> int:
    """Re-resolve every import of the repository against its current file paths."""
    paths = (
        await session.execute(select(IndexedFile.file_path).where(
            IndexedFile.repository_id == repository_id, IndexedFile.skip_reason.is_(None)))
    ).scalars().all()
    index = PathIndex(paths)
    rows = (
        await session.execute(
            select(Dependency.id, Dependency.target_name, Dependency.imported_name, Dependency.target_path,
                   IndexedFile.file_path, IndexedFile.language)
            .join(IndexedFile, IndexedFile.id == Dependency.source_file_id)
            .where(Dependency.repository_id == repository_id, Dependency.relationship_type == "IMPORTS")
        )
    ).all()
    changes = []
    for dep_id, module, imported, current, importer, language in rows:
        target = resolve_import(importer, language, module, imported, index)
        if target != current:
            changes.append({"id": dep_id, "target_path": target})
    for batch in _batched(changes, 1000):
        await session.execute(update(Dependency), list(batch))
    target_id = (
        select(IndexedFile.id)
        .where(IndexedFile.repository_id == Dependency.repository_id, IndexedFile.file_path == Dependency.target_path,
               IndexedFile.skip_reason.is_(None))
        .scalar_subquery()
    )
    await session.execute(
        update(Dependency).where(Dependency.repository_id == repository_id).values(target_file_id=target_id)
    )
    return len(changes)


async def apply_snapshot(
    session: AsyncSession,
    repository_id: uuid.UUID,
    commit_sha: str,
    upserts: Sequence[FileIndexData],
    deleted_paths: Sequence[str],
    skipped: Sequence[SkippedFile] = (),
) -> SnapshotResult:
    """Move the repository's index to ``commit_sha``. Does not commit."""
    repo = (
        await session.execute(select(Repository).where(Repository.id == repository_id).with_for_update())
    ).scalar_one_or_none()
    if repo is None:
        raise RepositoryGoneError("Repository no longer exists")

    files = {
        f.file_path: f
        for f in (await session.execute(select(IndexedFile).where(IndexedFile.repository_id == repository_id))).scalars()
    }

    removed_ids = [files[p].id for p in deleted_paths if p in files]
    await _delete_file_rows(session, removed_ids)
    for batch in _batched(removed_ids, _KEY_BATCH):
        await session.execute(delete(IndexedFile).where(IndexedFile.id.in_(batch)))

    changed_ids: List[uuid.UUID] = []
    new_rows: Dict[str, List[dict]] = {"symbols": [], "chunks": [], "deps": []}
    for data in upserts:
        record = files.get(data.path)
        if record is None:
            record = IndexedFile(repository_id=repository_id, file_path=data.path, language=data.language,
                                 size_bytes=data.size_bytes, file_hash=data.blob_sha)
            session.add(record)
            files[data.path] = record
        else:
            changed_ids.append(record.id)
        record.language = data.language
        record.size_bytes = data.size_bytes
        record.file_hash = data.blob_sha
        record.line_count = data.parse.line_count
        record.has_parse_errors = data.parse.has_errors
        record.is_deleted = False
        record.skip_reason = None
    for item in skipped:
        record = files.get(item.path)
        if record is None:
            record = IndexedFile(repository_id=repository_id, file_path=item.path, language=item.language,
                                 size_bytes=item.size_bytes, file_hash=item.blob_sha)
            session.add(record)
            files[item.path] = record
        else:
            changed_ids.append(record.id)
        record.language = item.language
        record.size_bytes = item.size_bytes
        record.file_hash = item.blob_sha
        record.line_count = 0
        record.has_parse_errors = False
        record.skip_reason = item.reason
    await session.flush()
    await _delete_file_rows(session, changed_ids)

    for data in upserts:
        file_id = files[data.path].id
        new_rows["symbols"] += _symbol_rows(repository_id, file_id, data.parse)
        new_rows["chunks"] += _chunk_rows(repository_id, file_id, commit_sha, data.chunks, data.embedding_keys)
        new_rows["deps"] += _dependency_rows(repository_id, file_id, data.parse)
    await _insert(session, CodeSymbol, new_rows["symbols"])
    await _insert(session, CodeChunk, new_rows["chunks"])
    await _insert(session, Dependency, new_rows["deps"])

    # Unchanged files and chunks are now part of the new commit's snapshot.
    await session.execute(
        update(IndexedFile).where(IndexedFile.repository_id == repository_id).values(commit_sha=commit_sha)
    )
    await session.execute(
        update(CodeChunk).where(CodeChunk.repository_id == repository_id).values(commit_sha=commit_sha)
    )
    await refresh_dependency_targets(session, repository_id)

    referenced = select(CodeChunk.embedding_key).where(
        CodeChunk.repository_id == repository_id, CodeChunk.embedding_key.is_not(None)
    )
    removed = await session.execute(
        delete(ChunkEmbedding).where(
            ChunkEmbedding.repository_id == repository_id, ChunkEmbedding.embedding_key.not_in(referenced)
        )
    )

    files_total = (await session.execute(
        select(func.count()).select_from(IndexedFile).where(
            IndexedFile.repository_id == repository_id, IndexedFile.skip_reason.is_(None))
    )).scalar_one()
    chunks_total = (await session.execute(
        select(func.count()).select_from(CodeChunk).where(CodeChunk.repository_id == repository_id)
    )).scalar_one()
    repo.total_files = files_total
    repo.total_chunks = chunks_total
    repo.last_indexed_commit = commit_sha
    repo.last_indexed_at = datetime.now(timezone.utc)
    repo.is_indexed = True
    repo.indexing_status = "indexed"
    await session.flush()
    return SnapshotResult(
        files_total=files_total, chunks_total=chunks_total, files_upserted=len(upserts),
        files_deleted=len(removed_ids), embeddings_removed=removed.rowcount or 0,
    )


async def delete_repository_index(session: AsyncSession, repository_id: uuid.UUID) -> None:
    """Remove every indexing row of a repository (explicit, dialect-independent)."""
    file_ids = (await session.execute(
        select(IndexedFile.id).where(IndexedFile.repository_id == repository_id)
    )).scalars().all()
    await _delete_file_rows(session, file_ids)
    await session.execute(delete(Dependency).where(Dependency.repository_id == repository_id))
    await session.execute(delete(CodeChunk).where(CodeChunk.repository_id == repository_id))
    await session.execute(delete(CodeSymbol).where(CodeSymbol.repository_id == repository_id))
    await session.execute(delete(IndexedFile).where(IndexedFile.repository_id == repository_id))
    await session.execute(delete(ChunkEmbedding).where(ChunkEmbedding.repository_id == repository_id))
    await session.execute(delete(IndexingJob).where(IndexingJob.repository_id == repository_id))


# ---------------------------------------------------------------------- search

def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def search_similar_chunks(session: AsyncSession, repository_id: uuid.UUID,
                                query_vector: Sequence[float], limit: int = 10) -> List[SearchHit]:
    """Nearest chunks by cosine similarity, restricted to one repository.

    PostgreSQL uses pgvector's ``<=>`` operator (HNSW index); other dialects compute
    cosine similarity in Python (for tests and small local databases).
    """
    from app.models.indexing import EMBEDDING_DIMENSIONS

    if len(query_vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"Query vector has {len(query_vector)} dimensions, expected {EMBEDDING_DIMENSIONS}")
    limit = max(1, min(limit, 100))
    join = and_(ChunkEmbedding.repository_id == CodeChunk.repository_id,
                ChunkEmbedding.embedding_key == CodeChunk.embedding_key)

    if _dialect(session) == "postgresql":
        vector_param = bindparam("query_vector", list(query_vector), type_=ChunkEmbedding.__table__.c.embedding.type)
        distance = ChunkEmbedding.embedding.op("<=>", return_type=Float)(vector_param)
        rows = (await session.execute(
            select(CodeChunk, IndexedFile.file_path, distance.label("distance"))
            .join(ChunkEmbedding, join)
            .join(IndexedFile, IndexedFile.id == CodeChunk.file_id)
            .where(CodeChunk.repository_id == repository_id)
            .order_by(distance)
            .limit(limit)
        )).all()
        return [SearchHit(chunk=c, file_path=p, score=1.0 - float(d)) for c, p, d in rows]

    rows = (await session.execute(
        select(CodeChunk, IndexedFile.file_path, ChunkEmbedding.embedding)
        .join(ChunkEmbedding, join)
        .join(IndexedFile, IndexedFile.id == CodeChunk.file_id)
        .where(CodeChunk.repository_id == repository_id)
    )).all()
    hits = [SearchHit(chunk=c, file_path=p, score=_cosine(query_vector, v)) for c, p, v in rows]
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]
