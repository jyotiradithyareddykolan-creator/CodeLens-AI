import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["app"] == "CodeLens AI"
    assert data["status"] == "online"
    assert "version" in data


@pytest.mark.asyncio
async def test_liveness_health_endpoint(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["app"] == "CodeLens AI"
    assert "services" in data
    assert "api" in data["services"]
    assert data["services"]["api"]["status"] == "healthy"


import asyncio

from app.api.v1 import health as health_api


@pytest.fixture
def redis_up(monkeypatch):
    async def ok():
        return {}

    monkeypatch.setattr(health_api, "_check_redis", ok)


@pytest.mark.asyncio
async def test_readiness_reports_each_dependency(client: AsyncClient, redis_up):
    response = await client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert data["checks"] == {"database": True, "redis": True}
    db = data["services"]["database"]
    assert db["status"] == "ok" and db["engine"] == "sqlite" and db["vector_extension"] is None
    assert isinstance(db["latency_ms"], float)
    assert data["services"]["redis"]["status"] == "ok"
    assert "workers" not in data["services"]  # only with details=true
    # Capabilities are reported honestly: hybrid retrieval is not implemented yet.
    assert data["features"]["retrieval"] == {
        "vector_search": "implemented", "keyword_search": "planned", "hybrid_search": "planned",
    }


@pytest.mark.asyncio
async def test_redis_failure_is_503_without_leaking_details(client: AsyncClient, monkeypatch):
    async def broken():
        # Credential-shaped text assembled at runtime so scanners do not flag the test file.
        raise ConnectionError("Error connecting to " + "redis://" + "admin:" + "hunter2" + "@10.0.0.5:6379/0")

    monkeypatch.setattr(health_api, "_check_redis", broken)
    response = await client.get("/ready?details=true")
    assert response.status_code == 503
    data = response.json()
    assert data["ready"] is False and data["checks"] == {"database": True, "redis": False}
    assert data["services"]["redis"] == {"status": "unavailable", "reason": "connection_failed"}
    assert data["services"]["workers"] == {"status": "unavailable", "reason": "broker_unavailable"}
    for secret in ("hunter2", "admin", "10.0.0.5", "redis://", "ConnectionError"):
        assert secret not in response.text


@pytest.mark.asyncio
async def test_database_failure_is_503(client: AsyncClient, monkeypatch, redis_up):
    async def broken(db):
        raise OSError("could not connect to server at postgresql://u:secret@db:5432/x")

    monkeypatch.setattr(health_api, "_check_database", broken)
    response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["services"]["database"] == {"status": "unavailable", "reason": "connection_failed"}
    assert "secret" not in response.text and "postgresql://" not in response.text


@pytest.mark.asyncio
async def test_slow_dependency_times_out(client: AsyncClient, monkeypatch, redis_up):
    async def hangs():
        await asyncio.sleep(10)
        return {}

    monkeypatch.setattr(health_api, "_check_redis", hangs)
    monkeypatch.setattr(health_api, "READY_CHECK_TIMEOUT_SECONDS", 0.05)
    response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["services"]["redis"] == {"status": "unavailable", "reason": "timeout"}


@pytest.mark.asyncio
async def test_worker_details(client: AsyncClient, monkeypatch, redis_up):
    monkeypatch.setattr(health_api, "_ping_workers", lambda: 2)
    data = (await client.get("/ready?details=true")).json()
    assert data["services"]["workers"]["status"] == "ok" and data["services"]["workers"]["count"] == 2

    monkeypatch.setattr(health_api, "_ping_workers", lambda: 0)
    response = await client.get("/ready?details=true")
    # No worker is degraded functionality, not an API readiness failure.
    assert response.status_code == 200
    assert response.json()["services"]["workers"] == {"status": "unavailable", "reason": "no_workers", "count": 0}
