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


@pytest.mark.asyncio
async def test_readiness_endpoint(client: AsyncClient):
    # In test environment with overridden SQLite database
    response = await client.get("/ready")
    assert response.status_code in [200, 503]
    data = response.json()
    assert "ready" in data
    assert "checks" in data
    assert "database" in data["checks"]
    # Database check should pass against SQLite
    assert data["checks"]["database"] is True
