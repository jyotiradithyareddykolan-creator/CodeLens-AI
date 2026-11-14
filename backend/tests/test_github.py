import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, decode_access_token
from app.services.github_service import GitHubServiceError, github_service
from tests.helpers import make_user_with_installation


def github_repo(repo_id: int = 998877, name: str = "sample-service", owner: str = "codelens-org"):
    return {
        "id": repo_id,
        "name": name,
        "full_name": f"{owner}/{name}",
        "owner": {"login": owner},
        "default_branch": "develop",
        "private": True,
        "description": "Production microservice for testing CodeLens AI",
        "html_url": f"https://github.com/{owner}/{name}",
    }


@pytest.mark.asyncio
async def test_session_token_generation_and_decoding():
    """Verify CodeLens user session JWT encoding and decoding."""
    user_id = "11111111-2222-3333-4444-555555555555"
    token = create_access_token(subject=user_id)
    assert token is not None

    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == user_id


@pytest.mark.asyncio
async def test_repository_endpoints_require_authentication(client: AsyncClient):
    assert (await client.get("/api/repositories")).status_code == 401
    assert (
        await client.post(
            "/api/repositories/connect",
            json={"installation_id": "00000000-0000-0000-0000-000000000000", "github_repo_id": 1},
        )
    ).status_code == 401
    assert (await client.get("/api/github/installations")).status_code == 401
    bad = {"Authorization": "Bearer not-a-jwt"}
    assert (await client.get("/api/repositories", headers=bad)).status_code == 401


@pytest.mark.asyncio
async def test_repository_connect_and_list_endpoints(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Connect uses GitHub (mocked) as the source of truth for repository metadata."""
    _, installation, headers = await make_user_with_installation(
        db_session, github_id=9988, username="connected-user", installation_id=778899
    )
    calls = []

    async def fake_get_repository_by_id(gh_installation_id, github_repo_id):
        calls.append((gh_installation_id, github_repo_id))
        return github_repo(github_repo_id)

    monkeypatch.setattr(github_service, "get_repository_by_id", fake_get_repository_by_id)

    # Client-supplied metadata is ignored; only the IDs are used.
    connect_payload = {
        "installation_id": str(installation.id),
        "github_repo_id": 998877,
        "full_name": "attacker/spoofed",
    }
    create_resp = await client.post("/api/repositories/connect", json=connect_payload, headers=headers)
    assert create_resp.status_code == 201
    assert calls == [(778899, 998877)]
    repo_data = create_resp.json()
    repo_id = repo_data["id"]
    assert repo_data["full_name"] == "codelens-org/sample-service"
    assert repo_data["default_branch"] == "develop"
    assert repo_data["is_private"] is True
    assert repo_data["is_indexed"] is False

    # Connecting again is idempotent and does not call GitHub.
    again = await client.post("/api/repositories/connect", json=connect_payload, headers=headers)
    assert again.status_code == 200
    assert again.json()["id"] == repo_id
    assert len(calls) == 1

    list_resp = await client.get("/api/repositories", headers=headers)
    assert list_resp.status_code == 200
    assert [r["id"] for r in list_resp.json()] == [repo_id]

    detail_resp = await client.get(f"/api/repositories/{repo_id}", headers=headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["name"] == "sample-service"

    status_resp = await client.get(f"/api/repositories/{repo_id}/status", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["indexing_status"] == "unindexed"

    branches_resp = await client.get(f"/api/repositories/{repo_id}/branches", headers=headers)
    assert branches_resp.status_code == 200
    branches = branches_resp.json()
    assert len(branches) == 1
    assert branches[0]["name"] == "develop"
    assert branches[0]["is_default"] is True

    delete_resp = await client.delete(f"/api/repositories/{repo_id}", headers=headers)
    assert delete_resp.status_code == 200
    assert delete_resp.json()["status"] == "disconnected"

    check_resp = await client.get(f"/api/repositories/{repo_id}", headers=headers)
    assert check_resp.status_code == 404


@pytest.mark.asyncio
async def test_connect_rejects_repository_not_granted_to_installation(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    _, installation, headers = await make_user_with_installation(
        db_session, github_id=1, username="u1", installation_id=111
    )

    async def not_accessible(gh_installation_id, github_repo_id):
        return None

    monkeypatch.setattr(github_service, "get_repository_by_id", not_accessible)
    resp = await client.post(
        "/api/repositories/connect",
        json={"installation_id": str(installation.id), "github_repo_id": 42},
        headers=headers,
    )
    assert resp.status_code == 404
    assert (await client.get("/api/repositories", headers=headers)).json() == []


@pytest.mark.asyncio
async def test_connect_reports_github_failures_without_leaking_details(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    _, installation, headers = await make_user_with_installation(
        db_session, github_id=1, username="u1", installation_id=111
    )

    async def failing(gh_installation_id, github_repo_id):
        raise GitHubServiceError("HTTP 500: internal detail", 500)

    monkeypatch.setattr(github_service, "get_repository_by_id", failing)
    resp = await client.post(
        "/api/repositories/connect",
        json={"installation_id": str(installation.id), "github_repo_id": 42},
        headers=headers,
    )
    assert resp.status_code == 502
    assert "internal detail" not in resp.text


@pytest.mark.asyncio
async def test_users_cannot_see_or_use_each_others_installations_and_repositories(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    _, inst_a, headers_a = await make_user_with_installation(
        db_session, github_id=1, username="alice", installation_id=111, account_login="alice"
    )
    _, inst_b, headers_b = await make_user_with_installation(
        db_session, github_id=2, username="bob", installation_id=222, account_login="bob"
    )

    async def fake_get_repository_by_id(gh_installation_id, github_repo_id):
        return github_repo(github_repo_id, owner="alice")

    monkeypatch.setattr(github_service, "get_repository_by_id", fake_get_repository_by_id)

    created = await client.post(
        "/api/repositories/connect",
        json={"installation_id": str(inst_a.id), "github_repo_id": 5},
        headers=headers_a,
    )
    assert created.status_code == 201
    repo_id = created.json()["id"]

    # Bob cannot connect through Alice's installation...
    resp = await client.post(
        "/api/repositories/connect",
        json={"installation_id": str(inst_a.id), "github_repo_id": 6},
        headers=headers_b,
    )
    assert resp.status_code == 404
    # ...nor list, read or delete Alice's repository.
    assert (await client.get("/api/repositories", headers=headers_b)).json() == []
    for method, path in [
        ("GET", f"/api/repositories/{repo_id}"),
        ("GET", f"/api/repositories/{repo_id}/status"),
        ("GET", f"/api/repositories/{repo_id}/branches"),
        ("GET", f"/api/repositories/{repo_id}/files"),
        ("DELETE", f"/api/repositories/{repo_id}"),
    ]:
        r = await client.request(method, path, headers=headers_b)
        assert r.status_code == 404, (method, path)
    # Bob cannot list repositories of Alice's installation either.
    r = await client.get(f"/api/github/installations/{inst_a.id}/repositories", headers=headers_b)
    assert r.status_code == 404
    # Bob's installation listing does not include Alice's.
    listing = (await client.get("/api/github/installations", headers=headers_b)).json()
    assert [i["installation_id"] for i in listing["installations"]] == [222]
    # Alice still has her repository.
    assert (await client.get(f"/api/repositories/{repo_id}", headers=headers_a)).status_code == 200


@pytest.mark.asyncio
async def test_list_installation_repositories_marks_connected(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    _, installation, headers = await make_user_with_installation(
        db_session, github_id=1, username="u1", installation_id=111
    )

    async def fake_list(gh_installation_id):
        assert gh_installation_id == 111
        return [github_repo(1, "one"), github_repo(2, "two"), {"id": "malformed"}]

    async def fake_get(gh_installation_id, github_repo_id):
        return github_repo(github_repo_id, "one")

    monkeypatch.setattr(github_service, "list_installation_repositories", fake_list)
    monkeypatch.setattr(github_service, "get_repository_by_id", fake_get)

    connected = await client.post(
        "/api/repositories/connect",
        json={"installation_id": str(installation.id), "github_repo_id": 1},
        headers=headers,
    )
    resp = await client.get(
        f"/api/github/installations/{installation.id}/repositories", headers=headers
    )
    assert resp.status_code == 200
    repos = {r["github_repo_id"]: r for r in resp.json()}
    assert set(repos) == {1, 2}
    assert repos[1]["connected_repository_id"] == connected.json()["id"]
    assert repos[2]["connected_repository_id"] is None
