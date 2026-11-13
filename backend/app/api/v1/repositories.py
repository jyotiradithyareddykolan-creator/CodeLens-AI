import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_async_db
from app.models import (
    GitHubInstallation,
    IndexedFile,
    Repository,
    RepositoryBranch,
)
from app.schemas.repository import RepositoryResponse
from app.services.github_service import github_service

router = APIRouter(prefix="/repositories", tags=["Repositories"])


class ConnectRepositoryRequest(BaseModel):
    github_repo_id: int
    full_name: str
    name: str
    owner: str
    default_branch: str = "main"
    is_private: bool = False
    html_url: Optional[str] = None
    description: Optional[str] = None
    installation_id: Optional[uuid.UUID] = None


@router.get("", response_model=List[RepositoryResponse], summary="List Connected Repositories")
async def list_repositories(
    db: AsyncSession = Depends(get_async_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Lists all connected GitHub repositories in CodeLens AI."""
    stmt = (
        select(Repository)
        .order_by(Repository.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    repos = result.scalars().all()
    return repos


@router.post("/connect", response_model=RepositoryResponse, status_code=status.HTTP_201_CREATED, summary="Connect Repository")
async def connect_repository(
    payload: ConnectRepositoryRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Connects a GitHub repository to CodeLens AI for indexing and PR analysis.
    Initially establishes read-only integration.
    """
    # 1. Check if repository is already connected
    stmt = select(Repository).where(Repository.github_repo_id == payload.github_repo_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    # 2. Find or establish default installation if not explicitly passed
    installation_id = payload.installation_id
    if installation_id:
        installation_stmt = select(GitHubInstallation).where(
            GitHubInstallation.id == installation_id
        )
        installation_result = await db.execute(installation_stmt)
        if not installation_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The supplied GitHub App installation_id does not exist.",
            )
    else:
        inst_stmt = select(GitHubInstallation).limit(1)
        inst_res = await db.execute(inst_stmt)
        default_inst = inst_res.scalar_one_or_none()

        if not default_inst:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A valid GitHub App installation_id is required to connect a repository.",
            )

        installation_id = default_inst.id

    # 3. Create repository record
    new_repo = Repository(
        installation_id=installation_id,
        github_repo_id=payload.github_repo_id,
        name=payload.name,
        owner=payload.owner,
        full_name=payload.full_name,
        default_branch=payload.default_branch,
        is_private=payload.is_private,
        html_url=payload.html_url or f"https://github.com/{payload.full_name}",
        description=payload.description,
        is_indexed=False,
        indexing_status="unindexed",
    )
    db.add(new_repo)
    await db.flush()

    # 4. Create default branch record
    default_branch_record = RepositoryBranch(
        repository_id=new_repo.id,
        name=payload.default_branch,
        is_default=True,
    )
    db.add(default_branch_record)

    await db.commit()
    await db.refresh(new_repo)

    return new_repo


@router.get("/{repo_id}", response_model=RepositoryResponse, summary="Get Repository Details")
async def get_repository_details(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Retrieves repository metadata, indexing state, and summary statistics."""
    stmt = select(Repository).where(Repository.id == repo_id)
    result = await db.execute(stmt)
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )
    return repo


@router.delete("/{repo_id}", status_code=status.HTTP_200_OK, summary="Disconnect Repository")
async def disconnect_repository(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Disconnects a repository from CodeLens AI.
    Removes indexed files, chunks, AST symbols, and reports without touching the GitHub repository.
    """
    stmt = select(Repository).where(Repository.id == repo_id)
    result = await db.execute(stmt)
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    await db.delete(repo)
    await db.commit()

    return {"status": "disconnected", "repository_id": str(repo_id)}


@router.get("/{repo_id}/status", summary="Get Repository Indexing Status")
async def get_repository_status(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Returns indexing state, total files, and last indexed commit."""
    stmt = select(Repository).where(Repository.id == repo_id)
    result = await db.execute(stmt)
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

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
):
    """Returns tracked branches for a repository."""
    stmt = select(RepositoryBranch).where(RepositoryBranch.repository_id == repo_id)
    result = await db.execute(stmt)
    branches = result.scalars().all()
    return branches


@router.get("/{repo_id}/files", summary="List Indexed Repository Files")
async def get_repository_files(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Returns indexed files for code browsing."""
    stmt = (
        select(IndexedFile)
        .where(IndexedFile.repository_id == repo_id, IndexedFile.is_deleted == False)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    files = result.scalars().all()
    return [
        {
            "id": str(f.id),
            "file_path": f.file_path,
            "language": f.language,
            "size_bytes": f.size_bytes,
        }
        for f in files
    ]
