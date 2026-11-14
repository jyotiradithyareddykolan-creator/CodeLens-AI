"""Chunking output.

A ``Chunk`` is an exact, contiguous slice of one source file at one commit. Content is
never rewritten, so ``source_bytes[start_byte:end_byte].decode() == content`` always
holds, and line numbers can be used directly for citations.

Mapping to the database model (persistence arrives in Milestone 5):
``CodeChunk.symbol_name/symbol_type/start_line/end_line/content/commit_sha`` map to the
fields below; ``start_byte``/``end_byte``, ``content_hash``, ``token_count``,
``parent_symbol``, ``qualified_name`` and ``context`` will need columns at that point.
"""

import uuid
from dataclasses import dataclass
from typing import Optional


class ChunkType:
    # Symbol chunks reuse SymbolKind values: function, method, class, interface,
    # type_alias, enum, namespace.
    IMPORTS = "imports"  # module-level import statements only
    MODULE = "module"  # other module-level code not inside any symbol


@dataclass(frozen=True)
class Chunk:
    id: uuid.UUID
    file_path: str
    language: str
    chunk_type: str
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    start_byte: int  # UTF-8 byte offset, inclusive
    end_byte: int  # UTF-8 byte offset, exclusive
    content: str
    token_count: int  # estimated tokens of ``content``
    content_hash: str  # sha256 of content (embedding reuse across commits)
    repository_id: Optional[uuid.UUID] = None
    commit_sha: Optional[str] = None
    symbol_name: Optional[str] = None
    qualified_name: Optional[str] = None
    symbol_kind: Optional[str] = None
    parent_symbol: Optional[str] = None
    # Enclosing signatures (e.g. the class and function a split part belongs to),
    # prepended when embedding. Not part of ``content`` and never duplicated source.
    context: Optional[str] = None
    context_token_count: int = 0
    part_index: int = 0  # position among chunks of the same symbol
    part_count: int = 1
    has_parse_errors: bool = False

    @property
    def total_token_count(self) -> int:
        """Estimated tokens of what will be embedded (context + content)."""
        return self.token_count + self.context_token_count
