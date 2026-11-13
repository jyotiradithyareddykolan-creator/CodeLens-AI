import json
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import generate_github_signature
from app.models import (
    GitHubInstallation,
    PullRequest,
    PRAnalysis,
    Repository,
    User,
    WebhookEvent,
)


@pytest.mark.asyncio
async def test_webhook_missing_headers(client: AsyncClient):
    """Webhook request without required GitHub headers should return 400 or 401."""
    response = await client.post("/api/webhooks/github", json={"action": "test"})
    assert response.status_code in [400, 401]


@pytest.mark.asyncio
async def test_production_webhook_requires_configured_secret(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", None)
    response = await client.post(
        "/api/webhooks/github",
        content=b"{}",
        headers={
            "X-GitHub-Delivery": "production-secret-missing",
            "X-GitHub-Event": "ping",
        },
    )
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_webhook_hmac_signature_validation(client: AsyncClient, monkeypatch):
    """Webhook with configured secret must strictly validate HMAC SHA-256 signature."""
    secret = "test-webhook-secret-xyz"
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", secret)

    payload = {"action": "opened"}
    payload_bytes = json.dumps(payload).encode("utf-8")

    # 1. Invalid signature -> 401
    bad_headers = {
        "X-Hub-Signature-256": "sha256=invalidhexsignature0000000000000000",
        "X-GitHub-Delivery": "delivery-test-1",
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json",
    }
    resp_bad = await client.post("/api/webhooks/github", content=payload_bytes, headers=bad_headers)
    assert resp_bad.status_code == 401
    assert "Invalid X-Hub-Signature-256" in resp_bad.json()["detail"]

    # 2. Valid signature -> 200
    valid_sig = generate_github_signature(payload_bytes, secret)
    good_headers = {
        "X-Hub-Signature-256": valid_sig,
        "X-GitHub-Delivery": "delivery-test-1",
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json",
    }
    resp_good = await client.post("/api/webhooks/github", content=payload_bytes, headers=good_headers)
    assert resp_good.status_code == 200


@pytest.mark.asyncio
async def test_webhook_delivery_idempotency(client: AsyncClient, db_session: AsyncSession):
    """Repeated delivery IDs should be recognized as duplicate and ignored without duplicating work."""
    delivery_id = "unique-delivery-id-999"
    payload = {"action": "ping", "zen": "Keep it logically awesome."}
    payload_bytes = json.dumps(payload).encode("utf-8")

    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "ping",
        "Content-Type": "application/json",
    }

    # First delivery
    resp1 = await client.post("/api/webhooks/github", content=payload_bytes, headers=headers)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["status"] in ["processed", "ignored"]

    # Second delivery with the EXACT same delivery ID
    resp2 = await client.post("/api/webhooks/github", content=payload_bytes, headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["status"] == "ignored"
    assert data2["reason"] == "duplicate"


@pytest.mark.asyncio
async def test_pull_request_opened_webhook(client: AsyncClient, db_session: AsyncSession):
    """PR opened webhook should create PullRequest and PRAnalysis records in database."""
    # Pre-seed User, Installation, and Repository
    user = User(github_id=888, username="test-user")
    db_session.add(user)
    await db_session.flush()

    inst = GitHubInstallation(
        user_id=user.id,
        installation_id=777,
        account_login="test-org",
        account_type="Organization",
    )
    db_session.add(inst)
    await db_session.flush()

    repo = Repository(
        installation_id=inst.id,
        github_repo_id=1234567,
        name="codelens-test",
        owner="test-org",
        full_name="test-org/codelens-test",
        default_branch="main",
    )
    db_session.add(repo)
    await db_session.commit()

    # Webhook payload for pull_request.opened
    pr_payload = {
        "action": "opened",
        "number": 101,
        "pull_request": {
            "number": 101,
            "title": "Add secure password validation",
            "body": "Implements bcrypt verification.",
            "state": "open",
            "html_url": "https://github.com/test-org/codelens-test/pull/101",
            "user": {
                "login": "octocat-dev",
                "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
            },
            "base": {"ref": "main", "sha": "1111111111111111"},
            "head": {"ref": "feature/auth", "sha": "2222222222222222"},
        },
        "repository": {
            "id": 1234567,
            "full_name": "test-org/codelens-test",
        },
    }

    headers = {
        "X-GitHub-Delivery": "delivery-pr-101",
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json",
    }

    resp = await client.post(
        "/api/webhooks/github", content=json.dumps(pr_payload).encode("utf-8"), headers=headers
    )
    assert resp.status_code == 200

    # Verify database state
    pr_stmt = select(PullRequest).where(
        PullRequest.repository_id == repo.id, PullRequest.pr_number == 101
    )
    pr_res = await db_session.execute(pr_stmt)
    pr = pr_res.scalar_one_or_none()
    assert pr is not None
    assert pr.title == "Add secure password validation"
    assert pr.author_username == "octocat-dev"
    assert pr.head_sha == "2222222222222222"

    # Verify pending PRAnalysis created
    analysis_stmt = select(PRAnalysis).where(PRAnalysis.pull_request_id == pr.id)
    analysis_res = await db_session.execute(analysis_stmt)
    analysis = analysis_res.scalar_one_or_none()
    assert analysis is not None
    assert analysis.status == "pending"
    assert analysis.head_sha == "2222222222222222"
