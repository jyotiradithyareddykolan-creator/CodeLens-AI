"""Persistence tests on SQLite and, when CODELENS_TEST_POSTGRES_URL is set, on live
PostgreSQL + pgvector with the schema built by Alembic migrations."""

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.indexing.store as store
from app.core.config import settings
from app.indexing.chunking import chunk_file
from app.indexing.dependencies import PathIndex, resolve_import
from app.indexing.parsing import parse_file
from app.indexing.store import (
    EmbeddingDimensionMismatchError,
    FileIndexData,
    RepositoryGoneError,
    apply_snapshot,
    delete_repository_index,
    existing_embedding_keys,
    load_file_hashes,
    save_embeddings,
    search_similar_chunks,
    verify_embedding_dimensions,
)
from app.models import (
    ChunkEmbedding,
    CodeChunk,
    CodeSymbol,
    Dependency,
    GitHubInstallation,
    IndexedFile,
    IndexingJob,
    Repository,
    User,
)
from app.models.indexing import EMBEDDING_DIMENSIONS

POSTGRES_URL = os.environ.get("CODELENS_TEST_POSTGRES_URL")
C1, C2 = "1" * 40, "2" * 40


@pytest.fixture(scope="module")
def postgres_schema():
    """Build the schema with Alembic on the disposable PostgreSQL database (if configured)."""
    if not POSTGRES_URL:
        yield None
        return
    from alembic import command

    from tests.test_migrations import alembic_config

    original = settings.DATABASE_URL
    settings.DATABASE_URL = POSTGRES_URL
    try:
        cfg = alembic_config()
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        yield POSTGRES_URL
        command.downgrade(cfg, "base")
    finally:
        settings.DATABASE_URL = original


@pytest.fixture(params=["sqlite", "postgresql"])
def backend(request):
    if request.param == "postgresql" and not POSTGRES_URL:
        pytest.skip("CODELENS_TEST_POSTGRES_URL not set")
    return request.param


@pytest_asyncio.fixture
async def session(backend, db_session, postgres_schema):
    if backend == "sqlite":
        yield db_session
        return
    engine = create_async_engine(POSTGRES_URL.replace("+psycopg", "+asyncpg"), poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE dependencies, code_chunks, code_symbols, chunk_embeddings, indexing_jobs, indexed_files, "
            "repository_branches, repositories, github_installations, users CASCADE"
        ))
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def make_repo(session: AsyncSession, n: int = 1) -> uuid.UUID:
    user = User(github_id=1000 + n, username=f"u{n}")
    session.add(user)
    await session.flush()
    inst = GitHubInstallation(user_id=user.id, installation_id=2000 + n, account_login=f"org{n}")
    session.add(inst)
    await session.flush()
    repo = Repository(installation_id=inst.id, github_repo_id=3000 + n, name=f"r{n}",
                      owner=f"org{n}", full_name=f"org{n}/r{n}")
    session.add(repo)
    await session.commit()
    return repo.id


def file_data(repo_id, path, language, text_, commit, blob=None):
    parsed = parse_file(path, language, text_)
    chunks = chunk_file(parsed, repository_id=repo_id, commit_sha=commit)
    keys = [c.content_hash for c in chunks]  # stand-in embedding keys
    return FileIndexData(path=path, blob_sha=blob or (str(abs(hash(text_)) % 10**40).rjust(40, "0")),
                         language=language, size_bytes=len(text_.encode()), parse=parsed.result,
                         chunks=chunks, embedding_keys=keys)


PKG_INIT = "from .models import User\n"
MODELS = "class User:\n    def name(self):\n        return 'x'\n"
SERVICE = "from pkg.models import User\nfrom pkg.missing import Thing\n\n\ndef load():\n    return User()\n"
TS_MAIN = 'import { helper } from "./util";\nimport React from "react";\n\nexport const run = () => helper();\n'
TS_UTIL = "export function helper() {\n  return 1;\n}\n"


def snapshot_v1(repo_id):
    return [
        file_data(repo_id, "src/pkg/__init__.py", "python", PKG_INIT, C1),
        file_data(repo_id, "src/pkg/models.py", "python", MODELS, C1),
        file_data(repo_id, "src/pkg/service.py", "python", SERVICE, C1),
        file_data(repo_id, "web/main.ts", "typescript", TS_MAIN, C1),
        file_data(repo_id, "web/util.ts", "typescript", TS_UTIL, C1),
    ]


async def count(session, model, repo_id):
    return (await session.execute(
        select(func.count()).select_from(model).where(model.repository_id == repo_id)
    )).scalar_one()


@pytest.mark.asyncio
async def test_initial_snapshot_persists_files_symbols_chunks_and_dependencies(session):
    repo_id = await make_repo(session)
    data = snapshot_v1(repo_id)
    result = await apply_snapshot(session, repo_id, C1, data, [])
    await session.commit()

    assert result.files_total == 5 and result.files_deleted == 0
    expected_chunks = sum(len(d.chunks) for d in data)
    assert result.chunks_total == expected_chunks == await count(session, CodeChunk, repo_id)
    assert await count(session, CodeSymbol, repo_id) == sum(len(d.parse.symbols) for d in data)

    repo = await session.get(Repository, repo_id)
    await session.refresh(repo)
    assert (repo.last_indexed_commit, repo.total_files, repo.total_chunks, repo.indexing_status, repo.is_indexed) == (
        C1, 5, expected_chunks, "indexed", True)

    # Chunk metadata round-trips exactly.
    source = data[1].chunks[0]
    row = (await session.execute(select(CodeChunk).where(CodeChunk.id == source.id))).scalar_one()
    assert (row.content, row.start_line, row.end_line, row.start_byte, row.end_byte) == (
        source.content, source.start_line, source.end_line, source.start_byte, source.end_byte)
    assert (row.symbol_type, row.qualified_name, row.content_hash, row.commit_sha) == (
        "class", "User", source.content_hash, C1)

    method = (await session.execute(select(CodeSymbol).where(CodeSymbol.qualified_name == "User.name"))).scalar_one()
    assert (method.symbol_type, method.parent_name, method.repository_id) == ("method", "User", repo_id)

    deps = {(d.source_name, d.target_name): d for d in (await session.execute(
        select(Dependency).where(Dependency.repository_id == repo_id))).scalars()}
    files = {f.file_path: f.id for f in (await session.execute(
        select(IndexedFile).where(IndexedFile.repository_id == repo_id))).scalars()}
    assert deps[("User", ".models")].target_path == "src/pkg/models.py"
    assert deps[("User", ".models")].target_file_id == files["src/pkg/models.py"]
    assert deps[("User", "pkg.models")].target_path == "src/pkg/models.py"  # absolute, under src/
    assert deps[("Thing", "pkg.missing")].target_path is None
    assert deps[("helper", "./util")].target_file_id == files["web/util.ts"]
    assert deps[("React", "react")].target_path is None  # external package


@pytest.mark.asyncio
async def test_reapplying_the_same_snapshot_is_idempotent(session):
    repo_id = await make_repo(session)
    await apply_snapshot(session, repo_id, C1, snapshot_v1(repo_id), [])
    await session.commit()
    before = [await count(session, m, repo_id) for m in (IndexedFile, CodeChunk, CodeSymbol, Dependency)]
    await apply_snapshot(session, repo_id, C1, snapshot_v1(repo_id), [])
    await session.commit()
    after = [await count(session, m, repo_id) for m in (IndexedFile, CodeChunk, CodeSymbol, Dependency)]
    assert before == after


@pytest.mark.asyncio
async def test_incremental_snapshot_updates_only_changed_files(session):
    repo_id = await make_repo(session)
    await apply_snapshot(session, repo_id, C1, snapshot_v1(repo_id), [])
    await session.commit()
    unchanged_ids = set((await session.execute(
        select(CodeChunk.id).join(IndexedFile).where(IndexedFile.file_path == "web/util.ts"))).scalars())

    changed_models = "class User:\n    def name(self):\n        return 'y'\n\n\nclass Admin(User):\n    pass\n"
    added = "class Thing:\n    pass\n"
    upserts = [
        file_data(repo_id, "src/pkg/models.py", "python", changed_models, C2),
        file_data(repo_id, "src/pkg/missing.py", "python", added, C2),
    ]
    result = await apply_snapshot(session, repo_id, C2, upserts, ["web/main.ts"])
    await session.commit()

    assert (result.files_total, result.files_deleted, result.files_upserted) == (5, 1, 2)
    hashes = await load_file_hashes(session, repo_id)
    assert set(hashes) == {"src/pkg/__init__.py", "src/pkg/models.py", "src/pkg/service.py",
                           "src/pkg/missing.py", "web/util.ts"}
    # Unchanged file kept its chunk rows; every chunk now belongs to the new commit.
    kept = set((await session.execute(
        select(CodeChunk.id).join(IndexedFile).where(IndexedFile.file_path == "web/util.ts"))).scalars())
    assert kept == unchanged_ids
    commits = set((await session.execute(select(CodeChunk.commit_sha).where(CodeChunk.repository_id == repo_id))).scalars())
    assert commits == {C2}
    assert "Admin" in set((await session.execute(select(CodeSymbol.name))).scalars())
    # Deleted file's rows are gone.
    assert not (await session.execute(select(Dependency).where(Dependency.target_name == "./util"))).scalars().all()
    # A previously unresolved import of an unchanged file now resolves to the new file.
    dep = (await session.execute(select(Dependency).where(Dependency.target_name == "pkg.missing"))).scalar_one()
    assert dep.target_path == "src/pkg/missing.py" and dep.target_file_id is not None


@pytest.mark.asyncio
async def test_failed_apply_rolls_back_and_keeps_previous_snapshot(session, monkeypatch):
    repo_id = await make_repo(session)
    await apply_snapshot(session, repo_id, C1, snapshot_v1(repo_id), [])
    await session.commit()
    before = [await count(session, m, repo_id) for m in (IndexedFile, CodeChunk, CodeSymbol, Dependency)]

    original = store._insert

    async def failing_insert(s, model, rows):
        if model is Dependency:
            raise RuntimeError("simulated crash mid-apply")
        await original(s, model, rows)

    monkeypatch.setattr(store, "_insert", failing_insert)
    with pytest.raises(RuntimeError):
        await apply_snapshot(session, repo_id, C2,
                             [file_data(repo_id, "src/pkg/models.py", "python", "X = 1\n", C2)], ["web/util.ts"])
    await session.rollback()

    after = [await count(session, m, repo_id) for m in (IndexedFile, CodeChunk, CodeSymbol, Dependency)]
    assert after == before
    repo = await session.get(Repository, repo_id)
    await session.refresh(repo)
    assert repo.last_indexed_commit == C1


@pytest.mark.asyncio
async def test_apply_for_deleted_repository_raises(session):
    with pytest.raises(RepositoryGoneError):
        await apply_snapshot(session, uuid.uuid4(), C1, [], [])


def unit(index: int):
    v = [0.0] * EMBEDDING_DIMENSIONS
    v[index] = 1.0
    return v


@pytest.mark.asyncio
async def test_embeddings_are_deduplicated_reused_searched_and_garbage_collected(session):
    repo_id = await make_repo(session, 1)
    other_repo = await make_repo(session, 2)
    data = snapshot_v1(repo_id)
    keys = [k for d in data for k in d.embedding_keys]
    rows = [(k, unit(i % EMBEDDING_DIMENSIONS)) for i, k in enumerate(keys)]
    await save_embeddings(session, repo_id, "gemini-embedding-2", EMBEDDING_DIMENSIONS, rows)
    await save_embeddings(session, repo_id, "gemini-embedding-2", EMBEDDING_DIMENSIONS, rows[:3])  # duplicates ignored
    await save_embeddings(session, repo_id, "gemini-embedding-2", EMBEDDING_DIMENSIONS, [("orphan" + "0" * 58, unit(0))])
    await save_embeddings(session, other_repo, "gemini-embedding-2", EMBEDDING_DIMENSIONS, [(keys[0], unit(0))])
    await session.commit()
    assert await count(session, ChunkEmbedding, repo_id) == len(set(keys)) + 1
    assert await existing_embedding_keys(session, repo_id, keys + ["nope"]) == set(keys)

    result = await apply_snapshot(session, repo_id, C1, data, [])
    await session.commit()
    assert result.embeddings_removed == 1  # the orphan
    assert await count(session, ChunkEmbedding, other_repo) == 1  # other repositories untouched

    target_index = keys.index(data[4].chunks[0].content_hash) % EMBEDDING_DIMENSIONS
    hits = await search_similar_chunks(session, repo_id, unit(target_index), limit=3)
    assert hits[0].chunk.embedding_key == data[4].chunks[0].content_hash
    assert hits[0].file_path == "web/util.ts"
    assert hits[0].score == pytest.approx(1.0, abs=1e-5)
    assert all(h.chunk.repository_id == repo_id for h in hits)
    assert hits[0].score >= hits[-1].score

    with pytest.raises(ValueError):
        await search_similar_chunks(session, repo_id, [1.0, 0.0])


@pytest.mark.asyncio
async def test_duplicate_chunk_rows_are_rejected_by_unique_index(session):
    repo_id = await make_repo(session)
    await apply_snapshot(session, repo_id, C1, snapshot_v1(repo_id)[:1], [])
    await session.commit()
    existing = (await session.execute(select(CodeChunk).where(CodeChunk.repository_id == repo_id))).scalars().first()
    session.add(CodeChunk(file_id=existing.file_id, repository_id=repo_id, symbol_type="module", start_line=1,
                          end_line=1, content="x", commit_sha=C1, start_byte=existing.start_byte))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_only_one_active_job_per_repository(session):
    repo_id = await make_repo(session)
    session.add(IndexingJob(repository_id=repo_id, status="succeeded"))
    session.add(IndexingJob(repository_id=repo_id, status="queued"))
    await session.commit()
    session.add(IndexingJob(repository_id=repo_id, status="running"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_delete_repository_index_removes_everything(session):
    repo_id = await make_repo(session)
    data = snapshot_v1(repo_id)
    await save_embeddings(session, repo_id, "m", EMBEDDING_DIMENSIONS, [(data[0].embedding_keys[0], unit(1))])
    await apply_snapshot(session, repo_id, C1, data, [])
    session.add(IndexingJob(repository_id=repo_id, status="succeeded"))
    await session.commit()
    await delete_repository_index(session, repo_id)
    await session.commit()
    for model in (IndexedFile, CodeChunk, CodeSymbol, Dependency, ChunkEmbedding, IndexingJob):
        assert await count(session, model, repo_id) == 0


@pytest.mark.asyncio
async def test_embedding_dimension_guard(session):
    await verify_embedding_dimensions(session, EMBEDDING_DIMENSIONS)  # matches (no-op on SQLite)
    if session.get_bind().dialect.name == "postgresql":
        with pytest.raises(EmbeddingDimensionMismatchError):
            await verify_embedding_dimensions(session, EMBEDDING_DIMENSIONS * 2)


# ---------------------------------------------------------------- resolver (pure)

@pytest.mark.parametrize(
    "importer,language,module,imported,expected",
    [
        ("backend/app/api/v1/auth.py", "python", "app.core.config", "settings", "backend/app/core/config.py"),
        ("backend/app/api/v1/auth.py", "python", "app.models", "User", "backend/app/models/__init__.py"),
        ("backend/app/api/v1/api.py", "python", ".auth", "router", "backend/app/api/v1/auth.py"),
        ("backend/app/api/v1/api.py", "python", "..", "v1", None),
        ("backend/app/indexing/parsing/python.py", "python", "..file_filter", "x", "backend/app/indexing/file_filter.py"),
        ("backend/app/x.py", "python", "os", None, None),
        ("frontend/src/pages/A.tsx", "tsx", "../services/api", "api", "frontend/src/services/api.ts"),
        ("frontend/src/pages/A.tsx", "tsx", "./B.js", None, "frontend/src/pages/B.tsx"),
        ("frontend/src/App.tsx", "tsx", "./components", None, "frontend/src/components/index.ts"),
        ("frontend/src/App.tsx", "tsx", "@/services/api", None, "frontend/src/services/api.ts"),
        ("frontend/src/App.tsx", "tsx", "react", "default", None),
        ("frontend/src/App.tsx", "tsx", "../../../outside", None, None),
    ],
)
def test_resolve_import(importer, language, module, imported, expected):
    index = PathIndex([
        "backend/app/core/config.py", "backend/app/models/__init__.py", "backend/app/api/v1/auth.py",
        "backend/app/api/v1/api.py", "backend/app/indexing/file_filter.py",
        "frontend/src/services/api.ts", "frontend/src/pages/B.tsx", "frontend/src/components/index.ts",
        "tools/app/core/config.py",
    ])
    assert resolve_import(importer, language, module, imported, index) == expected
