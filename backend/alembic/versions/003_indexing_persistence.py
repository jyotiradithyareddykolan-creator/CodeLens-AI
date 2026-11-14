"""Indexing persistence: chunk/symbol/dependency metadata, embeddings and jobs.

* indexed_files: commit_sha, line_count, has_parse_errors
* code_chunks: byte offsets, qualified/parent names, content hash, embedding key,
  token count, context, part index/count, unique (file_id, start_byte).
  The unused ``embedding`` column is replaced by the chunk_embeddings table (no
  indexing ever wrote to it before this revision).
* code_symbols: repository_id, qualified/parent names, byte offsets, flags,
  decorators, bases
* dependencies: imported_name, line, target_path
* chunk_embeddings: one vector per (repository, embedding key), HNSW cosine index on
  PostgreSQL when dimensions <= 2000
* indexing_jobs: job tracking with at most one active job per repository

The embedding dimension is read from GEMINI_EMBEDDING_DIMENSIONS when this revision
runs. Changing the dimension later requires a new migration and re-indexing.

Revision ID: 003_indexing_persistence
Revises: 002_reconcile_indexes
Create Date: 2026-11-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

try:
    from pgvector.sqlalchemy import Vector
    HAS_PGVECTOR = True
except ImportError:  # pragma: no cover
    HAS_PGVECTOR = False

revision: str = "003_indexing_persistence"
down_revision: Union[str, None] = "002_reconcile_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID_TYPE = sa.CHAR(36).with_variant(postgresql.UUID(as_uuid=True), "postgresql")
DIMS = settings.GEMINI_EMBEDDING_DIMENSIONS
HNSW_MAX_DIMENSIONS = 2000
ACTIVE_JOB = sa.text("status IN ('queued', 'running')")


def _vector(dims: int):
    if op.get_bind().dialect.name == "postgresql" and HAS_PGVECTOR:
        return Vector(dims)
    return sa.JSON()


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    false = sa.text("false")

    with op.batch_alter_table("indexed_files") as batch:
        batch.add_column(sa.Column("commit_sha", sa.String(100), nullable=True))
        batch.add_column(sa.Column("line_count", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("has_parse_errors", sa.Boolean(), server_default=false, nullable=False))

    with op.batch_alter_table("code_chunks") as batch:
        batch.drop_column("embedding")
        batch.add_column(sa.Column("qualified_name", sa.String(512), nullable=True))
        batch.add_column(sa.Column("parent_symbol", sa.String(512), nullable=True))
        batch.add_column(sa.Column("start_byte", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("end_byte", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("content_hash", sa.String(64), server_default="", nullable=False))
        batch.add_column(sa.Column("embedding_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("token_count", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("context", sa.Text(), nullable=True))
        batch.add_column(sa.Column("part_index", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("part_count", sa.Integer(), server_default="1", nullable=False))
    op.create_index("ix_code_chunks_embedding_key", "code_chunks", ["embedding_key"])
    op.create_index("uq_code_chunks_file_start", "code_chunks", ["file_id", "start_byte"], unique=True)

    with op.batch_alter_table("code_symbols") as batch:
        batch.add_column(sa.Column("repository_id", UUID_TYPE, nullable=True))
        batch.add_column(sa.Column("qualified_name", sa.String(512), nullable=True))
        batch.add_column(sa.Column("parent_name", sa.String(512), nullable=True))
        batch.add_column(sa.Column("start_byte", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("end_byte", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("is_exported", sa.Boolean(), server_default=false, nullable=False))
        batch.add_column(sa.Column("is_async", sa.Boolean(), server_default=false, nullable=False))
        batch.add_column(sa.Column("decorators", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("bases", sa.JSON(), nullable=True))
        batch.create_foreign_key(
            "fk_code_symbols_repository_id", "repositories", ["repository_id"], ["id"], ondelete="CASCADE"
        )
    # Backfill repository_id for any pre-existing symbols from their file.
    op.execute(
        "UPDATE code_symbols SET repository_id = "
        "(SELECT f.repository_id FROM indexed_files f WHERE f.id = code_symbols.file_id) "
        "WHERE repository_id IS NULL"
    )
    op.create_index("ix_code_symbols_repository_id", "code_symbols", ["repository_id"])
    op.create_index("ix_code_symbols_qualified_name", "code_symbols", ["qualified_name"])

    with op.batch_alter_table("dependencies") as batch:
        batch.add_column(sa.Column("imported_name", sa.String(255), nullable=True))
        batch.add_column(sa.Column("line", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("target_path", sa.String(1024), nullable=True))

    op.create_table(
        "chunk_embeddings",
        sa.Column("id", UUID_TYPE, primary_key=True),
        sa.Column("repository_id", UUID_TYPE, sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("embedding_key", sa.String(64), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", _vector(DIMS), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_chunk_embeddings_repository_id", "chunk_embeddings", ["repository_id"])
    op.create_index("uq_chunk_embeddings_repo_key", "chunk_embeddings", ["repository_id", "embedding_key"], unique=True)
    if DIMS <= HNSW_MAX_DIMENSIONS:
        op.create_index(
            "ix_chunk_embeddings_hnsw", "chunk_embeddings", ["embedding"],
            postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
        )

    op.create_table(
        "indexing_jobs",
        sa.Column("id", UUID_TYPE, primary_key=True),
        sa.Column("repository_id", UUID_TYPE, sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("requested_ref", sa.String(255), nullable=True),
        sa.Column("commit_sha", sa.String(100), nullable=True),
        sa.Column("base_commit_sha", sa.String(100), nullable=True),
        sa.Column("force_full", sa.Boolean(), nullable=False),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("phase", sa.String(30), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        *[sa.Column(name, sa.Integer(), nullable=False) for name in (
            "files_total", "files_changed", "files_processed", "files_indexed", "files_skipped",
            "files_failed", "files_deleted", "chunks_total", "embeddings_created", "embeddings_reused",
        )],
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_indexing_jobs_repository_id", "indexing_jobs", ["repository_id"])
    op.create_index("ix_indexing_jobs_status", "indexing_jobs", ["status"])
    op.create_index("ix_indexing_jobs_repo_created", "indexing_jobs", ["repository_id", "created_at"])
    op.create_index(
        "uq_indexing_jobs_active_repo", "indexing_jobs", ["repository_id"], unique=True,
        postgresql_where=ACTIVE_JOB, sqlite_where=ACTIVE_JOB,
    )


def downgrade() -> None:
    op.drop_table("indexing_jobs")
    op.drop_table("chunk_embeddings")

    with op.batch_alter_table("dependencies") as batch:
        for name in ("target_path", "line", "imported_name"):
            batch.drop_column(name)

    op.drop_index("ix_code_symbols_qualified_name", table_name="code_symbols")
    op.drop_index("ix_code_symbols_repository_id", table_name="code_symbols")
    with op.batch_alter_table("code_symbols") as batch:
        batch.drop_constraint("fk_code_symbols_repository_id", type_="foreignkey")
        for name in ("bases", "decorators", "is_async", "is_exported", "end_byte", "start_byte",
                     "parent_name", "qualified_name", "repository_id"):
            batch.drop_column(name)

    op.drop_index("uq_code_chunks_file_start", table_name="code_chunks")
    op.drop_index("ix_code_chunks_embedding_key", table_name="code_chunks")
    with op.batch_alter_table("code_chunks") as batch:
        for name in ("part_count", "part_index", "context", "token_count", "embedding_key",
                     "content_hash", "end_byte", "start_byte", "parent_symbol", "qualified_name"):
            batch.drop_column(name)
        batch.add_column(sa.Column("embedding", _vector(768), nullable=True))

    with op.batch_alter_table("indexed_files") as batch:
        for name in ("has_parse_errors", "line_count", "commit_sha"):
            batch.drop_column(name)
