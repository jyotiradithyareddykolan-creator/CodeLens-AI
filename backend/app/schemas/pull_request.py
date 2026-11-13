import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class FindingSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[uuid.UUID] = None
    category: str
    severity: str
    title: str
    description: str
    evidence: Optional[str] = None
    file_path: str
    start_line: int
    end_line: int
    suggestion: Optional[str] = None
    confidence: float = 0.0


class PRAnalysisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    head_sha: str
    base_sha: str
    status: str
    summary: Optional[str] = None
    total_findings: int = 0
    completed_at: Optional[datetime] = None
    findings: List[FindingSchema] = Field(default_factory=list)


class PullRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    pr_number: int
    title: str
    description: Optional[str] = None
    author_username: str
    state: str
    base_branch: str
    head_branch: str
    base_sha: str
    head_sha: str
    created_at: datetime
    latest_analysis: Optional[PRAnalysisResponse] = None
