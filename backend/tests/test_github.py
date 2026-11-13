import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, decode_access_token
from app.models import GitHubInstallation, User


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
async def test_repository_connect_and_list_endpoints(client: AsyncClient, db_session: AsyncSession):
    """Test connecting a new repository and querying repository endpoints."""
    # Repository connections must be associated with a real GitHub App
    # installation; the old implicit demo installation is intentionally gone.
    user = User(github_id=9988, username="connected-user")
    db_session.add(user)
    await db_session.flush()
    installation = GitHubInstallation(
        user_id=user.id,
        installation_id=778899,
        account_login="codelens-org",
        account_type="Organization",
        permissions={"contents": "read", "pull_requests": "read"},
    )
    db_session.add(installation)
    await db_session.flush()
    installation_id = str(installation.id)

    # 1. Connect repository
    connect_payload = {
        "github_repo_id": 998877,
        "name": "sample-service",
        "owner": "codelens-org",
        "full_name": "codelens-org/sample-service",
        "default_branch": "main",
        "is_private": False,
        "description": "Production microservice for testing CodeLens AI",
        "installation_id": installation_id,
    }

    create_resp = await client.post("/api/repositories/connect", json=connect_payload)
    assert create_resp.status_code == 201
    repo_data = create_resp.json()
    repo_id = repo_data["id"]
    assert repo_data["full_name"] == "codelens-org/sample-service"
    assert repo_data["is_indexed"] is False

    # 2. List repositories
    list_resp = await client.get("/api/repositories")
    assert list_resp.status_code == 200
    repos = list_resp.json()
    assert len(repos) >= 1
    assert any(r["id"] == repo_id for r in repos)

    # 3. Get repository details
    detail_resp = await client.get(f"/api/repositories/{repo_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["name"] == "sample-service"

    # 4. Get repository indexing status
    status_resp = await client.get(f"/api/repositories/{repo_id}/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["indexing_status"] == "unindexed"

    # 5. List branches
    branches_resp = await client.get(f"/api/repositories/{repo_id}/branches")
    assert branches_resp.status_code == 200
    branches = branches_resp.json()
    assert len(branches) >= 1
    assert branches[0]["name"] == "main"

    # 6. Disconnect repository
    delete_resp = await client.delete(f"/api/repositories/{repo_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["status"] == "disconnected"

    # Verify repository is no longer found
    check_resp = await client.get(f"/api/repositories/{repo_id}")
    assert check_resp.status_code == 404
