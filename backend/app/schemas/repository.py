import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class RepositoryBase(BaseModel):
    name: str
    owner: str
    full_name: str
    default_branch: str = "main"
    is_private: bool = False
    description: Optional[str] = None


class RepositoryCreate(RepositoryBase):
    github_repo_id: int
    installation_id: uuid.UUID


class RepositoryResponse(RepositoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    github_repo_id: int
    is_indexed: bool
    indexing_status: str
    last_indexed_at: Optional[datetime] = None
    last_indexed_commit: Optional[str] = None
    total_files: int = 0
    total_chunks: int = 0
    created_at: datetime
    updated_at: datetime
