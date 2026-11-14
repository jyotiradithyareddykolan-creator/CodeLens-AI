"""Syntax-aware chunking (Phase 3, Milestone 3)."""

from app.indexing.chunking.chunker import chunk_file, chunk_source
from app.indexing.chunking.models import Chunk, ChunkType

__all__ = ["Chunk", "ChunkType", "chunk_file", "chunk_source"]
