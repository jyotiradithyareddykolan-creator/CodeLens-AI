import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class InstallationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    installation_id: int
    account_login: str
    account_type: str
    created_at: datetime


class InstallationListResponse(BaseModel):
    installations: List[InstallationResponse]
    # Link to install the GitHub App on another account, when GITHUB_APP_SLUG is configured.
    install_url: Optional[str] = None


class InstallationRepositoryResponse(BaseModel):
    """A repository the installation can access, as reported by GitHub."""

    github_repo_id: int
    name: str
    owner: str
    full_name: str
    default_branch: str
    is_private: bool
    description: Optional[str] = None
    html_url: Optional[str] = None
    # CodeLens repository ID when this repository is already connected.
    connected_repository_id: Optional[uuid.UUID] = None
