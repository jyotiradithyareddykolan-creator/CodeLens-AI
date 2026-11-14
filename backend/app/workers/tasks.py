"""Celery tasks for repository indexing.

Each task run opens its own async engine (NullPool) inside ``asyncio.run`` so no
connection outlives the event loop that created it.
"""

import asyncio
import logging
import uuid
from typing import Optional

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.workers.celery_app import (
    INCREMENTAL_REINDEX_TASK_NAME,
    INDEX_REPOSITORY_TASK_NAME,
    celery_app,
)

logger = logging.getLogger(__name__)

# Push events that arrive while a job is active are retried this often.
PUSH_RETRY_SECONDS = 60
PUSH_MAX_RETRIES = 30


def _session_factory():
    engine = create_async_engine(settings.ASYNC_DATABASE_URL, poolclass=NullPool)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def run_index_job(job_id: str, session_factory=None, github=None, embedder_factory=None):
    """Run one indexing job. Dependencies are injectable for tests."""
    from app.indexing.pipeline import IndexingPipeline, default_embedder
    from app.services.github_service import github_service

    engine = None
    if session_factory is None:
        engine, session_factory = _session_factory()
    try:
        pipeline = IndexingPipeline(session_factory, github or github_service, embedder_factory or default_embedder)
        return await pipeline.run(uuid.UUID(job_id))
    finally:
        if engine is not None:
            await engine.dispose()


async def _soft_timeout(job_id: str, session_factory=None) -> None:
    """Re-queue a job interrupted by the soft time limit (embeddings are cached)."""
    from sqlalchemy import update

    from app.indexing.jobs import utcnow
    from app.models import IndexingJob

    engine = None
    if session_factory is None:
        engine, session_factory = _session_factory()
    try:
        async with session_factory() as s:
            await s.execute(update(IndexingJob).where(IndexingJob.id == uuid.UUID(job_id),
                                                      IndexingJob.status == "running")
                            .values(status="queued", phase="waiting_retry",
                                    error_message="Retrying: task time limit reached", updated_at=utcnow()))
            await s.commit()
    finally:
        if engine is not None:
            await engine.dispose()


@celery_app.task(bind=True, name=INDEX_REPOSITORY_TASK_NAME, acks_late=True,
                 max_retries=settings.INDEX_MAX_ATTEMPTS + 1)
def index_repository(self, job_id: str) -> dict:
    """Index one repository at a pinned commit for the given IndexingJob."""
    try:
        outcome = asyncio.run(run_index_job(job_id))
    except SoftTimeLimitExceeded:
        asyncio.run(_soft_timeout(job_id))
        raise self.retry(countdown=30)
    if outcome.status == "retry":
        raise self.retry(countdown=int(outcome.retry_after or 60))
    return {"job_id": job_id, "status": outcome.status, "message": outcome.message}


async def request_push_reindex(repository_id: str, commit_sha: Optional[str], session_factory=None) -> dict:
    """Create a push-triggered job pinned to ``commit_sha`` unless one is active."""
    from app.indexing.jobs import active_job, request_indexing
    from app.models import Repository

    engine = None
    if session_factory is None:
        engine, session_factory = _session_factory()
    try:
        async with session_factory() as s:
            repo = await s.get(Repository, uuid.UUID(repository_id))
            if repo is None:
                return {"status": "skipped", "reason": "repository_not_found"}
            if commit_sha and repo.last_indexed_commit == commit_sha:
                return {"status": "skipped", "reason": "already_indexed"}
            current = await active_job(s, repo.id)
            if current is not None and commit_sha and current.commit_sha == commit_sha:
                return {"status": "skipped", "reason": "already_queued", "job_id": str(current.id)}
            job, created = await request_indexing(s, repo, trigger="push", commit_sha=commit_sha)
            if not created:
                return {"status": "busy", "job_id": str(job.id)}
            return {"status": "created", "job_id": str(job.id)}
    finally:
        if engine is not None:
            await engine.dispose()


@celery_app.task(bind=True, name=INCREMENTAL_REINDEX_TASK_NAME, max_retries=PUSH_MAX_RETRIES)
def incremental_reindex(self, repository_id: str, commit_sha: Optional[str] = None) -> dict:
    """Push webhook: re-index only files whose blobs changed since the last snapshot."""
    result = asyncio.run(request_push_reindex(repository_id, commit_sha))
    if result["status"] == "busy":
        # Another job is running; index this commit once it finishes.
        raise self.retry(countdown=PUSH_RETRY_SECONDS)
    if result["status"] == "created":
        index_repository.delay(result["job_id"])
    return result
