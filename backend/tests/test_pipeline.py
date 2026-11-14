"""End-to-end pipeline tests: fake GitHub (in-memory git), mocked Gemini HTTP, real
discovery/parsing/chunking/persistence on the test database."""

import hashlib
import json
import uuid

import httpx
import pytest
from celery.exceptions import Retry
from sqlalchemy import func, select

from app.core.config import settings
from app.indexing.embeddings import GeminiEmbedder
from app.indexing.jobs import request_indexing
from app.indexing.pipeline import IndexingPipeline
from app.models import ChunkEmbedding, CodeChunk, IndexedFile, IndexingJob, Repository
from app.models.indexing import EMBEDDING_DIMENSIONS
from app.services.github_service import GitHubRateLimitError
from app.workers import tasks
from tests.helpers import make_user_with_installation
from tests.test_store import POSTGRES_URL, postgres_schema  # noqa: F401  (module-scoped fixture)

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture(params=["sqlite", "postgresql"])
def backend(request):
    if request.param == "postgresql" and not POSTGRES_URL:
        pytest.skip("CODELENS_TEST_POSTGRES_URL not set")
    return request.param


@pytest_asyncio.fixture
async def pg_factory(backend, postgres_schema):
    """Session factory for the selected backend (overrides conftest for this module)."""
    if backend == "sqlite":
        yield None
        return
    engine = create_async_engine(POSTGRES_URL.replace("+psycopg", "+asyncpg"), poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE dependencies, code_chunks, code_symbols, chunk_embeddings, indexing_jobs, indexed_files, "
            "repository_branches, repositories, github_installations, users CASCADE"
        ))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def session_factory(pg_factory, request):
    if pg_factory is None:
        return request.getfixturevalue("sqlite_session_factory")
    return pg_factory


@pytest_asyncio.fixture
async def db_session(session_factory):
    async with session_factory() as s:
        yield s

FILES_V1 = {
    "src/app/main.py": "from app.util import helper\n\n\ndef main():\n    return helper()\n",
    "src/app/util.py": "def helper():\n    return 42\n\n\nclass Tool:\n    def run(self):\n        return helper()\n",
    "web/index.ts": 'import { x } from "./x";\nexport const run = () => x;\n',
    "web/x.ts": "export const x = 1;\n",
    "README.md": "# docs\n",
    ".env": "SECRET=1\n",
    "node_modules/lib/index.js": "module.exports = 1;\n",
    "src/app/creds.py": 'TOKEN = "' + "".join(["gh", "p_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"]) + '"\n',
}


def git_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


class FakeGitHub:
    """Content-addressed in-memory repository with one commit per snapshot."""

    def __init__(self, commits, branch="main"):
        self.commits = commits  # {commit_sha: {path: content}}
        self.head = list(commits)[-1]
        self.branch = branch
        self.blob_fetches = []
        self.blobs = {}
        self.fail_tree = []  # exceptions raised by get_tree before succeeding
        self.on_tree = None
        for files in commits.values():
            for content in files.values():
                self.blobs[git_sha(content.encode())] = content.encode()

    async def get_repository_metadata(self, inst, owner, repo):
        return {"default_branch": self.branch, "private": True}

    async def resolve_commit_sha(self, inst, owner, repo, ref):
        assert ref == self.branch
        return self.head

    async def get_tree(self, inst, owner, repo, sha, recursive):
        if self.on_tree:
            await self.on_tree()
        if self.fail_tree:
            raise self.fail_tree.pop(0)
        entries = [
            {"path": p, "mode": "100644", "type": "blob", "sha": git_sha(c.encode()), "size": len(c.encode())}
            for p, c in self.commits[sha].items()
        ]
        return {"sha": sha, "tree": entries, "truncated": False}

    async def get_blob(self, inst, owner, repo, sha):
        self.blob_fetches.append(sha)
        return self.blobs[sha]


class FakeGemini:
    def __init__(self, fail_after_batches=None, failure=None):
        self.texts = 0
        self.batches = 0
        self.fail_after_batches = fail_after_batches
        self.failure = failure

    def __call__(self, request):
        if self.fail_after_batches is not None and self.batches >= self.fail_after_batches:
            return self.failure
        self.batches += 1
        body = json.loads(request.content)
        self.texts += len(body["requests"])
        return httpx.Response(200, json={
            "embeddings": [{"values": [float(i + 1) for i in range(EMBEDDING_DIMENSIONS)]} for _ in body["requests"]],
            "usageMetadata": {"promptTokenCount": 10 * len(body["requests"])},
        })


def embedder_for(fake):
    async def no_sleep(_):
        return None

    return lambda: GeminiEmbedder(api_key="k", model="gemini-embedding-2", dimensions=EMBEDDING_DIMENSIONS,
                                  batch_size=2, transport=httpx.MockTransport(fake), sleep=no_sleep)


async def setup_repo(db_session):
    _, installation, _ = await make_user_with_installation(db_session, 77, "owner", 7777)
    repo = Repository(installation_id=installation.id, github_repo_id=555, name="r", owner="o", full_name="o/r")
    db_session.add(repo)
    await db_session.commit()
    return repo


async def queue(db_session, repo, **kw):
    job, created = await request_indexing(db_session, repo, **kw)
    assert created
    return job.id


async def job_row(session_factory, job_id):
    async with session_factory() as s:
        return await s.get(IndexingJob, job_id)


async def count(session_factory, model, **where):
    async with session_factory() as s:
        statement = select(func.count()).select_from(model)
        for key, value in where.items():
            statement = statement.where(getattr(model, key) == value)
        return (await s.execute(statement)).scalar_one()


C1, C2 = "a" * 40, "b" * 40


@pytest.mark.asyncio
async def test_initial_index_without_gemini_key_stores_everything_but_embeddings(db_session, session_factory):
    repo = await setup_repo(db_session)
    github = FakeGitHub({C1: FILES_V1})
    job_id = await queue(db_session, repo)

    outcome = await IndexingPipeline(session_factory, github, lambda: None).run(job_id)
    assert outcome.status == "succeeded"

    job = await job_row(session_factory, job_id)
    assert (job.status, job.progress, job.commit_sha, job.attempts) == ("succeeded", 100.0, C1, 1)
    assert job.started_at is not None and job.finished_at is not None
    assert job.files_total == 5  # main.py, util.py, index.ts, x.ts, creds.py pass path filters
    assert job.files_indexed == 4  # creds.py excluded by the secret scanner
    assert job.stats["skipped_by_reason"] == {
        "unsupported_language": 1, "sensitive_filename": 1, "excluded_directory": 1, "secret_detected": 1,
    }
    assert job.stats["embeddings"].startswith("skipped")
    async with session_factory() as s:
        r = await s.get(Repository, repo.id)
        assert (r.indexing_status, r.last_indexed_commit, r.total_files, r.is_indexed) == ("indexed", C1, 4, True)
        paths = set((await s.execute(select(IndexedFile.file_path).where(IndexedFile.skip_reason.is_(None)))).scalars())
        excluded = dict((await s.execute(select(IndexedFile.file_path, IndexedFile.skip_reason)
                                         .where(IndexedFile.skip_reason.is_not(None)))).all())
        creds_chunks = (await s.execute(select(func.count()).select_from(CodeChunk).join(IndexedFile)
                                        .where(IndexedFile.file_path == "src/app/creds.py"))).scalar_one()
    assert "src/app/creds.py" not in paths and ".env" not in paths
    # Excluded files are remembered (so unchanged ones are not re-downloaded) but never chunked.
    assert excluded == {"src/app/creds.py": "secret_detected"} and creds_chunks == 0
    assert await count(session_factory, CodeChunk, embedding_key=None) == job.chunks_total > 0


@pytest.mark.asyncio
async def test_embeddings_are_created_once_and_reused_on_rerun(db_session, session_factory):
    repo = await setup_repo(db_session)
    github, gemini = FakeGitHub({C1: FILES_V1}), FakeGemini()
    pipeline = IndexingPipeline(session_factory, github, embedder_for(gemini))

    await pipeline.run(await queue(db_session, repo))
    first_texts = gemini.texts
    async with session_factory() as s:
        first_job = (await s.execute(select(IndexingJob).order_by(IndexingJob.created_at))).scalars().first()
    assert first_job.stats["token_estimate_ratio"] is not None  # calibrated from usageMetadata
    chunks = await count(session_factory, CodeChunk)
    assert first_texts > 0
    assert await count(session_factory, ChunkEmbedding) == first_texts
    assert await count(session_factory, CodeChunk, embedding_key=None) == 0

    fetches = len(github.blob_fetches)
    job2 = await queue(db_session, repo)
    await pipeline.run(job2)
    job = await job_row(session_factory, job2)
    assert job.status == "succeeded"
    assert gemini.texts == first_texts  # no new API calls
    assert len(github.blob_fetches) == fetches  # unchanged blobs not downloaded again
    assert (job.files_changed, job.embeddings_created, job.embeddings_reused) == (0, 0, first_texts)
    assert await count(session_factory, CodeChunk) == chunks


@pytest.mark.asyncio
async def test_incremental_reindex_processes_only_changed_files(db_session, session_factory):
    repo = await setup_repo(db_session)
    files_v2 = dict(FILES_V1)
    files_v2["src/app/util.py"] = "def helper():\n    return 43\n"  # changed
    del files_v2["web/x.ts"]  # deleted
    files_v2["src/app/new.py"] = "def added():\n    pass\n"  # added
    github, gemini = FakeGitHub({C1: FILES_V1, C2: files_v2}), FakeGemini()
    pipeline = IndexingPipeline(session_factory, github, embedder_for(gemini))

    github.head = C1
    await pipeline.run(await queue(db_session, repo))
    texts_after_v1, fetches_after_v1 = gemini.texts, len(github.blob_fetches)

    github.head = C2
    job_id = await queue(db_session, repo)
    await pipeline.run(job_id)
    job = await job_row(session_factory, job_id)
    assert job.status == "succeeded" and job.base_commit_sha == C1 and job.commit_sha == C2
    assert job.files_changed == 2 and job.files_deleted == 1
    assert len(github.blob_fetches) - fetches_after_v1 == 2
    assert gemini.texts - texts_after_v1 == job.embeddings_created == 2  # one chunk per changed/new file

    async with session_factory() as s:
        paths = set((await s.execute(select(IndexedFile.file_path))).scalars())
        commits = set((await s.execute(select(CodeChunk.commit_sha))).scalars())
        util = (await s.execute(select(CodeChunk.content).join(IndexedFile).where(
            IndexedFile.file_path == "src/app/util.py"))).scalars().all()
    assert "web/x.ts" not in paths and "src/app/new.py" in paths
    assert commits == {C2}
    assert util == ["def helper():\n    return 43\n"]
    # Embeddings of the old util.py and deleted x.ts were garbage-collected.
    assert await count(session_factory, ChunkEmbedding) == await count(session_factory, CodeChunk)


@pytest.mark.asyncio
async def test_embeddings_are_backfilled_when_a_key_is_added_later(db_session, session_factory):
    repo = await setup_repo(db_session)
    github = FakeGitHub({C1: FILES_V1})
    await IndexingPipeline(session_factory, github, lambda: None).run(await queue(db_session, repo))
    fetches = len(github.blob_fetches)

    gemini = FakeGemini()
    job_id = await queue(db_session, repo)
    await IndexingPipeline(session_factory, github, embedder_for(gemini)).run(job_id)
    job = await job_row(session_factory, job_id)
    assert job.status == "succeeded" and job.files_changed == 0
    assert len(github.blob_fetches) == fetches  # nothing re-downloaded or re-parsed
    assert await count(session_factory, CodeChunk, embedding_key=None) == 0
    assert job.embeddings_created == gemini.texts > 0


@pytest.mark.asyncio
async def test_github_rate_limit_requeues_then_succeeds(db_session, session_factory):
    repo = await setup_repo(db_session)
    github = FakeGitHub({C1: FILES_V1})
    github.fail_tree = [GitHubRateLimitError("limited", retry_after_seconds=90)]
    pipeline = IndexingPipeline(session_factory, github, lambda: None)
    job_id = await queue(db_session, repo)

    outcome = await pipeline.run(job_id)
    assert outcome.status == "retry" and outcome.retry_after == 90
    job = await job_row(session_factory, job_id)
    assert job.status == "queued" and job.next_retry_at is not None
    assert job.error_message.startswith("Retrying:") and job.attempts == 1

    outcome = await pipeline.run(job_id)  # the Celery retry
    job = await job_row(session_factory, job_id)
    assert outcome.status == "succeeded" and job.status == "succeeded" and job.attempts == 2


@pytest.mark.asyncio
async def test_job_fails_after_max_attempts(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "INDEX_MAX_ATTEMPTS", 2)
    repo = await setup_repo(db_session)
    github = FakeGitHub({C1: FILES_V1})
    github.fail_tree = [GitHubRateLimitError("limited", 30)] * 2
    pipeline = IndexingPipeline(session_factory, github, lambda: None)
    job_id = await queue(db_session, repo)
    assert (await pipeline.run(job_id)).status == "retry"
    assert (await pipeline.run(job_id)).status == "failed"
    job = await job_row(session_factory, job_id)
    assert job.status == "failed" and "gave up after 2 attempts" in job.error_message
    async with session_factory() as s:
        assert (await s.get(Repository, repo.id)).indexing_status == "failed"


@pytest.mark.asyncio
async def test_embedding_rate_limit_keeps_finished_batches_for_the_retry(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_MAX_RETRY_WAIT_SECONDS", 10)
    repo = await setup_repo(db_session)
    github = FakeGitHub({C1: FILES_V1})
    quota = httpx.Response(429, json={"error": {"message": "quota", "details": [{"retryDelay": "600s"}]}})
    gemini = FakeGemini(fail_after_batches=1, failure=quota)
    pipeline = IndexingPipeline(session_factory, github, embedder_for(gemini))
    job_id = await queue(db_session, repo)

    outcome = await pipeline.run(job_id)
    assert outcome.status == "retry" and outcome.retry_after == 600
    assert await count(session_factory, ChunkEmbedding) == 2  # first batch persisted
    assert await count(session_factory, CodeChunk) == 0  # snapshot not applied

    gemini.fail_after_batches = None
    texts_before = gemini.texts
    assert (await pipeline.run(job_id)).status == "succeeded"
    total_unique = await count(session_factory, ChunkEmbedding)
    assert gemini.texts - texts_before == total_unique - 2  # cached batch not re-embedded


@pytest.mark.asyncio
async def test_fatal_embedding_error_fails_job_but_keeps_previous_snapshot(db_session, session_factory):
    repo = await setup_repo(db_session)
    github = FakeGitHub({C1: FILES_V1, C2: {**FILES_V1, "web/x.ts": "export const x = 2;\n"}})
    github.head = C1
    await IndexingPipeline(session_factory, github, embedder_for(FakeGemini())).run(await queue(db_session, repo))

    github.head = C2
    bad = FakeGemini(fail_after_batches=0, failure=httpx.Response(400, json={"error": {"message": "bad request"}}))
    job_id = await queue(db_session, repo)
    outcome = await IndexingPipeline(session_factory, github, embedder_for(bad)).run(job_id)
    assert outcome.status == "failed"
    job = await job_row(session_factory, job_id)
    assert "Embedding failed" in job.error_message
    async with session_factory() as s:
        r = await s.get(Repository, repo.id)
        assert (r.last_indexed_commit, r.indexing_status) == (C1, "indexed")
        content = (await s.execute(select(CodeChunk.content).join(IndexedFile).where(
            IndexedFile.file_path == "web/x.ts"))).scalar_one()
    assert content == "export const x = 1;\n"


@pytest.mark.asyncio
async def test_a_file_that_now_contains_a_secret_is_removed_from_the_index(db_session, session_factory):
    repo = await setup_repo(db_session)
    secret = "".join(["AK", "IA", "Z7Q3K9TXW2M4P8RB"])
    github = FakeGitHub({C1: FILES_V1, C2: {**FILES_V1, "web/x.ts": f'export const key = "{secret}";\n'}})
    pipeline = IndexingPipeline(session_factory, github, lambda: None)
    github.head = C1
    await pipeline.run(await queue(db_session, repo))
    github.head = C2
    job_id = await queue(db_session, repo)
    await pipeline.run(job_id)
    async with session_factory() as s:
        paths = set((await s.execute(select(IndexedFile.file_path).where(IndexedFile.skip_reason.is_(None)))).scalars())
        chunks = (await s.execute(select(CodeChunk.content))).scalars().all()
        repo_row = await s.get(Repository, repo.id)
    assert "web/x.ts" not in paths
    assert repo_row.total_files == 3  # main.py, util.py, index.ts
    assert not any(secret in c for c in chunks)
    # creds.py is unchanged and remembered as excluded, so only x.ts was screened this run.
    assert (await job_row(session_factory, job_id)).stats["skipped_by_reason"]["secret_detected"] == 1


@pytest.mark.asyncio
async def test_claim_is_idempotent(db_session, session_factory):
    repo = await setup_repo(db_session)
    github = FakeGitHub({C1: FILES_V1})
    pipeline = IndexingPipeline(session_factory, github, lambda: None)
    job_id = await queue(db_session, repo)
    assert (await pipeline.run(job_id)).status == "succeeded"
    assert (await pipeline.run(job_id)).status == "skipped"  # duplicate delivery of the task
    assert (await pipeline.run(uuid.uuid4())).status == "skipped"  # unknown job


@pytest.mark.asyncio
async def test_repository_disconnected_during_indexing_is_handled(db_session, session_factory):
    from app.indexing.store import delete_repository_index

    repo = await setup_repo(db_session)
    github = FakeGitHub({C1: FILES_V1})

    async def disconnect():
        async with session_factory() as s:
            await delete_repository_index(s, repo.id)
            await s.delete(await s.get(Repository, repo.id))
            await s.commit()

    github.on_tree = disconnect
    job_id = await queue(db_session, repo)
    outcome = await IndexingPipeline(session_factory, github, lambda: None).run(job_id)
    assert outcome.status == "skipped"
    assert await count(session_factory, IndexedFile) == 0


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_active_job_and_stale_jobs_are_replaced(db_session, session_factory, monkeypatch):
    from datetime import timedelta

    from app.indexing.jobs import utcnow

    repo = await setup_repo(db_session)
    first, created = await request_indexing(db_session, repo)
    second, created_again = await request_indexing(db_session, repo)
    assert created and not created_again and first.id == second.id

    first.status = "running"
    first.heartbeat_at = utcnow() - timedelta(minutes=settings.INDEX_STALE_JOB_MINUTES + 5)
    await db_session.commit()
    third, created = await request_indexing(db_session, repo)
    assert created and third.id != first.id
    await db_session.refresh(first)
    assert first.status == "failed" and "stopped responding" in first.error_message


# ------------------------------------------------------------------ Celery wrappers

def test_index_task_raises_celery_retry_with_delay(monkeypatch):
    from app.indexing.pipeline import JobOutcome

    async def fake_run(job_id, *a, **k):
        return JobOutcome("retry", retry_after=42)

    monkeypatch.setattr(tasks, "run_index_job", fake_run)
    with pytest.raises(Retry):
        tasks.index_repository.apply(args=[str(uuid.uuid4())], throw=True)


def test_index_task_returns_result(monkeypatch):
    from app.indexing.pipeline import JobOutcome

    async def fake_run(job_id, *a, **k):
        return JobOutcome("succeeded")

    monkeypatch.setattr(tasks, "run_index_job", fake_run)
    result = tasks.index_repository.apply(args=["abc"]).get()
    assert result == {"job_id": "abc", "status": "succeeded", "message": None}


@pytest.mark.asyncio
async def test_push_reindex_creates_pinned_job_or_reports_busy(db_session, session_factory):
    repo = await setup_repo(db_session)
    first = await tasks.request_push_reindex(str(repo.id), C1, session_factory=session_factory)
    assert first["status"] == "created"
    job = await job_row(session_factory, uuid.UUID(first["job_id"]))
    assert (job.trigger, job.commit_sha) == ("push", C1)
    assert (await tasks.request_push_reindex(str(repo.id), C1, session_factory=session_factory))["status"] == "skipped"
    assert (await tasks.request_push_reindex(str(repo.id), C2, session_factory=session_factory))["status"] == "busy"
    missing = await tasks.request_push_reindex(str(uuid.uuid4()), C2, session_factory=session_factory)
    assert missing["status"] == "skipped"


@pytest.mark.asyncio
async def test_push_job_indexes_the_pinned_commit(db_session, session_factory):
    repo = await setup_repo(db_session)
    github = FakeGitHub({C1: FILES_V1, C2: {**FILES_V1, "web/x.ts": "export const x = 3;\n"}})
    github.head = C2  # branch already moved on; the push job must index C1
    created = await tasks.request_push_reindex(str(repo.id), C1, session_factory=session_factory)
    outcome = await IndexingPipeline(session_factory, github, lambda: None).run(uuid.UUID(created["job_id"]))
    assert outcome.status == "succeeded"
    async with session_factory() as s:
        assert (await s.get(Repository, repo.id)).last_indexed_commit == C1


def test_registered_celery_tasks():
    from app.workers.celery_app import (
        ANALYZE_PULL_REQUEST_TASK_NAME,
        INCREMENTAL_REINDEX_TASK_NAME,
        INDEX_REPOSITORY_TASK_NAME,
        celery_app,
        missing_application_tasks,
    )

    assert {INDEX_REPOSITORY_TASK_NAME, INCREMENTAL_REINDEX_TASK_NAME} <= set(celery_app.tasks)
    assert missing_application_tasks() == {ANALYZE_PULL_REQUEST_TASK_NAME}  # Phase 5


@pytest.mark.asyncio
async def test_unchanged_excluded_files_are_not_downloaded_again(db_session, session_factory):
    repo = await setup_repo(db_session)
    github = FakeGitHub({C1: FILES_V1})
    pipeline = IndexingPipeline(session_factory, github, lambda: None)
    await pipeline.run(await queue(db_session, repo))
    fetched = len(github.blob_fetches)
    job_id = await queue(db_session, repo)
    await pipeline.run(job_id)
    assert len(github.blob_fetches) == fetched
    assert (await job_row(session_factory, job_id)).files_changed == 0


@pytest.mark.asyncio
async def test_temporary_download_failure_keeps_the_previous_version(db_session, session_factory):
    from app.services.github_service import GitHubServiceError

    repo = await setup_repo(db_session)
    v2 = {**FILES_V1, "web/x.ts": "export const x = 5;\n"}
    github = FakeGitHub({C1: FILES_V1, C2: v2})
    pipeline = IndexingPipeline(session_factory, github, lambda: None)
    github.head = C1
    await pipeline.run(await queue(db_session, repo))

    github.head = C2
    real_get_blob = github.get_blob

    async def flaky(inst, owner, name, sha):
        if github.blobs[sha] == b"export const x = 5;\n":
            raise GitHubServiceError("HTTP 502", 502)
        return await real_get_blob(inst, owner, name, sha)

    github.get_blob = flaky
    job_id = await queue(db_session, repo)
    await pipeline.run(job_id)
    job = await job_row(session_factory, job_id)
    assert job.status == "succeeded" and job.files_failed == 1
    async with session_factory() as s:
        content = (await s.execute(select(CodeChunk.content).join(IndexedFile).where(
            IndexedFile.file_path == "web/x.ts"))).scalar_one()
    assert content == "export const x = 1;\n"  # previous version kept, retried next run
