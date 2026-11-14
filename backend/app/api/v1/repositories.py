import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import require_current_user
from app.api.v1.github import (
    get_owned_installation,
    github_upstream_error,
    repository_from_github,
)
from app.database.session import get_async_db
from app.indexing.store import delete_repository_index
from app.models import (
    GitHubInstallation,
    IndexedFile,
    Repository,
    RepositoryBranch,
    User,
)
from app.schemas.repository import RepositoryResponse
from app.services.github_service import GitHubServiceError, github_service

# Every endpoint here requires a signed-in user and only sees repositories that belong
# to that user's GitHub App installations.
router = APIRouter(
    prefix="/repositories",
    tags=["Repositories"],
    dependencies=[Depends(require_current_user)],
)


class ConnectRepositoryRequest(BaseModel):
    """Connect a repository granted to one of the user's GitHub App installations.

    Repository metadata (name, owner, default branch, visibility) is fetched from GitHub
    rather than trusted from the client.
    """

    installation_id: uuid.UUID
    github_repo_id: int = Field(..., gt=0)


async def get_owned_repository(
    db: AsyncSession, user: User, repo_id: uuid.UUID
) -> Repository:
    """Return the repository if it belongs to one of ``user``'s installations, else 404."""
    result = await db.execute(
        select(Repository)
        .join(GitHubInstallation, Repository.installation_id == GitHubInstallation.id)
        .where(Repository.id == repo_id, GitHubInstallation.user_id == user.id)
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repo


@router.get("", response_model=List[RepositoryResponse], summary="List Connected Repositories")
async def list_repositories(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Lists the current user's connected GitHub repositories."""
    stmt = (
        select(Repository)
        .join(GitHubInstallation, Repository.installation_id == GitHubInstallation.id)
        .where(GitHubInstallation.user_id == current_user.id)
        .order_by(Repository.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post(
    "/connect",
    response_model=RepositoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Connect Repository",
)
async def connect_repository(
    payload: ConnectRepositoryRequest,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    """
    Connects a GitHub repository to CodeLens AI for indexing and PR analysis.
    Access is read-only; CodeLens never writes to the repository.
    """
    installation = await get_owned_installation(db, current_user, payload.installation_id)

    # Already connected? Return it (200) if it is under this installation.
    result = await db.execute(
        select(Repository).where(Repository.github_repo_id == payload.github_repo_id)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        if existing.installation_id != installation.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This repository is already connected through another installation.",
            )
        response.status_code = status.HTTP_200_OK
        return existing

    # The installation token can only see repositories granted to the installation,
    # so a successful lookup proves this installation may access the repository.
    try:
        github_repo = await github_service.get_repository_by_id(
            installation.installation_id, payload.github_repo_id
        )
    except GitHubServiceError as exc:
        raise github_upstream_error(exc)

    fields = repository_from_github(github_repo) if github_repo else None
    if fields is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository is not accessible to this GitHub App installation.",
        )

    new_repo = Repository(
        installation_id=installation.id,
        github_repo_id=fields["github_repo_id"],
        name=fields["name"],
        owner=fields["owner"],
        full_name=fields["full_name"],
        default_branch=fields["default_branch"],
        is_private=fields["is_private"],
        html_url=fields["html_url"] or f"https://github.com/{fields['full_name']}",
        description=fields["description"],
        is_indexed=False,
        indexing_status="unindexed",
    )
    db.add(new_repo)
    await db.flush()

    db.add(
        RepositoryBranch(
            repository_id=new_repo.id,
            name=fields["default_branch"],
            is_default=True,
        )
    )

    await db.commit()
    await db.refresh(new_repo)
    return new_repo


@router.get("/{repo_id}", response_model=RepositoryResponse, summary="Get Repository Details")
async def get_repository_details(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    """Retrieves repository metadata, indexing state, and summary statistics."""
    return await get_owned_repository(db, current_user, repo_id)


@router.delete("/{repo_id}", status_code=status.HTTP_200_OK, summary="Disconnect Repository")
async def disconnect_repository(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    """
    Disconnects a repository from CodeLens AI.
    Removes indexed files, chunks, symbols and reports without touching the GitHub repository.
    """
    repo = await get_owned_repository(db, current_user, repo_id)
    await delete_repository_index(db, repo.id)
    await db.delete(repo)
    await db.commit()
    return {"status": "disconnected", "repository_id": str(repo_id)}


@router.get("/{repo_id}/status", summary="Get Repository Indexing Status")
async def get_repository_status(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    """Returns indexing state, total files, and last indexed commit."""
    repo = await get_owned_repository(db, current_user, repo_id)
    return {
        "repository_id": str(repo.id),
        "is_indexed": repo.is_indexed,
        "indexing_status": repo.indexing_status,
        "last_indexed_at": repo.last_indexed_at,
        "last_indexed_commit": repo.last_indexed_commit,
        "total_files": repo.total_files,
        "total_chunks": repo.total_chunks,
    }


@router.get("/{repo_id}/branches", summary="List Repository Branches")
async def get_repository_branches(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    """Returns tracked branches for a repository."""
    await get_owned_repository(db, current_user, repo_id)
    result = await db.execute(
        select(RepositoryBranch).where(RepositoryBranch.repository_id == repo_id)
    )
    return [
        {
            "id": str(b.id),
            "name": b.name,
            "latest_commit_sha": b.latest_commit_sha,
            "is_default": b.is_default,
        }
        for b in result.scalars().all()
    ]


@router.get("/{repo_id}/files", summary="List Indexed Repository Files")
async def get_repository_files(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Returns indexed files for code browsing."""
    await get_owned_repository(db, current_user, repo_id)
    stmt = (
        select(IndexedFile)
        .where(IndexedFile.repository_id == repo_id, IndexedFile.is_deleted.is_(False))
        .order_by(IndexedFile.file_path)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [
        {
            "id": str(f.id),
            "file_path": f.file_path,
            "language": f.language,
            "size_bytes": f.size_bytes,
        }
        for f in result.scalars().all()
    ]
