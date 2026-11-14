"""Indexing APIs. Every endpoint requires sign-in and repository ownership (404 otherwise)."""

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import require_current_user
from app.api.v1.repositories import get_owned_repository
from app.core.config import settings
from app.database.session import get_async_db
from app.indexing import jobs as job_service
from app.indexing.embeddings import EmbeddingError, GeminiEmbedder
from app.indexing.store import search_similar_chunks
from app.models import ChunkEmbedding, CodeChunk, CodeSymbol, Dependency, IndexedFile, IndexingJob, User
from app.schemas.indexing import (
    ChunkResponse,
    IndexedFileResponse,
    IndexingJobResponse,
    IndexingStatsResponse,
    IndexingStatusResponse,
    SearchHitResponse,
    SearchResponse,
    StartIndexingRequest,
    SymbolResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/repositories", tags=["Indexing"], dependencies=[Depends(require_current_user)])


def _chunk_response(chunk: CodeChunk) -> ChunkResponse:
    return ChunkResponse(
        id=chunk.id, chunk_type=chunk.symbol_type, symbol_name=chunk.symbol_name,
        qualified_name=chunk.qualified_name, parent_symbol=chunk.parent_symbol,
        start_line=chunk.start_line, end_line=chunk.end_line, start_byte=chunk.start_byte,
        end_byte=chunk.end_byte, token_count=chunk.token_count, part_index=chunk.part_index,
        part_count=chunk.part_count, has_embedding=chunk.embedding_key is not None,
        commit_sha=chunk.commit_sha, content=chunk.content,
    )


async def _start(db: AsyncSession, user: User, repo_id: uuid.UUID, response: Response,
                 force_full: bool, trigger: str) -> IndexingJobResponse:
    repo = await get_owned_repository(db, user, repo_id)
    job, created = await job_service.request_indexing(db, repo, trigger=trigger, force_full=force_full)
    if not created:
        response.status_code = status.HTTP_200_OK
        if not job_service.needs_redispatch(job):
            return IndexingJobResponse.model_validate(job)
        # Re-send a queued job whose task message may have been lost.
    try:
        job.celery_task_id = job_service.dispatch_index_job(job.id)
        await db.commit()
    except job_service.IndexingDispatchError as exc:
        await job_service.mark_dispatch_failed(db, job, str(exc))
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    await db.refresh(job)
    return IndexingJobResponse.model_validate(job)


@router.post("/{repo_id}/index", response_model=IndexingJobResponse, status_code=status.HTTP_202_ACCEPTED,
             summary="Start indexing (returns the active job if one is already running)")
async def start_indexing(
    repo_id: uuid.UUID,
    response: Response,
    payload: Optional[StartIndexingRequest] = None,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    force_full = payload.force_full if payload else False
    return await _start(db, current_user, repo_id, response, force_full, "manual")


@router.post("/{repo_id}/reindex", response_model=IndexingJobResponse, status_code=status.HTTP_202_ACCEPTED,
             summary="Re-index every file at the latest commit")
async def reindex(
    repo_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    return await _start(db, current_user, repo_id, response, True, "manual")


@router.post("/{repo_id}/index/jobs/{job_id}/retry", response_model=IndexingJobResponse,
             status_code=status.HTTP_202_ACCEPTED, summary="Retry a failed indexing job")
async def retry_job(
    repo_id: uuid.UUID,
    job_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    repo = await get_owned_repository(db, current_user, repo_id)
    job = (await db.execute(
        select(IndexingJob).where(IndexingJob.id == job_id, IndexingJob.repository_id == repo.id)
    )).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Indexing job not found")
    if job.status != "failed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only failed jobs can be retried")
    return await _start(db, current_user, repo_id, response, job.force_full, "retry")


@router.get("/{repo_id}/index/status", response_model=IndexingStatusResponse, summary="Indexing status")
async def indexing_status(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    repo = await get_owned_repository(db, current_user, repo_id)
    active = await job_service.active_job(db, repo.id)
    latest = await job_service.latest_job(db, repo.id)
    return IndexingStatusResponse(
        repository_id=repo.id, indexing_status=repo.indexing_status, is_indexed=repo.is_indexed,
        last_indexed_commit=repo.last_indexed_commit, last_indexed_at=repo.last_indexed_at,
        total_files=repo.total_files, total_chunks=repo.total_chunks,
        embeddings_enabled=bool(settings.GEMINI_API_KEY),
        active_job=IndexingJobResponse.model_validate(active) if active else None,
        latest_job=IndexingJobResponse.model_validate(latest) if latest else None,
    )


@router.get("/{repo_id}/index/jobs", response_model=List[IndexingJobResponse], summary="Indexing job history")
async def list_jobs(
    repo_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    repo = await get_owned_repository(db, current_user, repo_id)
    rows = (await db.execute(
        select(IndexingJob).where(IndexingJob.repository_id == repo.id)
        .order_by(IndexingJob.created_at.desc()).limit(limit)
    )).scalars().all()
    return [IndexingJobResponse.model_validate(j) for j in rows]


@router.get("/{repo_id}/index/stats", response_model=IndexingStatsResponse, summary="Indexed content statistics")
async def indexing_stats(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    repo = await get_owned_repository(db, current_user, repo_id)

    async def grouped(column, model, where):
        rows = await db.execute(select(column, func.count()).select_from(model).where(where).group_by(column))
        return {key: n for key, n in rows.all()}

    async def scalar(statement):
        return (await db.execute(statement)).scalar_one()

    return IndexingStatsResponse(
        repository_id=repo.id,
        files_by_language=await grouped(IndexedFile.language, IndexedFile, and_(
            IndexedFile.repository_id == repo.id, IndexedFile.skip_reason.is_(None))),
        chunks_by_type=await grouped(CodeChunk.symbol_type, CodeChunk, CodeChunk.repository_id == repo.id),
        symbols_by_kind=await grouped(CodeSymbol.symbol_type, CodeSymbol, CodeSymbol.repository_id == repo.id),
        files_with_parse_errors=await scalar(select(func.count()).select_from(IndexedFile).where(
            IndexedFile.repository_id == repo.id, IndexedFile.has_parse_errors.is_(True))),
        chunks_with_embeddings=await scalar(select(func.count()).select_from(CodeChunk).where(
            CodeChunk.repository_id == repo.id, CodeChunk.embedding_key.in_(
                select(ChunkEmbedding.embedding_key).where(ChunkEmbedding.repository_id == repo.id)))),
        total_chunks=await scalar(select(func.count()).select_from(CodeChunk).where(CodeChunk.repository_id == repo.id)),
        dependencies_total=await scalar(select(func.count()).select_from(Dependency).where(
            Dependency.repository_id == repo.id)),
        dependencies_resolved=await scalar(select(func.count()).select_from(Dependency).where(
            Dependency.repository_id == repo.id, Dependency.target_file_id.is_not(None))),
    )


@router.get("/{repo_id}/index/files", response_model=List[IndexedFileResponse], summary="Indexed files")
async def indexed_files(
    repo_id: uuid.UUID,
    q: Optional[str] = Query(None, max_length=200, description="Filter by path substring"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    repo = await get_owned_repository(db, current_user, repo_id)
    chunk_counts = (
        select(CodeChunk.file_id, func.count().label("n")).where(CodeChunk.repository_id == repo.id)
        .group_by(CodeChunk.file_id).subquery()
    )
    statement = (
        select(IndexedFile, func.coalesce(chunk_counts.c.n, 0))
        .outerjoin(chunk_counts, chunk_counts.c.file_id == IndexedFile.id)
        .where(IndexedFile.repository_id == repo.id, IndexedFile.skip_reason.is_(None))
    )
    if q:
        statement = statement.where(IndexedFile.file_path.contains(q))
    rows = (await db.execute(statement.order_by(IndexedFile.file_path).offset(skip).limit(limit))).all()
    return [
        IndexedFileResponse(id=f.id, file_path=f.file_path, language=f.language, size_bytes=f.size_bytes,
                            line_count=f.line_count, has_parse_errors=f.has_parse_errors,
                            commit_sha=f.commit_sha, chunk_count=n)
        for f, n in rows
    ]


@router.get("/{repo_id}/index/files/{file_id}/chunks", response_model=List[ChunkResponse],
            summary="Chunks of an indexed file")
async def file_chunks(
    repo_id: uuid.UUID,
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    repo = await get_owned_repository(db, current_user, repo_id)
    file = (await db.execute(
        select(IndexedFile).where(IndexedFile.id == file_id, IndexedFile.repository_id == repo.id)
    )).scalar_one_or_none()
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    chunks = (await db.execute(
        select(CodeChunk).where(CodeChunk.file_id == file.id).order_by(CodeChunk.start_byte)
    )).scalars().all()
    return [_chunk_response(c) for c in chunks]


@router.get("/{repo_id}/symbols", response_model=List[SymbolResponse], summary="Repository symbols")
async def repository_symbols(
    repo_id: uuid.UUID,
    q: Optional[str] = Query(None, max_length=200, description="Filter by name substring"),
    kind: Optional[str] = Query(None, max_length=50),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    repo = await get_owned_repository(db, current_user, repo_id)
    statement = (
        select(CodeSymbol, IndexedFile.file_path).join(IndexedFile, IndexedFile.id == CodeSymbol.file_id)
        .where(IndexedFile.repository_id == repo.id)
    )
    if q:
        statement = statement.where(CodeSymbol.name.contains(q))
    if kind:
        statement = statement.where(CodeSymbol.symbol_type == kind)
    rows = (await db.execute(statement.order_by(IndexedFile.file_path, CodeSymbol.start_line).limit(limit))).all()
    return [
        SymbolResponse(id=s.id, name=s.name, qualified_name=s.qualified_name, kind=s.symbol_type, file_path=path,
                       start_line=s.start_line, end_line=s.end_line, signature=s.signature,
                       is_exported=s.is_exported)
        for s, path in rows
    ]


@router.get("/{repo_id}/search", response_model=SearchResponse, summary="Semantic code search (vector)")
async def search_code(
    repo_id: uuid.UUID,
    q: str = Query(..., min_length=2, max_length=1000),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_current_user),
):
    repo = await get_owned_repository(db, current_user, repo_id)
    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Semantic search needs GEMINI_API_KEY to be configured.")
    try:
        vector = await GeminiEmbedder().embed_query(q)
    except EmbeddingError as exc:
        logger.warning("Query embedding failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Embedding the query failed.")
    hits = await search_similar_chunks(db, repo.id, vector, limit)
    return SearchResponse(query=q, results=[
        SearchHitResponse(chunk=_chunk_response(h.chunk), file_path=h.file_path, score=round(h.score, 4))
        for h in hits
    ])
