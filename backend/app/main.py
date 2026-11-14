import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.api import api_router
from app.api.v1.health import health_check, readiness_check
from app.core.config import settings

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    # The schema is owned by Alembic: run `alembic upgrade head` before starting the API
    # (docker-compose does this automatically). Tables are intentionally not created here.
    logger.info("Starting %s v%s [%s]", settings.PROJECT_NAME, settings.VERSION, settings.ENVIRONMENT)
    yield
    logger.info("Shutting down %s", settings.PROJECT_NAME)


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Full-Stack AI Codebase Intelligence & Pull Request Analyzer Platform",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Top-level Kubernetes & Docker health endpoints
app.add_api_route("/health", health_check, methods=["GET"], tags=["Health"])
app.add_api_route("/ready", readiness_check, methods=["GET"], tags=["Health"])

# Mount API routes under /api and /api/v1
app.include_router(api_router, prefix="/api")
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/", tags=["Root"])
def read_root():
    return {
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "online",
        "docs": f"{settings.API_V1_STR}/docs",
    }
