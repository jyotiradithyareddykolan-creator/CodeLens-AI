"""GitHub REST client behaviour. All HTTP is mocked with httpx.MockTransport."""

import httpx
import pytest

from app.core.config import settings
from app.services.github_service import (
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubService,
    GitHubServiceError,
)

COMMIT = "a" * 40
TREE = "b" * 40
BLOB = "c" * 40
NOW = 1_000_000.0


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_service(handler, sleeps=None):
    async def fake_sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)

    svc = GitHubService(transport=httpx.MockTransport(handler), sleep=fake_sleep, clock=lambda: NOW)
    svc._installation_token_cache[1] = ("installation-token", NOW + 3600)
    return svc


@pytest.mark.asyncio
async def test_resolve_commit_sha_pins_ref_and_sends_read_only_get():
    rec = Recorder([httpx.Response(200, text=COMMIT + "\n")])
    svc = make_service(rec)
    assert await svc.resolve_commit_sha(1, "octo", "repo", "feature/x") == COMMIT
    req = rec.requests[0]
    assert req.method == "GET"
    assert req.url.path == "/repos/octo/repo/commits/feature/x"
    assert req.headers["accept"] == "application/vnd.github.sha"
    assert req.headers["authorization"] == "Bearer installation-token"


@pytest.mark.asyncio
async def test_resolve_commit_sha_validates_input_and_output():
    svc = make_service(Recorder([httpx.Response(200, text="not-a-sha")]))
    with pytest.raises(GitHubServiceError):
        await svc.resolve_commit_sha(1, "octo", "repo", "main")
    for bad_ref in ("", "../etc", "/abs", "a" * 300):
        with pytest.raises(GitHubServiceError):
            await svc.resolve_commit_sha(1, "octo", "repo", bad_ref)
    svc = make_service(Recorder([httpx.Response(404, json={"message": "No commit found"})]))
    with pytest.raises(GitHubNotFoundError):
        await svc.resolve_commit_sha(1, "octo", "repo", "missing")


@pytest.mark.asyncio
async def test_owner_and_repo_are_url_encoded():
    rec = Recorder([httpx.Response(200, json={"sha": TREE, "tree": [], "truncated": False})])
    svc = make_service(rec)
    await svc.get_tree(1, "octo/../x", "re po", TREE, recursive=True)
    assert rec.requests[0].url.raw_path.startswith(b"/repos/octo%2F..%2Fx/re%20po/git/trees/")
    assert rec.requests[0].url.params["recursive"] == "1"


@pytest.mark.asyncio
async def test_get_tree_and_blob_reject_invalid_shas_without_requests():
    rec = Recorder([])
    svc = make_service(rec)
    with pytest.raises(GitHubServiceError):
        await svc.get_tree(1, "o", "r", "main", recursive=True)
    with pytest.raises(GitHubServiceError):
        await svc.get_blob(1, "o", "r", "../../x")
    assert rec.requests == []


@pytest.mark.asyncio
async def test_get_blob_returns_raw_bytes():
    rec = Recorder([httpx.Response(200, content=b"print('hi')\n")])
    svc = make_service(rec)
    assert await svc.get_blob(1, "o", "r", BLOB) == b"print('hi')\n"
    assert rec.requests[0].headers["accept"] == "application/vnd.github.raw+json"


@pytest.mark.asyncio
async def test_transient_server_errors_are_retried_with_backoff():
    sleeps = []
    rec = Recorder(
        [
            httpx.Response(502),
            httpx.Response(503),
            httpx.Response(200, json={"sha": TREE, "tree": [], "truncated": False}),
        ]
    )
    svc = make_service(rec, sleeps)
    tree = await svc.get_tree(1, "o", "r", TREE, recursive=False)
    assert tree["tree"] == []
    assert len(rec.requests) == 3
    assert len(sleeps) == 2 and all(0 < s <= 30 for s in sleeps)


@pytest.mark.asyncio
async def test_network_errors_are_retried_then_reported_safely(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_MAX_RETRIES", 2)
    rec = Recorder([httpx.ConnectError("boom with secret-ish detail")] * 3)
    svc = make_service(rec, [])
    with pytest.raises(GitHubServiceError) as exc:
        await svc.get_blob(1, "o", "r", BLOB)
    assert len(rec.requests) == 3
    assert "ConnectError" in str(exc.value)
    assert "secret-ish" not in str(exc.value)
    assert "installation-token" not in str(exc.value)


@pytest.mark.asyncio
async def test_persistent_server_error_returns_final_status(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_MAX_RETRIES", 1)
    rec = Recorder([httpx.Response(500), httpx.Response(500, json={"message": "Server Error"})])
    svc = make_service(rec, [])
    with pytest.raises(GitHubServiceError) as exc:
        await svc.get_blob(1, "o", "r", BLOB)
    assert exc.value.status_code == 500
    assert len(rec.requests) == 2


@pytest.mark.asyncio
async def test_primary_rate_limit_waits_until_reset():
    sleeps = []
    rec = Recorder(
        [
            httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW + 30))},
                json={"message": "API rate limit exceeded"},
            ),
            httpx.Response(200, content=b"ok"),
        ]
    )
    svc = make_service(rec, sleeps)
    assert await svc.get_blob(1, "o", "r", BLOB) == b"ok"
    assert sleeps == [31.0]


@pytest.mark.asyncio
async def test_secondary_rate_limit_honours_retry_after():
    sleeps = []
    rec = Recorder(
        [
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(
                403, json={"message": "You have exceeded a secondary rate limit"}
            ),
            httpx.Response(200, content=b"ok"),
        ]
    )
    svc = make_service(rec, sleeps)
    assert await svc.get_blob(1, "o", "r", BLOB) == b"ok"
    assert sleeps == [7.0, 60.0]


@pytest.mark.asyncio
async def test_rate_limit_longer_than_max_wait_fails_fast(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_MAX_RETRY_WAIT_SECONDS", 60)
    sleeps = []
    rec = Recorder(
        [
            httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW + 3000))},
            )
        ]
    )
    svc = make_service(rec, sleeps)
    with pytest.raises(GitHubRateLimitError) as exc:
        await svc.get_blob(1, "o", "r", BLOB)
    assert sleeps == []
    assert exc.value.retry_after_seconds > 60


@pytest.mark.asyncio
async def test_permission_403_is_not_treated_as_rate_limit():
    sleeps = []
    rec = Recorder([httpx.Response(403, json={"message": "Resource not accessible by integration"})])
    svc = make_service(rec, sleeps)
    with pytest.raises(GitHubServiceError) as exc:
        await svc.get_blob(1, "o", "r", BLOB)
    assert exc.value.status_code == 403
    assert sleeps == [] and len(rec.requests) == 1


@pytest.mark.asyncio
async def test_rejected_installation_token_is_refreshed_once(monkeypatch):
    svc = make_service(lambda r: None)
    minted = []

    async def fake_token(installation_id):
        token = f"token-{len(minted)}"
        minted.append(token)
        svc._installation_token_cache[installation_id] = (token, NOW + 3600)
        return token

    rec = Recorder([httpx.Response(401), httpx.Response(200, content=b"ok")])
    svc._transport = httpx.MockTransport(rec)
    monkeypatch.setattr(svc, "get_installation_access_token", fake_token)
    assert await svc.get_blob(1, "o", "r", BLOB) == b"ok"
    assert [r.headers["authorization"] for r in rec.requests] == ["Bearer token-0", "Bearer token-1"]


@pytest.mark.asyncio
async def test_pagination_follows_link_header():
    page1 = [{"id": i} for i in range(100)]
    page2 = [{"id": 100}]
    rec = Recorder(
        [
            httpx.Response(200, json={"repositories": page1}, headers={"link": '<x?page=2>; rel="next"'}),
            httpx.Response(200, json={"repositories": page2}),
        ]
    )
    svc = make_service(rec)
    repos = await svc.list_installation_repositories(1)
    assert len(repos) == 101
    assert [r.url.params["page"] for r in rec.requests] == ["1", "2"]


def test_missing_key_file_mounted_as_directory_reports_missing_credentials(tmp_path, monkeypatch):
    """Docker turns a bind mount of a missing host file into an empty directory."""
    mount = tmp_path / "github-app.pem"
    mount.mkdir()
    monkeypatch.setattr(settings, "GITHUB_APP_ID", "123")
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY", None)
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY_PATH", str(mount))
    with pytest.raises(GitHubServiceError) as exc:
        GitHubService().create_app_jwt()
    assert "credentials missing" in str(exc.value)


def test_private_key_is_read_from_a_mounted_file(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jose import jwt as jose_jwt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "github-app.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    monkeypatch.setattr(settings, "GITHUB_APP_ID", "123")
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY", None)
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY_PATH", str(pem))
    token = GitHubService().create_app_jwt()
    assert jose_jwt.get_unverified_claims(token)["iss"] == "123"
