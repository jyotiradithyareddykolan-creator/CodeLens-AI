import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import get_async_db
from app.schemas.health import HealthResponse, ReadyResponse, ServiceHealth

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------- readiness

READY_CHECK_TIMEOUT_SECONDS = 3.0
WORKER_PING_TIMEOUT_SECONDS = 1.0

# Capabilities as implemented today (static facts, not health). Shown so the dashboard
# never implies that keyword or hybrid retrieval exists before Phase 4 delivers it.
RETRIEVAL_FEATURES = {
    "vector_search": "implemented",
    "keyword_search": "planned",
    "hybrid_search": "planned",
}


async def _timed(check: Callable[[], Awaitable[Dict[str, Any]]], timeout: float) -> Dict[str, Any]:
    """Run one dependency check. Never returns exception text, URLs or credentials."""
    started = time.perf_counter()
    try:
        details = await asyncio.wait_for(check(), timeout=timeout)
    except asyncio.TimeoutError:
        return {"status": "unavailable", "reason": "timeout"}
    except Exception as exc:  # connection refused, auth failure, DNS, driver errors, ...
        logger.warning("Readiness check failed: %s", type(exc).__name__)
        return {"status": "unavailable", "reason": "connection_failed"}
    return {"status": "ok", "latency_ms": round((time.perf_counter() - started) * 1000, 1), **details}


async def _check_database(db: AsyncSession) -> Dict[str, Any]:
    if (await db.execute(text("SELECT 1"))).scalar() != 1:
        raise RuntimeError("unexpected result")
    engine = db.get_bind().dialect.name
    details: Dict[str, Any] = {"engine": engine, "vector_extension": None}
    if engine == "postgresql":
        details["vector_extension"] = (await db.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )).scalar()
    return details


async def _check_redis() -> Dict[str, Any]:
    client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    try:
        if not await client.ping():
            raise RuntimeError("no pong")
    finally:
        await client.aclose()
    return {}


def _ping_workers() -> int:
    """Number of Celery workers that answer a broadcast ping (blocking call)."""
    from app.workers.celery_app import celery_app

    replies = celery_app.control.ping(timeout=WORKER_PING_TIMEOUT_SECONDS) or []
    return len(replies)


async def _check_workers() -> Dict[str, Any]:
    count = await asyncio.to_thread(_ping_workers)
    return {"count": count}


@router.get("/ready", response_model=ReadyResponse, summary="Readiness Probe")
async def readiness_check(
    db: AsyncSession = Depends(get_async_db),
    details: bool = Query(False, description="Also ping Celery workers (adds about 1 second)"),
):
    """Readiness probe: real PostgreSQL and Redis connectivity, each with a timeout.

    ``ready`` (and HTTP 200 vs 503) depends only on PostgreSQL and Redis. With
    ``details=true`` the response also reports how many Celery workers answer a ping;
    that is informational and does not affect readiness.
    """
    database, redis_status = await asyncio.gather(
        _timed(lambda: _check_database(db), READY_CHECK_TIMEOUT_SECONDS),
        _timed(_check_redis, READY_CHECK_TIMEOUT_SECONDS),
    )
    services: Dict[str, Any] = {"database": database, "redis": redis_status}
    if details:
        if redis_status["status"] == "ok":
            workers = await _timed(_check_workers, WORKER_PING_TIMEOUT_SECONDS + 2.0)
            if workers["status"] == "ok" and workers.get("count", 0) == 0:
                workers = {"status": "unavailable", "reason": "no_workers", "count": 0}
        else:
            workers = {"status": "unavailable", "reason": "broker_unavailable"}
        services["workers"] = workers

    is_ready = database["status"] == "ok" and redis_status["status"] == "ok"
    return JSONResponse(
        status_code=status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "ready": is_ready,
            "checks": {"database": database["status"] == "ok", "redis": redis_status["status"] == "ok"},
            "services": services,
            "features": {"retrieval": RETRIEVAL_FEATURES},
        },
    )
