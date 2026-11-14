import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class StartIndexingRequest(BaseModel):
    force_full: bool = Field(False, description="Re-process every file instead of only changed ones")


class IndexingJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    status: str
    trigger: str
    phase: Optional[str] = None
    progress: float
    commit_sha: Optional[str] = None
    base_commit_sha: Optional[str] = None
    force_full: bool
    attempts: int
    files_total: int
    files_changed: int
    files_processed: int
    files_indexed: int
    files_skipped: int
    files_failed: int
    files_deleted: int
    chunks_total: int
    embeddings_created: int
    embeddings_reused: int
    stats: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    next_retry_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class IndexingStatusResponse(BaseModel):
    repository_id: uuid.UUID
    indexing_status: str
    is_indexed: bool
    last_indexed_commit: Optional[str] = None
    last_indexed_at: Optional[datetime] = None
    total_files: int
    total_chunks: int
    embeddings_enabled: bool
    active_job: Optional[IndexingJobResponse] = None
    latest_job: Optional[IndexingJobResponse] = None


class IndexingStatsResponse(BaseModel):
    repository_id: uuid.UUID
    files_by_language: Dict[str, int]
    chunks_by_type: Dict[str, int]
    symbols_by_kind: Dict[str, int]
    files_with_parse_errors: int
    chunks_with_embeddings: int
    total_chunks: int
    dependencies_total: int
    dependencies_resolved: int


class IndexedFileResponse(BaseModel):
    id: uuid.UUID
    file_path: str
    language: str
    size_bytes: int
    line_count: int
    has_parse_errors: bool
    commit_sha: Optional[str] = None
    chunk_count: int


class ChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chunk_type: str
    symbol_name: Optional[str] = None
    qualified_name: Optional[str] = None
    parent_symbol: Optional[str] = None
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    token_count: int
    part_index: int
    part_count: int
    has_embedding: bool
    commit_sha: str
    content: str


class SymbolResponse(BaseModel):
    id: uuid.UUID
    name: str
    qualified_name: Optional[str] = None
    kind: str
    file_path: str
    start_line: int
    end_line: int
    signature: Optional[str] = None
    is_exported: bool


class SearchHitResponse(BaseModel):
    chunk: ChunkResponse
    file_path: str
    score: float


class SearchResponse(BaseModel):
    query: str
    results: List[SearchHitResponse]
