"""Local end-to-end check of the indexing pipeline on the real stack.

Runs the real FastAPI app, a real Celery worker process, real Redis and real
PostgreSQL + pgvector. GitHub and Gemini are replaced by a local HTTP server that
implements the REST endpoints CodeLens calls (installation tokens, repository
metadata, commits, trees, blobs, batchEmbedContents). The served repository is this
project's own source code.

    cd backend
    venv\\Scripts\\python scripts\\e2e_local.py

Requirements: `docker compose up -d db redis`, and a DISPOSABLE database (default
`codelens_test`; it is wiped). Nothing is sent to GitHub or Google.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
DIMS = 768
WEBHOOK_SECRET = "e2e-webhook-secret"


def git_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load_project_snapshot() -> dict:
    files = {}
    for base, suffixes in (("backend/app", (".py",)), ("frontend/src", (".ts", ".tsx"))):
        for path in sorted((REPO_ROOT / base).rglob("*")):
            if path.suffix in suffixes and "__pycache__" not in path.parts and ".test." not in path.name:
                files[path.relative_to(REPO_ROOT).as_posix()] = path.read_bytes()
    files["README.md"] = b"# not indexed\n"
    files["config/.env"] = b"SECRET=1\n"
    return files


class FakeRemote:
    """In-memory GitHub repository + Gemini embedding endpoint."""

    def __init__(self):
        self.commits = {}
        self.head = None
        self.blobs = {}
        self.calls = {"tokens": 0, "trees": 0, "blobs": 0, "embed_requests": 0, "embedded_texts": 0}

    def add_commit(self, files: dict) -> str:
        sha = hashlib.sha1(json.dumps(sorted((p, git_sha(c)) for p, c in files.items())).encode()).hexdigest()
        self.commits[sha] = files
        for content in files.values():
            self.blobs[git_sha(content)] = content
        self.head = sha
        return sha


def make_handler(remote: FakeRemote):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep output readable
            pass

        def _send(self, status, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            if path.startswith("/app/installations/") and path.endswith("/access_tokens"):
                assert self.headers["Authorization"].startswith("Bearer ")  # RS256 app JWT
                remote.calls["tokens"] += 1
                expires = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                return self._send(201, {"token": "ghs_e2e_installation_token", "expires_at": expires})
            if path.endswith(":batchEmbedContents"):
                assert self.headers.get("x-goog-api-key") == "e2e-fake-gemini-key"
                requests = json.loads(body)["requests"]
                remote.calls["embed_requests"] += 1
                remote.calls["embedded_texts"] += len(requests)
                embeddings = []
                for r in requests:
                    seed = hashlib.sha256(r["content"]["parts"][0]["text"].encode()).digest()
                    embeddings.append({"values": [((seed[i % 32] + i) % 97) / 97.0 + 0.01 for i in range(DIMS)]})
                return self._send(200, {"embeddings": embeddings,
                                        "usageMetadata": {"promptTokenCount": sum(len(r["content"]["parts"][0]["text"]) // 4 for r in requests)}})
            return self._send(404, {"message": "Not Found"})

        def do_GET(self):
            parsed = urlparse(self.path)
            parts = parsed.path.strip("/").split("/")
            assert self.headers.get("Authorization") == "Bearer ghs_e2e_installation_token"
            if len(parts) == 3 and parts[0] == "repos":
                return self._send(200, {"default_branch": "main", "private": True})
            if len(parts) >= 5 and parts[3] == "commits":
                return self._send(200, remote.head.encode(), "text/plain")
            if len(parts) == 6 and parts[3:5] == ["git", "trees"]:
                remote.calls["trees"] += 1
                files = remote.commits[parts[5]]
                tree = [{"path": p, "mode": "100644", "type": "blob", "sha": git_sha(c), "size": len(c)}
                        for p, c in files.items()]
                return self._send(200, {"sha": parts[5], "tree": tree, "truncated": False})
            if len(parts) == 6 and parts[3:5] == ["git", "blobs"]:
                remote.calls["blobs"] += 1
                return self._send(200, remote.blobs[parts[5]], "application/octet-stream")
            return self._send(404, {"message": "Not Found"})

    return Handler


def configure_environment(port: int, db_url: str, redis_base: str, key_path: str) -> dict:
    env = {
        "ENVIRONMENT": "development",
        "DEBUG": "false",
        "DATABASE_URL": db_url,
        "ASYNC_DATABASE_URL": db_url.replace("+psycopg", "+asyncpg"),
        "REDIS_URL": f"{redis_base}/5",
        "CELERY_BROKER_URL": f"{redis_base}/5",
        "CELERY_RESULT_BACKEND": f"{redis_base}/6",
        "GITHUB_API_URL": f"http://127.0.0.1:{port}",
        "GITHUB_APP_ID": "123456",
        "GITHUB_PRIVATE_KEY_PATH": key_path,
        "GITHUB_PRIVATE_KEY": "",
        "GITHUB_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "GEMINI_API_BASE_URL": f"http://127.0.0.1:{port}/v1beta",
        "GEMINI_API_KEY": "e2e-fake-gemini-key",
        "GEMINI_EMBEDDING_MODEL": "gemini-embedding-2",
        "GEMINI_EMBEDDING_DIMENSIONS": str(DIMS),
        "GEMINI_EMBEDDING_BATCH_SIZE": "50",
    }
    os.environ.update(env)
    return env


def write_private_key(directory: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = Path(directory) / "e2e-app-key.pem"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    return str(path)


async def wait_for_job(client, headers, repo_id, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = (await client.get(f"/api/v1/repositories/{repo_id}/index/status", headers=headers)).json()
        if not status["active_job"]:
            return status
        job = status["active_job"]
        print(f"    ... {job['status']:<8} {job.get('phase') or '':<12} {job['progress']:5.1f}%", flush=True)
        await asyncio.sleep(1.5)
    raise TimeoutError("indexing did not finish in time")


async def scenario(remote: FakeRemote, commit_files: list) -> dict:
    import httpx

    from app.core.security import create_access_token
    from app.database.session import AsyncSessionLocal
    from app.main import app
    from app.models import GitHubInstallation, Repository, User

    async with AsyncSessionLocal() as s:
        user = User(github_id=424242, username="e2e-user")
        s.add(user)
        await s.flush()
        inst = GitHubInstallation(user_id=user.id, installation_id=987654, account_login="e2e-org")
        s.add(inst)
        await s.flush()
        repo = Repository(installation_id=inst.id, github_repo_id=111222, name="codelens", owner="e2e-org",
                          full_name="e2e-org/codelens", default_branch="main")
        s.add(repo)
        await s.commit()
        repo_id, user_id = repo.id, user.id
    headers = {"Authorization": f"Bearer {create_access_token(str(user_id))}"}
    report = {}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://e2e") as client:
        # 1. Initial index through the API -> Redis -> Celery worker.
        print("[1] initial indexing")
        started = await client.post(f"/api/v1/repositories/{repo_id}/index", headers=headers)
        assert started.status_code == 202, started.text
        status = await wait_for_job(client, headers, repo_id)
        job = status["latest_job"]
        assert job["status"] == "succeeded", job
        assert status["total_files"] > 50 and status["total_chunks"] > 100 and job["embeddings_created"] > 0
        report["initial"] = {k: job[k] for k in ("files_total", "files_indexed", "files_skipped", "chunks_total",
                                                  "embeddings_created", "embeddings_reused")}
        report["initial"]["skipped_by_reason"] = job["stats"]["skipped_by_reason"]
        report["initial"]["token_estimate_ratio"] = job["stats"]["token_estimate_ratio"]

        stats = (await client.get(f"/api/v1/repositories/{repo_id}/index/stats", headers=headers)).json()
        assert stats["chunks_with_embeddings"] == stats["total_chunks"]
        report["stats"] = {k: stats[k] for k in ("files_by_language", "chunks_by_type", "dependencies_total",
                                                 "dependencies_resolved", "chunks_with_embeddings")}

        search = await client.get(f"/api/v1/repositories/{repo_id}/search",
                                  params={"q": "how are embeddings validated", "limit": 3}, headers=headers)
        assert search.status_code == 200 and search.json()["results"], search.text
        report["search_top_hits"] = [(r["file_path"], r["chunk"]["qualified_name"], r["score"])
                                     for r in search.json()["results"]]

        # 2. Re-run at the same commit: nothing downloaded or embedded again.
        print("[2] re-run at the same commit")
        calls = dict(remote.calls)
        await client.post(f"/api/v1/repositories/{repo_id}/index", headers=headers)
        job = (await wait_for_job(client, headers, repo_id))["latest_job"]
        assert job["status"] == "succeeded" and job["files_changed"] == 0
        assert remote.calls["blobs"] == calls["blobs"] and remote.calls["embedded_texts"] == calls["embedded_texts"]
        report["rerun"] = {"files_changed": 0, "new_blob_downloads": 0, "new_embeddings": 0,
                           "embeddings_reused": job["embeddings_reused"]}

        # 3. New commit via a signed push webhook -> incremental_reindex task.
        print("[3] push webhook -> incremental reindex")
        head = remote.add_commit(commit_files[1])
        blobs_before, embedded_before = remote.calls["blobs"], remote.calls["embedded_texts"]
        payload = json.dumps({"ref": "refs/heads/main", "after": head,
                              "repository": {"id": 111222, "full_name": "e2e-org/codelens"}}).encode()
        signature = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        hook = await client.post("/api/v1/webhooks/github", content=payload, headers={
            "X-GitHub-Event": "push", "X-GitHub-Delivery": str(uuid.uuid4()),
            "X-Hub-Signature-256": signature, "Content-Type": "application/json"})
        assert hook.status_code == 200 and hook.json()["status"] == "processed", hook.text
        deadline = time.time() + 120
        while True:
            jobs = (await client.get(f"/api/v1/repositories/{repo_id}/index/jobs", headers=headers)).json()
            push_jobs = [j for j in jobs if j["trigger"] == "push"]
            if push_jobs and push_jobs[0]["status"] in ("succeeded", "failed"):
                break
            assert time.time() < deadline, "push job did not finish"
            await asyncio.sleep(1.5)
        job = push_jobs[0]
        assert job["status"] == "succeeded" and job["commit_sha"] == head, job
        assert (job["files_changed"], job["files_deleted"]) == (2, 2), job  # 1 changed + 1 added; 2 removed
        report["incremental_push"] = {
            "files_changed": job["files_changed"], "files_deleted": job["files_deleted"],
            "blob_downloads": remote.calls["blobs"] - blobs_before,
            "new_embedded_texts": remote.calls["embedded_texts"] - embedded_before,
            "embeddings_reused": job["embeddings_reused"],
        }
        files = (await client.get(f"/api/v1/repositories/{repo_id}/index/files", params={"q": "e2e_added"},
                                  headers=headers)).json()
        assert [f["file_path"] for f in files] == ["backend/app/e2e_added.py"]
        report["remote_calls"] = remote.calls
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", default="postgresql+psycopg://postgres:postgres@localhost:5432/codelens_test")
    parser.add_argument("--redis", default="redis://localhost:6379")
    args = parser.parse_args()

    remote = FakeRemote()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(remote))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    v1 = load_project_snapshot()
    v2 = dict(v1)
    changed = "backend/app/indexing/tokens.py"
    v2[changed] = v1[changed] + b"\n\ndef e2e_marker():\n    return 'changed'\n"
    v2.pop("frontend/src/vite-env.d.ts", None)
    removed = next(p for p in v1 if p.startswith("frontend/src/pages/") and p.endswith(".tsx"))
    v2.pop(removed)
    v2["backend/app/e2e_added.py"] = b"def added_in_second_commit():\n    return 2\n"
    remote.add_commit(v1)

    with tempfile.TemporaryDirectory() as tmp:
        env = configure_environment(port, args.db_url, args.redis, write_private_key(tmp))
        sys.path.insert(0, str(BACKEND))
        os.chdir(BACKEND)

        print("[0] migrating disposable database (downgrade base -> upgrade head)")
        from alembic import command
        from alembic.config import Config

        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")

        import redis

        redis.Redis.from_url(env["CELERY_BROKER_URL"]).flushdb()
        worker = subprocess.Popen(
            [sys.executable, "-m", "celery", "-A", "app.workers.celery_app", "worker", "--pool=solo",
             "--loglevel=WARNING", "--without-heartbeat", "--without-gossip", "--without-mingle"],
            cwd=BACKEND, env={**os.environ}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            time.sleep(4)
            assert worker.poll() is None, worker.stdout.read()
            report = asyncio.run(scenario(remote, [v1, v2]))
        except BaseException:
            worker.terminate()
            print("---- worker output ----")
            print(worker.communicate(timeout=20)[0][-6000:])
            raise
        finally:
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    worker.kill()
            server.shutdown()
    print("\nE2E RESULT: PASSED")
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
