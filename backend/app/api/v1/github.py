import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import require_current_user
from app.core.config import settings
from app.database.session import get_async_db
from app.models import GitHubInstallation, Repository, User
from app.schemas.github import (
    InstallationListResponse,
    InstallationRepositoryResponse,
)
from app.services.github_service import GitHubServiceError, github_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["GitHub"])


async def get_owned_installation(
    db: AsyncSession, user: User, installation_uuid: uuid.UUID
) -> GitHubInstallation:
    """Return the installation if it belongs to ``user``; 404 otherwise (no existence leak)."""
    result = await db.execute(
        select(GitHubInstallation).where(
            GitHubInstallation.id == installation_uuid,
            GitHubInstallation.user_id == user.id,
        )
    )
    installation = result.scalar_one_or_none()
    if installation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Installation not found")
    return installation


def github_upstream_error(exc: GitHubServiceError) -> HTTPException:
    logger.warning("GitHub API error: %s", exc)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="GitHub request failed. Check the GitHub App configuration and try again.",
    )


def repository_from_github(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate and normalise a GitHub repository object; None if malformed."""
    repo_id = data.get("id")
    name = data.get("name")
    full_name = data.get("full_name")
    owner = (data.get("owner") or {}).get("login")
    if not isinstance(repo_id, int) or isinstance(repo_id, bool) or repo_id <= 0:
        return None
    if not all(isinstance(v, str) and v for v in (name, full_name, owner)):
        return None
    return {
        "github_repo_id": repo_id,
        "name": name[:255],
        "owner": owner[:255],
        "full_name": full_name[:512],
        "default_branch": str(data.get("default_branch") or "main")[:255],
        "is_private": bool(data.get("private", False)),
        "description": data.get("description"),
        "html_url": data.get("html_url"),
    }


@router.get(
    "/installations",
    response_model=InstallationListResponse,
    summary="List the current user's GitHub App installations",
)
async def list_installations(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    result = await db.execute(
        select(GitHubInstallation)
        .where(GitHubInstallation.user_id == current_user.id)
        .order_by(GitHubInstallation.account_login)
    )
    install_url = (
        f"https://github.com/apps/{settings.GITHUB_APP_SLUG}/installations/new"
        if settings.GITHUB_APP_SLUG
        else None
    )
    return InstallationListResponse(
        installations=list(result.scalars().all()), install_url=install_url
    )


@router.get(
    "/installations/{installation_id}/repositories",
    response_model=List[InstallationRepositoryResponse],
    summary="List repositories granted to an installation",
)
async def list_installation_repositories(
    installation_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    installation = await get_owned_installation(db, current_user, installation_id)
    try:
        raw_repos = await github_service.list_installation_repositories(
            installation.installation_id
        )
    except GitHubServiceError as exc:
        raise github_upstream_error(exc)

    repos = [r for r in (repository_from_github(d) for d in raw_repos) if r is not None]
    connected: Dict[int, uuid.UUID] = {}
    if repos:
        result = await db.execute(
            select(Repository.github_repo_id, Repository.id).where(
                Repository.github_repo_id.in_([r["github_repo_id"] for r in repos])
            )
        )
        connected = {gh_id: repo_id for gh_id, repo_id in result.all()}

    return [
        InstallationRepositoryResponse(
            **r, connected_repository_id=connected.get(r["github_repo_id"])
        )
        for r in repos
    ]
