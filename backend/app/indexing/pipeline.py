"""End-to-end repository indexing (Phase 3, Milestone 6).

``IndexingPipeline.run(job_id)`` executes one queued job:

1. claim the job atomically (queued -> running); otherwise exit (idempotent)
2. refresh repository metadata (default branch) and pin the commit SHA
3. discover eligible files at that commit (path filters, size limits)
4. fetch only new/changed blobs; screen content and secrets; parse; chunk
5. embed chunk texts not already stored (unchanged files are backfilled when the
   model changed or embeddings were skipped before); embeddings are saved after
   every batch so a retry never pays twice
6. apply the new snapshot and the job result in ONE transaction

Rate limits and transient failures re-queue the job (keeping its slot) and return a
retry delay for the Celery task; after ``INDEX_MAX_ATTEMPTS`` the job fails. Errors
stored on the job are short, sanitized messages.
"""

import asyncio
import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.indexing.chunking import chunk_file
from app.indexing.discovery import DiscoveryError, discover_files, load_file
from app.indexing.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingError,
    EmbeddingRateLimitError,
    EmbeddingInput,
    EmbeddingUnavailableError,
    GeminiEmbedder,
    chunk_input,
    document_input,
)
from app.indexing.file_filter import SkipReason
from app.indexing.jobs import utcnow
from app.indexing.parsing import parse_file
from app.indexing.store import (
    EmbeddingDimensionMismatchError,
    FileIndexData,
    RepositoryGoneError,
    SkippedFile,
    apply_snapshot,
    existing_embedding_keys,
    load_file_hashes,
    save_embeddings,
    verify_embedding_dimensions,
)
from app.models import CodeChunk, GitHubInstallation, IndexedFile, IndexingJob, Repository
from app.services.github_service import (
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubService,
    GitHubServiceError,
)

logger = logging.getLogger(__name__)

PROGRESS_DISCOVERY = 5.0
PROGRESS_FILES_END = 60.0
PROGRESS_EMBED_END = 95.0


class _Retryable(Exception):
    def __init__(self, message: str, delay: float):
        super().__init__(message)
        self.delay = delay


class _Fatal(Exception):
    pass


@dataclass
class JobOutcome:
    status: str  # succeeded | failed | retry | skipped
    retry_after: Optional[float] = None
    message: Optional[str] = None


class IndexingPipeline:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        github: GitHubService,
        embedder_factory: Callable[[], Optional[GeminiEmbedder]],
    ):
        self.sessions = session_factory
        self.github = github
        self.embedder_factory = embedder_factory

    # ---------------------------------------------------------------- job state

    async def _claim(self, job_id: uuid.UUID) -> bool:
        async with self.sessions() as s:
            now = utcnow()
            result = await s.execute(
                update(IndexingJob)
                .where(IndexingJob.id == job_id, IndexingJob.status == "queued")
                .values(status="running", attempts=IndexingJob.attempts + 1, heartbeat_at=now,
                        started_at=func.coalesce(IndexingJob.started_at, now),
                        next_retry_at=None, error_message=None, phase="starting", updated_at=now)
            )
            await s.commit()
            return result.rowcount == 1

    async def _update_job(self, job_id: uuid.UUID, **values) -> None:
        values.setdefault("heartbeat_at", utcnow())
        values["updated_at"] = utcnow()
        async with self.sessions() as s:
            await s.execute(update(IndexingJob).where(IndexingJob.id == job_id).values(**values))
            await s.commit()

    async def _set_repo_status(self, repository_id: uuid.UUID, status: str) -> None:
        async with self.sessions() as s:
            await s.execute(update(Repository).where(Repository.id == repository_id).values(indexing_status=status))
            await s.commit()

    # ---------------------------------------------------------------- entry point

    async def run(self, job_id: uuid.UUID) -> JobOutcome:
        job_id = uuid.UUID(str(job_id))
        if not await self._claim(job_id):
            logger.info("Indexing job %s is not queued (already running or finished); skipping", job_id)
            return JobOutcome("skipped")

        async with self.sessions() as s:
            job = await s.get(IndexingJob, job_id)
            repo = await s.get(Repository, job.repository_id) if job else None
            installation = await s.get(GitHubInstallation, repo.installation_id) if repo else None
            if job is None or repo is None or installation is None:
                return JobOutcome("skipped", message="Repository no longer exists")
            context = {
                "repo_id": repo.id, "owner": repo.owner, "name": repo.name,
                "installation_id": installation.installation_id, "default_branch": repo.default_branch,
                "was_indexed": repo.is_indexed, "attempts": job.attempts, "force_full": job.force_full,
                "requested_ref": job.requested_ref, "commit_sha": job.commit_sha,
            }
        await self._set_repo_status(context["repo_id"], "indexing")

        try:
            stats = await self._index(job_id, context)
        except _Retryable as exc:
            return await self._retry_or_fail(job_id, context, str(exc), exc.delay)
        except RepositoryGoneError:
            logger.info("Repository of job %s was disconnected during indexing", job_id)
            return JobOutcome("skipped", message="Repository no longer exists")
        except _Fatal as exc:
            return await self._fail(job_id, context, str(exc))
        except Exception as exc:  # unexpected: record a safe message, keep details in logs
            logger.exception("Indexing job %s failed unexpectedly", job_id)
            return await self._fail(job_id, context, f"Unexpected error ({type(exc).__name__}). See worker logs.")
        logger.info("Indexing job %s succeeded: %s", job_id, stats)
        return JobOutcome("succeeded")

    async def _retry_or_fail(self, job_id, context, message: str, delay: float) -> JobOutcome:
        if context["attempts"] >= settings.INDEX_MAX_ATTEMPTS:
            return await self._fail(job_id, context, f"{message} (gave up after {context['attempts']} attempts)")
        delay = max(5.0, min(delay, 3600.0))
        await self._update_job(job_id, status="queued", phase="waiting_retry",
                               error_message=f"Retrying: {message}",
                               next_retry_at=utcnow() + timedelta(seconds=delay))
        await self._set_repo_status(context["repo_id"], "indexed" if context["was_indexed"] else "pending")
        logger.warning("Indexing job %s will retry in %.0fs: %s", job_id, delay, message)
        return JobOutcome("retry", retry_after=delay, message=message)

    async def _fail(self, job_id, context, message: str) -> JobOutcome:
        await self._update_job(job_id, status="failed", phase="failed", error_message=message[:2000],
                               finished_at=utcnow())
        # A previously successful snapshot stays queryable.
        await self._set_repo_status(context["repo_id"], "indexed" if context["was_indexed"] else "failed")
        return JobOutcome("failed", message=message)

    # ---------------------------------------------------------------- stages

    async def _index(self, job_id: uuid.UUID, ctx: dict) -> dict:
        inst, owner, name = ctx["installation_id"], ctx["owner"], ctx["name"]
        embedder = self.embedder_factory()

        if embedder is not None:
            async with self.sessions() as s:
                try:
                    await verify_embedding_dimensions(s, embedder.dimensions)
                except EmbeddingDimensionMismatchError as exc:
                    raise _Fatal(str(exc))

        # 1. Pin the commit.
        await self._update_job(job_id, phase="resolving", progress=1.0)
        commit_sha = ctx["commit_sha"]
        try:
            if not commit_sha:
                meta = await self.github.get_repository_metadata(inst, owner, name)
                branch = ctx["requested_ref"] or meta.get("default_branch") or ctx["default_branch"]
                await self._refresh_repository(ctx["repo_id"], meta)
                commit_sha = await self.github.resolve_commit_sha(inst, owner, name, branch)
            # 2. Discover files at that commit.
            await self._update_job(job_id, phase="discovering", commit_sha=commit_sha)
            discovery = await discover_files(self.github, inst, owner, name, commit_sha)
        except GitHubRateLimitError as exc:
            raise _Retryable(str(exc), exc.retry_after_seconds or 60.0)
        except GitHubNotFoundError as exc:
            raise _Fatal(f"GitHub could not find the repository or commit: {exc}")
        except GitHubServiceError as exc:
            if exc.status_code is None or exc.status_code >= 500:
                raise _Retryable(str(exc), 30.0 * ctx["attempts"])
            raise _Fatal(f"GitHub request failed: {exc}")
        except DiscoveryError as exc:
            raise _Fatal(str(exc))

        async with self.sessions() as s:
            existing = await load_file_hashes(s, ctx["repo_id"])
        current = {f.path: f for f in discovery.files}
        if ctx["force_full"]:
            changed = list(discovery.files)
        else:
            changed = [f for f in discovery.files if existing.get(f.path) != f.blob_sha]
        unchanged_paths = {p for p in current if p in existing} - {f.path for f in changed}
        skipped = Counter(discovery.skipped)
        await self._update_job(job_id, phase="processing", progress=PROGRESS_DISCOVERY,
                               files_total=len(discovery.files), files_changed=len(changed))

        # 3. Fetch, screen, parse and chunk changed files.
        upserts, excluded, failed_paths = await self._process_files(job_id, ctx, commit_sha, changed, skipped)
        failed = len(failed_paths)
        # A temporary download failure must not drop a file's previous index entry.
        kept = unchanged_paths | {p for p in failed_paths if p in existing}
        deleted = sorted(set(existing) - kept - {d.path for d in upserts} - {e.path for e in excluded})

        # 4. Embeddings (new chunks + backfill for unchanged files).
        await self._update_job(job_id, phase="embedding", progress=PROGRESS_FILES_END)
        embed_stats = await self._embed(job_id, ctx, embedder, upserts, unchanged_paths)

        # 5. Apply snapshot + job result atomically.
        await self._update_job(job_id, phase="saving", progress=PROGRESS_EMBED_END)
        stats = {
            "skipped_by_reason": dict(skipped),
            "truncated_tree": discovery.used_truncation_fallback,
            "tree_requests": discovery.tree_requests,
            **embed_stats["stats"],
        }
        async with self.sessions() as s:
            async with s.begin():
                # Backfilled keys first: apply_snapshot removes embeddings no chunk references.
                if embed_stats["backfill"]:
                    await s.execute(update(CodeChunk), [
                        {"id": chunk_id, "embedding_key": key} for chunk_id, key in embed_stats["backfill"].items()
                    ])
                result = await apply_snapshot(s, ctx["repo_id"], commit_sha, upserts, deleted, excluded)
                now = utcnow()
                await s.execute(update(IndexingJob).where(IndexingJob.id == job_id).values(
                    status="succeeded", phase="done", progress=100.0, commit_sha=commit_sha,
                    files_indexed=result.files_total, files_skipped=sum(skipped.values()),
                    files_failed=failed, files_deleted=result.files_deleted,
                    chunks_total=result.chunks_total, embeddings_created=embed_stats["created"],
                    embeddings_reused=embed_stats["reused"], stats=stats, error_message=None,
                    finished_at=now, heartbeat_at=now, updated_at=now,
                ))
        return stats

    async def _refresh_repository(self, repository_id: uuid.UUID, meta: dict) -> None:
        values = {}
        if isinstance(meta.get("default_branch"), str) and meta["default_branch"]:
            values["default_branch"] = meta["default_branch"][:255]
        if isinstance(meta.get("private"), bool):
            values["is_private"] = meta["private"]
        if values:
            async with self.sessions() as s:
                await s.execute(update(Repository).where(Repository.id == repository_id).values(**values))
                await s.commit()

    async def _process_files(self, job_id, ctx, commit_sha, changed, skipped: Counter
                             ) -> Tuple[List[FileIndexData], List[SkippedFile], List[str]]:
        semaphore = asyncio.Semaphore(max(1, settings.INDEX_FETCH_CONCURRENCY))
        results: List[Optional[FileIndexData]] = [None] * len(changed)
        excluded: List[SkippedFile] = []
        failed: List[str] = []
        done = 0

        async def work(i, discovered):
            async with semaphore:
                loaded = await load_file(self.github, ctx["installation_id"], ctx["owner"], ctx["name"], discovered)
            if not loaded.included:
                return i, None, loaded.skip_reason
            parsed = parse_file(discovered.path, discovered.language, loaded.text)
            chunks = chunk_file(parsed, repository_id=ctx["repo_id"], commit_sha=commit_sha)
            return i, FileIndexData(
                path=discovered.path, blob_sha=discovered.blob_sha, language=discovered.language,
                size_bytes=discovered.size, parse=parsed.result, chunks=chunks,
                embedding_keys=[None] * len(chunks),
            ), None

        tasks = [asyncio.create_task(work(i, f)) for i, f in enumerate(changed)]
        try:
            for finished in asyncio.as_completed(tasks):
                i, data, reason = await finished
                if data is not None:
                    results[i] = data
                elif reason == SkipReason.FETCH_FAILED:
                    failed.append(changed[i].path)
                else:
                    skipped[reason.value] += 1
                    f = changed[i]
                    excluded.append(SkippedFile(path=f.path, blob_sha=f.blob_sha, language=f.language,
                                                size_bytes=f.size, reason=reason.value))
                done += 1
                if done % 25 == 0 or done == len(changed):
                    progress = PROGRESS_DISCOVERY + (PROGRESS_FILES_END - PROGRESS_DISCOVERY) * done / max(1, len(changed))
                    await self._update_job(job_id, files_processed=done, progress=round(progress, 1))
        except GitHubRateLimitError as exc:
            raise _Retryable(str(exc), exc.retry_after_seconds or 60.0)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
        return [r for r in results if r is not None], excluded, failed

    async def _embed(self, job_id, ctx, embedder: Optional[GeminiEmbedder],
                     upserts: Sequence[FileIndexData], unchanged_paths) -> dict:
        if embedder is None:
            return {"created": 0, "reused": 0, "backfill": {},
                    "stats": {"embeddings": "skipped: GEMINI_API_KEY is not configured"}}

        model, dims = embedder.model, embedder.dimensions
        inputs: Dict[str, EmbeddingInput] = {}
        for data in upserts:
            keys = []
            for chunk in data.chunks:
                item = chunk_input(chunk, model=model)
                key = item.key(model, dims)
                inputs.setdefault(key, item)
                keys.append(key)
            data.embedding_keys = keys

        backfill: Dict[uuid.UUID, str] = {}
        if unchanged_paths:
            async with self.sessions() as s:
                rows = (await s.execute(
                    select(CodeChunk.id, CodeChunk.content, CodeChunk.context, CodeChunk.qualified_name,
                           CodeChunk.embedding_key, IndexedFile.file_path)
                    .join(IndexedFile, IndexedFile.id == CodeChunk.file_id)
                    .where(CodeChunk.repository_id == ctx["repo_id"])
                )).all()
            for chunk_id, content, context, qualified, stored_key, path in rows:
                if path not in unchanged_paths:
                    continue
                title = f"{path} :: {qualified}" if qualified else path
                item = document_input(content, title=title, context=context, model=model)
                key = item.key(model, dims)
                if key != stored_key:
                    backfill[chunk_id] = key
                inputs.setdefault(key, item)

        async with self.sessions() as s:
            present = await existing_embedding_keys(s, ctx["repo_id"], inputs.keys())
        missing = [inputs[k] for k in inputs if k not in present]
        created = 0

        async def persist(batch, vectors):
            nonlocal created
            async with self.sessions() as s:
                await save_embeddings(s, ctx["repo_id"], model, dims,
                                      [(item.key(model, dims), v) for item, v in zip(batch, vectors)])
                await s.commit()
            created += len(batch)
            progress = PROGRESS_FILES_END + (PROGRESS_EMBED_END - PROGRESS_FILES_END) * created / max(1, len(missing))
            await self._update_job(job_id, embeddings_created=created, progress=round(progress, 1))

        try:
            await embedder.embed(missing, on_batch=persist)
        except EmbeddingRateLimitError as exc:
            raise _Retryable(str(exc), exc.retry_after_seconds or 60.0)
        except EmbeddingUnavailableError as exc:
            raise _Retryable(str(exc), 60.0 * ctx["attempts"])
        except EmbeddingConfigurationError as exc:
            raise _Fatal(str(exc))
        except EmbeddingError as exc:
            raise _Fatal(f"Embedding failed: {exc}")

        calibration = embedder.stats.calibration
        return {
            "created": created,
            "reused": len(inputs) - len(missing),
            "backfill": backfill,
            "stats": {
                "embedding_model": model,
                "embedding_dimensions": dims,
                "embedding_requests": embedder.stats.requests,
                "token_estimate_ratio": calibration.ratio,
                "api_prompt_tokens": calibration.actual or None,
            },
        }


def default_embedder() -> Optional[GeminiEmbedder]:
    """Embeddings are skipped (not failed) when no API key is configured."""
    return GeminiEmbedder() if settings.GEMINI_API_KEY else None
