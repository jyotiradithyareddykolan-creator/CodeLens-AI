from fastapi import APIRouter
from app.api.v1.auth import router as auth_router
from app.api.v1.github import router as github_router
from app.api.v1.health import router as health_router
from app.api.v1.repositories import router as repositories_router
from app.api.v1.webhooks import router as webhooks_router

api_router = APIRouter()

# Mount API V1 routers
api_router.include_router(health_router, prefix="/system", tags=["System"])
api_router.include_router(auth_router)
api_router.include_router(github_router)
api_router.include_router(webhooks_router)
api_router.include_router(repositories_router)
