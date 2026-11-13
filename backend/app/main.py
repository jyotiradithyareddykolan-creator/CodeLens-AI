from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.api import api_router
from app.api.v1.health import health_check, readiness_check
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    print(f"[STARTUP] Starting {settings.PROJECT_NAME} v{settings.VERSION} [{settings.ENVIRONMENT}]")
    try:
        from app.database.session import async_engine, Base
        import app.models  # ensure all models are imported
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("[STARTUP] Database tables verified & initialized.")
    except Exception as e:
        print(f"[STARTUP] Database initialization notice: {e}")
    yield
    # Shutdown actions
    print(f"[SHUTDOWN] Shutting down {settings.PROJECT_NAME}")


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
