"""Indexing job lifecycle: creation, de-duplication, stale detection and dispatch.

At most one job per repository can be ``queued`` or ``running`` (enforced by a
partial unique index). A job whose worker stopped reporting progress for
``INDEX_STALE_JOB_MINUTES`` is marked failed so a new job can start.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import IndexingJob, Repository

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running")


class IndexingDispatchError(Exception):
    """The job could not be handed to the task queue."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes
    return value


def is_stale(job: IndexingJob, now: Optional[datetime] = None) -> bool:
    now = now or utcnow()
    last = _aware(job.heartbeat_at) or _aware(job.updated_at) or _aware(job.created_at)
    if job.status == "queued" and job.next_retry_at is not None:
        last = max(last, _aware(job.next_retry_at)) if last else _aware(job.next_retry_at)
    return last is not None and now - last > timedelta(minutes=settings.INDEX_STALE_JOB_MINUTES)


async def active_job(db: AsyncSession, repository_id: uuid.UUID) -> Optional[IndexingJob]:
    return (await db.execute(
        select(IndexingJob).where(IndexingJob.repository_id == repository_id,
                                  IndexingJob.status.in_(ACTIVE_STATUSES))
    )).scalar_one_or_none()


async def latest_job(db: AsyncSession, repository_id: uuid.UUID) -> Optional[IndexingJob]:
    return (await db.execute(
        select(IndexingJob).where(IndexingJob.repository_id == repository_id)
        .order_by(IndexingJob.created_at.desc()).limit(1)
    )).scalar_one_or_none()


async def request_indexing(
    db: AsyncSession,
    repo: Repository,
    *,
    trigger: str = "manual",
    force_full: bool = False,
    requested_ref: Optional[str] = None,
    commit_sha: Optional[str] = None,
) -> Tuple[IndexingJob, bool]:
    """Create a queued job, or return the existing active one. Commits.

    Returns ``(job, created)``.
    """
    await db.refresh(repo)  # base commit / status must reflect other sessions' updates
    existing = await active_job(db, repo.id)
    if existing is not None:
        if not is_stale(existing):
            return existing, False
        existing.status = "failed"
        existing.error_message = "Indexing worker stopped responding; the job was abandoned."
        existing.finished_at = utcnow()
        await db.flush()

    job = IndexingJob(
        repository_id=repo.id, status="queued", trigger=trigger, force_full=force_full,
        requested_ref=requested_ref, commit_sha=commit_sha, base_commit_sha=repo.last_indexed_commit,
    )
    db.add(job)
    if repo.indexing_status in ("unindexed", "failed") or not repo.is_indexed:
        repo.indexing_status = "pending"
    try:
        await db.commit()
    except IntegrityError:
        # Another request created an active job concurrently.
        await db.rollback()
        existing = await active_job(db, repo.id)
        if existing is None:
            raise
        return existing, False
    await db.refresh(job)
    return job, True


def dispatch_index_job(job_id: uuid.UUID) -> str:
    """Send the job to Celery. Returns the Celery task ID."""
    from app.workers.celery_app import INDEX_REPOSITORY_TASK_NAME, celery_app

    try:
        result = celery_app.send_task(INDEX_REPOSITORY_TASK_NAME, args=[str(job_id)])
    except Exception as exc:  # broker unreachable, misconfiguration
        logger.error("Could not dispatch indexing job %s: %s", job_id, type(exc).__name__)
        raise IndexingDispatchError("The indexing queue is unavailable. Is Redis running?") from None
    return result.id


async def mark_dispatch_failed(db: AsyncSession, job: IndexingJob, message: str) -> None:
    job.status = "failed"
    job.error_message = message
    job.finished_at = utcnow()
    await db.commit()
