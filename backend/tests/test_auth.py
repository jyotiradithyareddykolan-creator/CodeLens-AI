"""OAuth sign-in flow tests. GitHub is mocked with httpx.MockTransport (no live calls)."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.models import GitHubInstallation, User

STATE = "known-state-value"


@pytest.fixture
def oauth_settings(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_CLIENT_ID", "Iv1.testclient")
    monkeypatch.setattr(settings, "GITHUB_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "BACKEND_URL", "http://localhost:8000")
    monkeypatch.setattr(settings, "FRONTEND_URL", "http://localhost:5173")


@pytest.fixture
def mock_github(monkeypatch):
    """Route outgoing httpx calls to a fake GitHub. Returns a dict to tweak responses."""
    state = {
        "token_status": 200,
        "token_body": {"access_token": "gho_user_token"},
        "profile": {"id": 4242, "login": "octo-dev", "email": None, "avatar_url": None},
        "installations_status": 200,
        "installations": [
            {"id": 9001, "account": {"login": "octo-dev", "type": "User"}, "permissions": {"contents": "read"}},
            {"id": 9002, "account": {"login": "octo-org", "type": "Organization"}},
            {"id": "bad", "account": {"login": "ignored"}},
        ],
        "requests": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        url = str(request.url)
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(state["token_status"], json=state["token_body"])
        if url == "https://api.github.com/user":
            return httpx.Response(200, json=state["profile"])
        if url.startswith("https://api.github.com/user/installations"):
            return httpx.Response(
                state["installations_status"],
                json={"total_count": len(state["installations"]), "installations": state["installations"]},
            )
        return httpx.Response(404, json={"message": "Not Found"})

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    return state


def fragment(resp: httpx.Response) -> dict:
    location = resp.headers["location"]
    assert location.startswith("http://localhost:5173/auth/callback#")
    return {k: v[0] for k, v in parse_qs(urlparse(location).fragment).items()}


async def callback(client: AsyncClient, state_param=STATE, cookie=STATE, code="abc"):
    params = {"code": code}
    if state_param is not None:
        params["state"] = state_param
    headers = {"Cookie": f"codelens_oauth_state={cookie}"} if cookie else {}
    return await client.get("/api/v1/auth/callback", params=params, headers=headers)


@pytest.mark.asyncio
async def test_login_url_and_redirect_set_state_cookie(client: AsyncClient, oauth_settings):
    resp = await client.get("/api/auth/github")
    assert resp.status_code == 200
    assert resp.json()["url"] == "http://localhost:8000/api/v1/auth/github/login"

    resp = await client.get("/api/v1/auth/github/login")
    assert resp.status_code == 302
    target = urlparse(resp.headers["location"])
    assert target.netloc == "github.com"
    query = parse_qs(target.query)
    assert query["client_id"] == ["Iv1.testclient"]
    assert query["redirect_uri"] == ["http://localhost:8000/api/v1/auth/callback"]
    cookie = resp.headers["set-cookie"]
    assert f"codelens_oauth_state={query['state'][0]}" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()


@pytest.mark.asyncio
async def test_sign_in_endpoints_report_missing_configuration(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_CLIENT_ID", None)
    assert (await client.get("/api/auth/github")).status_code == 503
    assert (await client.get("/api/auth/github/login")).status_code == 503


@pytest.mark.asyncio
async def test_callback_rejects_missing_or_mismatched_state(
    client: AsyncClient, oauth_settings, mock_github
):
    for kwargs in (
        {"state_param": None},
        {"cookie": None},
        {"state_param": "attacker-state"},
    ):
        resp = await callback(client, **kwargs)
        assert resp.status_code == 302
        assert fragment(resp) == {"error": "invalid_state"}
    # GitHub must never be contacted when state verification fails.
    assert mock_github["requests"] == []


@pytest.mark.asyncio
async def test_callback_creates_user_links_installations_and_returns_token_in_fragment(
    client: AsyncClient, db_session: AsyncSession, oauth_settings, mock_github
):
    resp = await callback(client)
    assert resp.status_code == 302
    frag = fragment(resp)
    assert "token" in frag and "error" not in frag
    # Token is only in the fragment, never in the query string.
    assert "token=" not in urlparse(resp.headers["location"]).query
    # State cookie is cleared.
    assert 'codelens_oauth_state=""' in resp.headers["set-cookie"] or "Max-Age=0" in resp.headers["set-cookie"]

    user = (await db_session.execute(select(User).where(User.github_id == 4242))).scalar_one()
    assert user.username == "octo-dev"
    assert user.encrypted_access_token is None  # the GitHub user token is not stored
    assert decode_access_token(frag["token"])["sub"] == str(user.id)

    installations = (
        await db_session.execute(select(GitHubInstallation).order_by(GitHubInstallation.installation_id))
    ).scalars().all()
    assert [(i.installation_id, i.account_type, i.user_id) for i in installations] == [
        (9001, "User", user.id),
        (9002, "Organization", user.id),
    ]

    # The session token works against the API.
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {frag['token']}"})
    assert me.json()["authenticated"] is True
    listing = await client.get(
        "/api/github/installations", headers={"Authorization": f"Bearer {frag['token']}"}
    )
    assert [i["installation_id"] for i in listing.json()["installations"]] == [9001, 9002]


@pytest.mark.asyncio
async def test_callback_does_not_steal_installation_linked_to_another_user(
    client: AsyncClient, db_session: AsyncSession, oauth_settings, mock_github
):
    other = User(github_id=1, username="first-owner")
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        GitHubInstallation(user_id=other.id, installation_id=9002, account_login="octo-org")
    )
    await db_session.commit()

    resp = await callback(client)
    assert "token" in fragment(resp)
    owner_id = (
        await db_session.execute(
            select(GitHubInstallation.user_id).where(GitHubInstallation.installation_id == 9002)
        )
    ).scalar_one()
    assert owner_id == other.id


@pytest.mark.asyncio
async def test_installation_listing_failure_does_not_block_sign_in(
    client: AsyncClient, db_session: AsyncSession, oauth_settings, mock_github
):
    mock_github["installations_status"] = 403
    resp = await callback(client)
    assert "token" in fragment(resp)
    assert (await db_session.execute(select(GitHubInstallation))).scalars().all() == []


@pytest.mark.asyncio
async def test_callback_handles_failed_code_exchange_and_denied_access(
    client: AsyncClient, db_session: AsyncSession, oauth_settings, mock_github
):
    mock_github["token_body"] = {"error": "bad_verification_code", "error_description": "secret detail"}
    resp = await callback(client)
    assert fragment(resp) == {"error": "token_exchange_failed"}
    assert "secret detail" not in resp.headers["location"]

    resp = await client.get(
        "/api/v1/auth/callback",
        params={"error": "access_denied", "state": STATE},
        headers={"Cookie": f"codelens_oauth_state={STATE}"},
    )
    assert fragment(resp) == {"error": "access_denied"}
    assert (await db_session.execute(select(User))).scalars().all() == []


@pytest.mark.asyncio
async def test_inactive_user_cannot_sign_in_or_use_token(
    client: AsyncClient, db_session: AsyncSession, oauth_settings, mock_github
):
    from app.core.security import create_access_token

    user = User(github_id=4242, username="octo-dev", is_active=False)
    db_session.add(user)
    await db_session.commit()

    resp = await callback(client)
    assert fragment(resp) == {"error": "account_disabled"}

    headers = {"Authorization": f"Bearer {create_access_token(subject=str(user.id))}"}
    assert (await client.get("/api/repositories", headers=headers)).status_code == 401
