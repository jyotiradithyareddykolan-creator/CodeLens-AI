import redis.asyncio as aioredis
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import get_async_db
from app.schemas.health import HealthResponse, ReadyResponse, ServiceHealth

router = APIRouter(tags=["System & Health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness Probe")
async def health_check():
    """Liveness probe: returns 200 if the API process is alive and responding."""
    return HealthResponse(
        status="healthy",
        app=settings.PROJECT_NAME,
        version=settings.VERSION,
        timestamp=datetime.now(timezone.utc),
        environment=settings.ENVIRONMENT,
        services={
            "api": ServiceHealth(status="healthy", details="FastAPI server is running normally")
        }
    )


@router.get("/ready", response_model=ReadyResponse, summary="Readiness Probe")
async def readiness_check(db: AsyncSession = Depends(get_async_db)):
    """Readiness probe: validates connectivity to PostgreSQL and Redis backing services."""
    checks = {}
    is_ready = True

    # 1. Check PostgreSQL Database
    try:
        result = await db.execute(text("SELECT 1"))
        scalar = result.scalar()
        if scalar == 1:
            checks["database"] = True
        else:
            checks["database"] = False
            is_ready = False
    except Exception as e:
        checks["database"] = False
        is_ready = False

    # 2. Check Redis Broker
    try:
        r = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        pong = await r.ping()
        await r.aclose()
        checks["redis"] = bool(pong)
        if not checks["redis"]:
            is_ready = False
    except Exception as e:
        checks["redis"] = False
        is_ready = False

    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=status_code,
        content={"ready": is_ready, "checks": checks}
    )
