import uuid
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.database.session import Base
from app.models.base import GUID, AdaptiveVector, TimestampMixin

# pgvector HNSW indexes support at most 2000 dimensions.
HNSW_MAX_DIMENSIONS = 2000
EMBEDDING_DIMENSIONS = settings.GEMINI_EMBEDDING_DIMENSIONS


class IndexedFile(Base, TimestampMixin):
    __tablename__ = "indexed_files"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(1024), index=True, nullable=False)
    language: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Git blob SHA of the indexed content (content-addressed: unchanged blob = unchanged file).
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    commit_sha: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    has_parse_errors: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    # Set when the file was excluded after content screening (secret detected, binary,
    # generated, ...). Excluded files have no chunks/symbols; the row remembers the
    # blob so unchanged excluded files are not downloaded again.
    skip_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Relationships
    repository: Mapped["Repository"] = relationship("Repository", back_populates="indexed_files")
    chunks: Mapped[List["CodeChunk"]] = relationship("CodeChunk", back_populates="file", cascade="all, delete-orphan")
    symbols: Mapped[List["CodeSymbol"]] = relationship("CodeSymbol", back_populates="file", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_indexed_files_repo_path", "repository_id", "file_path", unique=True),
    )


class CodeChunk(Base, TimestampMixin):
    __tablename__ = "code_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("indexed_files.id", ondelete="CASCADE"), index=True, nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol_name: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    # Chunk type: a symbol kind (function, method, class, interface, type_alias, enum,
    # namespace) or "imports" / "module".
    symbol_type: Mapped[str] = mapped_column(String(50), default="chunk", index=True, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    qualified_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    parent_symbol: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    start_byte: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    end_byte: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)
    # Key into chunk_embeddings (sha256 of model, dimensions and embedded text).
    embedding_key: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    part_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    part_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)

    # Relationships
    file: Mapped["IndexedFile"] = relationship("IndexedFile", back_populates="chunks")
    repository: Mapped["Repository"] = relationship("Repository", back_populates="code_chunks")

    __table_args__ = (
        Index("ix_code_chunks_repo_symbol", "repository_id", "symbol_name"),
        Index("uq_code_chunks_file_start", "file_id", "start_byte", unique=True),
    )


class CodeSymbol(Base, TimestampMixin):
    __tablename__ = "code_symbols"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("indexed_files.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Nullable only so the migration can add it to existing tables; always set by indexing.
    repository_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    symbol_type: Mapped[str] = mapped_column(String(50), nullable=False)  # function, class, method, interface, ...
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    signature: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    docstring: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    qualified_name: Mapped[Optional[str]] = mapped_column(String(512), index=True, nullable=True)
    parent_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    start_byte: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    end_byte: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    is_exported: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    is_async: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    decorators: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    bases: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)

    # Relationships
    file: Mapped["IndexedFile"] = relationship("IndexedFile", back_populates="symbols")

    __table_args__ = (
        Index("ix_code_symbols_file_name", "file_id", "name"),
    )


class Dependency(Base, TimestampMixin):
    __tablename__ = "dependencies"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_symbol_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("code_symbols.id", ondelete="SET NULL"), nullable=True
    )
    target_symbol_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("code_symbols.id", ondelete="SET NULL"), nullable=True
    )
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("indexed_files.id", ondelete="CASCADE"), index=True, nullable=False
    )
    target_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("indexed_files.id", ondelete="SET NULL"), nullable=True
    )
    # For IMPORTS: source_name = local binding, target_name = module specifier.
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    relationship_type: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False
    )  # IMPORTS, CALLS, DEFINES, INHERITS, IMPLEMENTS, USES
    imported_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    line: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Repository path the import resolves to (None for external packages / unresolved).
    target_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    # Relationships
    repository: Mapped["Repository"] = relationship("Repository", back_populates="dependencies")


_embedding_indexes = ()
if EMBEDDING_DIMENSIONS <= HNSW_MAX_DIMENSIONS:
    _embedding_indexes = (
        Index(
            "ix_chunk_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class ChunkEmbedding(Base, TimestampMixin):
    """One embedding vector per distinct embedded text in a repository.

    Saved after every API batch, so interrupted jobs and unchanged chunks never pay for
    the same embedding twice. Rows no longer referenced by any chunk are removed when a
    snapshot is applied.
    """

    __tablename__ = "chunk_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    embedding_key: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[Any] = mapped_column(AdaptiveVector(EMBEDDING_DIMENSIONS), nullable=False)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="chunk_embeddings")

    __table_args__ = (
        Index("uq_chunk_embeddings_repo_key", "repository_id", "embedding_key", unique=True),
        *_embedding_indexes,
    )


class IndexingJob(Base, TimestampMixin):
    """One indexing run of a repository at a pinned commit."""

    __tablename__ = "indexing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # queued, running, succeeded, failed
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True, nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)  # manual, push, retry
    requested_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    commit_sha: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    base_commit_sha: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    force_full: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phase: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    files_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    files_changed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    files_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    files_indexed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    files_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    files_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    files_deleted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunks_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embeddings_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embeddings_reused: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stats: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)  # skip reasons, calibration, ...
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="indexing_jobs")

    __table_args__ = (
        # At most one queued/running job per repository (prevents duplicate concurrent runs).
        Index(
            "uq_indexing_jobs_active_repo",
            "repository_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_indexing_jobs_repo_created", "repository_id", "created_at"),
    )
