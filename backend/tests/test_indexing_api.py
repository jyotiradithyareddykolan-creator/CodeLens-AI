"""Indexing API tests: authentication, ownership, job dispatch, status and content views."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.indexing import jobs as job_service
from app.indexing.chunking import chunk_file
from app.indexing.parsing import parse_file
from app.indexing.store import FileIndexData, apply_snapshot, save_embeddings
from app.models import IndexingJob, Repository
from app.models.indexing import EMBEDDING_DIMENSIONS
from tests.helpers import make_user_with_installation

COMMIT = "c" * 40


async def setup(db_session, github_id=1, installation_id=10, repo_id=100):
    user, inst, headers = await make_user_with_installation(db_session, github_id, f"user{github_id}", installation_id,
                                                            account_login=f"org{github_id}")
    repo = Repository(installation_id=inst.id, github_repo_id=repo_id, name="r", owner=f"org{github_id}",
                      full_name=f"org{github_id}/r{repo_id}")
    db_session.add(repo)
    await db_session.commit()
    return repo, headers


@pytest.fixture
def dispatched(monkeypatch):
    calls = []

    def fake_dispatch(job_id):
        calls.append(job_id)
        return f"celery-{job_id}"

    monkeypatch.setattr(job_service, "dispatch_index_job", fake_dispatch)
    return calls


async def index_snapshot(db_session, repo):
    text = "import os\n\n\nclass Service:\n    def run(self):\n        return os.getcwd()\n\n\ndef helper():\n    return 1\n"
    parsed = parse_file("app/service.py", "python", text)
    chunks = chunk_file(parsed, repository_id=repo.id, commit_sha=COMMIT)
    keys = [c.content_hash for c in chunks]
    data = FileIndexData(path="app/service.py", blob_sha="d" * 40, language="python", size_bytes=len(text),
                         parse=parsed.result, chunks=chunks, embedding_keys=keys)
    vectors = []
    for i, key in enumerate(keys):
        v = [0.0] * EMBEDDING_DIMENSIONS
        v[i] = 1.0
        vectors.append((key, v))
    await save_embeddings(db_session, repo.id, "gemini-embedding-2", EMBEDDING_DIMENSIONS, vectors)
    await apply_snapshot(db_session, repo.id, COMMIT, [data], [])
    await db_session.commit()
    return chunks


@pytest.mark.asyncio
async def test_endpoints_require_authentication(client: AsyncClient, db_session):
    repo, _ = await setup(db_session)
    for method, path in [
        ("POST", f"/api/repositories/{repo.id}/index"),
        ("POST", f"/api/repositories/{repo.id}/reindex"),
        ("GET", f"/api/repositories/{repo.id}/index/status"),
        ("GET", f"/api/repositories/{repo.id}/index/jobs"),
        ("GET", f"/api/repositories/{repo.id}/index/stats"),
        ("GET", f"/api/repositories/{repo.id}/index/files"),
        ("GET", f"/api/repositories/{repo.id}/symbols"),
        ("GET", f"/api/repositories/{repo.id}/search?q=auth"),
    ]:
        assert (await client.request(method, path)).status_code == 401, path


@pytest.mark.asyncio
async def test_other_users_cannot_index_or_read_a_repository(client: AsyncClient, db_session, dispatched):
    repo, _ = await setup(db_session, 1, 10, 100)
    _, intruder = await setup(db_session, 2, 20, 200)
    await index_snapshot(db_session, repo)
    file_id = (await client.get(f"/api/repositories/{repo.id}/index/files",
                                headers=(await setup(db_session, 3, 30, 300))[1])).status_code
    assert file_id == 404
    for method, path in [
        ("POST", f"/api/repositories/{repo.id}/index"),
        ("POST", f"/api/repositories/{repo.id}/reindex"),
        ("GET", f"/api/repositories/{repo.id}/index/status"),
        ("GET", f"/api/repositories/{repo.id}/index/jobs"),
        ("GET", f"/api/repositories/{repo.id}/index/stats"),
        ("GET", f"/api/repositories/{repo.id}/index/files"),
        ("GET", f"/api/repositories/{repo.id}/symbols"),
        ("GET", f"/api/repositories/{repo.id}/search?q=auth"),
    ]:
        assert (await client.request(method, path, headers=intruder)).status_code == 404, path
    assert dispatched == []


@pytest.mark.asyncio
async def test_start_indexing_creates_and_dispatches_one_job(client: AsyncClient, db_session, dispatched):
    repo, headers = await setup(db_session)
    first = await client.post(f"/api/repositories/{repo.id}/index", headers=headers)
    assert first.status_code == 202
    job = first.json()
    assert (job["status"], job["trigger"], job["force_full"], job["progress"]) == ("queued", "manual", False, 0.0)
    assert dispatched == [uuid.UUID(job["id"])]

    # A second request right away returns the same job without re-sending it
    # (normal pick-up latency; repeated clicks never create jobs or message storms).
    second = await client.post(f"/api/repositories/{repo.id}/index", headers=headers, json={"force_full": True})
    assert second.status_code == 200 and second.json()["id"] == job["id"]
    assert dispatched == [uuid.UUID(job["id"])]

    status_ = (await client.get(f"/api/repositories/{repo.id}/index/status", headers=headers)).json()
    assert status_["active_job"]["id"] == job["id"]
    assert status_["indexing_status"] == "pending"
    assert len((await client.get(f"/api/repositories/{repo.id}/index/jobs", headers=headers)).json()) == 1
    stored = (await db_session.execute(select(IndexingJob).where(IndexingJob.id == uuid.UUID(job["id"])))).scalar_one()
    assert stored.celery_task_id == f"celery-{job['id']}"


@pytest.mark.asyncio
async def test_reindex_forces_full_run(client: AsyncClient, db_session, dispatched):
    repo, headers = await setup(db_session)
    response = await client.post(f"/api/repositories/{repo.id}/reindex", headers=headers)
    assert response.status_code == 202 and response.json()["force_full"] is True


@pytest.mark.asyncio
async def test_queue_unavailable_returns_503_and_marks_job_failed(client: AsyncClient, db_session, monkeypatch):
    def broken(job_id):
        raise job_service.IndexingDispatchError("The indexing queue is unavailable. Is Redis running?")

    monkeypatch.setattr(job_service, "dispatch_index_job", broken)
    repo, headers = await setup(db_session)
    response = await client.post(f"/api/repositories/{repo.id}/index", headers=headers)
    assert response.status_code == 503 and "Redis" in response.json()["detail"]
    job = (await db_session.execute(select(IndexingJob))).scalar_one()
    assert job.status == "failed"
    # The failed job frees the slot: a new attempt is possible.
    monkeypatch.setattr(job_service, "dispatch_index_job", lambda job_id: "ok")
    assert (await client.post(f"/api/repositories/{repo.id}/index", headers=headers)).status_code == 202


@pytest.mark.asyncio
async def test_retry_only_failed_jobs(client: AsyncClient, db_session, dispatched):
    repo, headers = await setup(db_session)
    done = IndexingJob(repository_id=repo.id, status="succeeded")
    failed = IndexingJob(repository_id=repo.id, status="failed", force_full=True, error_message="boom")
    db_session.add_all([done, failed])
    await db_session.commit()

    assert (await client.post(f"/api/repositories/{repo.id}/index/jobs/{done.id}/retry", headers=headers)).status_code == 409
    assert (await client.post(f"/api/repositories/{repo.id}/index/jobs/{uuid.uuid4()}/retry", headers=headers)).status_code == 404
    retried = await client.post(f"/api/repositories/{repo.id}/index/jobs/{failed.id}/retry", headers=headers)
    assert retried.status_code == 202
    assert (retried.json()["trigger"], retried.json()["force_full"]) == ("retry", True)
    jobs = (await client.get(f"/api/repositories/{repo.id}/index/jobs", headers=headers)).json()
    assert len(jobs) == 3 and jobs[0]["id"] == retried.json()["id"]


@pytest.mark.asyncio
async def test_status_stats_files_chunks_and_symbols_after_indexing(client: AsyncClient, db_session):
    repo, headers = await setup(db_session)
    chunks = await index_snapshot(db_session, repo)

    status_ = (await client.get(f"/api/repositories/{repo.id}/index/status", headers=headers)).json()
    assert (status_["indexing_status"], status_["last_indexed_commit"], status_["total_files"], status_["total_chunks"]) == (
        "indexed", COMMIT, 1, len(chunks))
    assert status_["active_job"] is None

    stats = (await client.get(f"/api/repositories/{repo.id}/index/stats", headers=headers)).json()
    assert stats["files_by_language"] == {"python": 1}
    assert stats["symbols_by_kind"] == {"class": 1, "method": 1, "function": 1}
    assert stats["chunks_with_embeddings"] == stats["total_chunks"] == len(chunks)
    assert stats["dependencies_total"] == 1 and stats["dependencies_resolved"] == 0  # `os` is external

    files = (await client.get(f"/api/repositories/{repo.id}/index/files", headers=headers)).json()
    assert [(f["file_path"], f["chunk_count"], f["commit_sha"]) for f in files] == [("app/service.py", len(chunks), COMMIT)]
    assert (await client.get(f"/api/repositories/{repo.id}/index/files?q=nomatch", headers=headers)).json() == []

    listed = (await client.get(f"/api/repositories/{repo.id}/index/files/{files[0]['id']}/chunks", headers=headers)).json()
    assert [c["content"] for c in listed] == [c.content for c in chunks]
    assert listed[1]["chunk_type"] == "class" and listed[1]["start_line"] == 4 and listed[1]["has_embedding"]
    assert (await client.get(f"/api/repositories/{repo.id}/index/files/{uuid.uuid4()}/chunks", headers=headers)).status_code == 404

    symbols = (await client.get(f"/api/repositories/{repo.id}/symbols?kind=method", headers=headers)).json()
    assert [(s["qualified_name"], s["file_path"]) for s in symbols] == [("Service.run", "app/service.py")]


@pytest.mark.asyncio
async def test_search_requires_gemini_key(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    repo, headers = await setup(db_session)
    response = await client.get(f"/api/repositories/{repo.id}/search?q=service", headers=headers)
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_search_returns_nearest_chunks(client: AsyncClient, db_session, monkeypatch):
    from app.api.v1 import indexing as indexing_api

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    repo, headers = await setup(db_session)
    chunks = await index_snapshot(db_session, repo)

    class FakeEmbedder:
        async def embed_query(self, query):
            v = [0.0] * EMBEDDING_DIMENSIONS
            v[2] = 1.0  # matches the third chunk's stored vector
            return v

    monkeypatch.setattr(indexing_api, "GeminiEmbedder", FakeEmbedder)
    response = await client.get(f"/api/repositories/{repo.id}/search?q=helper&limit=2", headers=headers)
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["chunk"]["content"] == chunks[2].content
    assert results[0]["file_path"] == "app/service.py" and results[0]["score"] == pytest.approx(1.0)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_disconnect_removes_index_and_jobs(client: AsyncClient, db_session, dispatched):
    repo, headers = await setup(db_session)
    await index_snapshot(db_session, repo)
    await client.post(f"/api/repositories/{repo.id}/reindex", headers=headers)
    assert (await client.delete(f"/api/repositories/{repo.id}", headers=headers)).status_code == 200
    assert (await db_session.execute(select(IndexingJob))).scalars().all() == []
    assert (await client.get(f"/api/repositories/{repo.id}/index/status", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_running_or_retry_scheduled_jobs_are_not_redispatched(client: AsyncClient, db_session, dispatched):
    from datetime import timedelta

    from app.indexing.jobs import utcnow

    repo, headers = await setup(db_session)
    job = IndexingJob(repository_id=repo.id, status="running", heartbeat_at=utcnow())
    db_session.add(job)
    await db_session.commit()
    response = await client.post(f"/api/repositories/{repo.id}/index", headers=headers)
    assert response.status_code == 200 and response.json()["id"] == str(job.id)
    assert dispatched == []

    job.status = "queued"
    job.next_retry_at = utcnow() + timedelta(minutes=5)
    await db_session.commit()
    await client.post(f"/api/repositories/{repo.id}/index", headers=headers)
    assert dispatched == []  # waits for its scheduled Celery retry

    job.next_retry_at = utcnow() - timedelta(minutes=1)
    job.updated_at = utcnow() - timedelta(minutes=2)
    await db_session.commit()
    await client.post(f"/api/repositories/{repo.id}/index", headers=headers)
    assert dispatched == [job.id]  # retry time passed without a worker picking it up


@pytest.mark.asyncio
async def test_stuck_queued_job_is_resent_once_per_window(client: AsyncClient, db_session, dispatched):
    from datetime import timedelta

    from app.indexing.jobs import RESEND_AFTER_SECONDS, utcnow

    repo, headers = await setup(db_session)
    job = IndexingJob(repository_id=repo.id, status="queued")
    db_session.add(job)
    await db_session.commit()
    job.updated_at = utcnow() - timedelta(seconds=RESEND_AFTER_SECONDS + 5)
    await db_session.commit()

    for _ in range(3):  # rapid repeated clicks
        response = await client.post(f"/api/repositories/{repo.id}/index", headers=headers)
        assert response.status_code == 200 and response.json()["id"] == str(job.id)
    assert dispatched == [job.id]  # re-sent once; the re-send restarts the window
    jobs = (await db_session.execute(select(IndexingJob).where(IndexingJob.repository_id == repo.id))).scalars().all()
    assert len(jobs) == 1  # never a duplicate job


@pytest.mark.asyncio
async def test_job_history_is_paginated_newest_first_and_scoped(client: AsyncClient, db_session):
    from datetime import timedelta

    from app.indexing.jobs import utcnow

    repo, headers = await setup(db_session, 1, 10, 100)
    other_repo, other_headers = await setup(db_session, 2, 20, 200)
    base = utcnow() - timedelta(hours=1)
    jobs = []
    for i in range(5):
        job = IndexingJob(repository_id=repo.id, status="succeeded" if i % 2 == 0 else "failed",
                          commit_sha=f"{i}" * 40, error_message=None if i % 2 == 0 else f"error {i}",
                          files_total=10 + i, files_changed=i, embeddings_created=i, embeddings_reused=100 - i,
                          started_at=base + timedelta(minutes=i), finished_at=base + timedelta(minutes=i, seconds=8))
        db_session.add(job)
        await db_session.flush()
        job.created_at = base + timedelta(minutes=i)
        jobs.append(job)
    foreign = IndexingJob(repository_id=other_repo.id, status="failed", error_message="not yours")
    db_session.add(foreign)
    await db_session.commit()

    page1 = (await client.get(f"/api/repositories/{repo.id}/index/jobs?limit=2", headers=headers)).json()
    page2 = (await client.get(f"/api/repositories/{repo.id}/index/jobs?limit=2&skip=2", headers=headers)).json()
    page3 = (await client.get(f"/api/repositories/{repo.id}/index/jobs?limit=2&skip=4", headers=headers)).json()
    assert [j["commit_sha"][0] for j in page1 + page2 + page3] == ["4", "3", "2", "1", "0"]
    assert page1[1]["status"] == "failed" and page1[1]["error_message"] == "error 3"
    assert (page1[0]["files_total"], page1[0]["embeddings_created"], page1[0]["embeddings_reused"]) == (14, 4, 96)
    assert page1[0]["started_at"] and page1[0]["finished_at"]
    assert (await client.get(f"/api/repositories/{repo.id}/index/jobs?skip=-1", headers=headers)).status_code == 422

    detail = await client.get(f"/api/repositories/{repo.id}/index/jobs/{jobs[3].id}", headers=headers)
    assert detail.status_code == 200 and detail.json()["error_message"] == "error 3"
    # Another user's job is invisible, even through a repository the caller owns.
    assert (await client.get(f"/api/repositories/{repo.id}/index/jobs/{foreign.id}", headers=headers)).status_code == 404
    assert (await client.get(f"/api/repositories/{other_repo.id}/index/jobs", headers=headers)).status_code == 404
    assert (await client.get(f"/api/repositories/{repo.id}/index/jobs/{jobs[0].id}", headers=other_headers)).status_code == 404
    assert (await client.get(f"/api/repositories/{repo.id}/index/jobs/{jobs[0].id}")).status_code == 401
