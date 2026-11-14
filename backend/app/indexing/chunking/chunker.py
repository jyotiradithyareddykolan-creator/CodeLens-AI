"""Syntax-aware chunking.

Strategy
--------
1. Build a containment tree from the parser's symbols (by byte range).
2. Walk the file. Code between symbols becomes ``imports`` / ``module`` chunks; each
   symbol that fits the budget becomes one chunk containing its whole source
   (decorators, nested definitions and the directly preceding comment block included).
3. A symbol that is too large is split along its own structure: its child symbols
   (e.g. a class's methods) become their own chunks, and the remaining parts (class
   header, fields, trailing code) become chunks owned by the symbol.
4. A symbol with no children that is still too large (a long function) is split at
   statement boundaries from the syntax tree, descending into nested blocks only
   when a single statement is too large; then at line boundaries; then, as the last
   resort for a single overlong line, at character boundaries.

Guarantees (enforced by tests):
* chunks are non-overlapping, ordered, exact slices of the source;
* every non-whitespace byte of the file belongs to exactly one chunk;
* every chunk's estimated tokens, including its context header, are within the
  configured limit (the header is capped at a quarter of the limit and shortened
  with "…" when enclosing signatures are longer).

Chunk boundaries are aligned to line starts whenever the code allows, so line ranges
cite whole lines.
"""

import bisect
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from tree_sitter import Node

from app.core.config import settings
from app.indexing.chunking.models import Chunk, ChunkType
from app.indexing.parsing import ParsedFile, Symbol, parse_file
from app.indexing.tokens import estimate_tokens

_CHUNK_ID_NAMESPACE = uuid.UUID("6f1c2b8e-4d4a-4f7e-9a51-3c0de1e2a7b4")
# Lone closing brackets after a split container are merged into the preceding chunk.
_TRAILER = re.compile(rb"^[\s)\]};,]*$")
_TINY_TRAILER_TOKENS = 8
_MIN_BUDGET = 16
_MAX_DEPTH = 64
_IMPORT_NODES = frozenset(
    {"import_statement", "import_from_statement", "future_import_statement", "comment"}
)
_COMMENT_PREFIXES = {
    "python": (b"#",),
}
_DEFAULT_COMMENT_PREFIXES = (b"//", b"/*", b"*", b"*/")


@dataclass
class _SymbolNode:
    symbol: Symbol
    children: List["_SymbolNode"] = field(default_factory=list)


@dataclass
class _Piece:
    start: int
    end: int
    owner: Optional[Symbol]
    context: Optional[str]


class _Chunker:
    def __init__(self, parsed: ParsedFile, max_tokens: int):
        self.data = parsed.source
        self.result = parsed.result
        self.root = parsed.tree.root_node
        self.max_tokens = max_tokens
        self.comment_prefixes = _COMMENT_PREFIXES.get(self.result.language, _DEFAULT_COMMENT_PREFIXES)
        self.line_starts = [0] + [m.end() for m in re.finditer(rb"\n", self.data)]
        self.pieces: List[_Piece] = []

    # ------------------------------------------------------------ line helpers

    def _line_index(self, offset: int) -> int:
        return bisect.bisect_right(self.line_starts, offset) - 1

    def _line_start(self, offset: int) -> int:
        return self.line_starts[self._line_index(offset)]

    def _line_end(self, end: int) -> int:
        """Offset just past the newline of the line containing byte ``end - 1``."""
        if end <= 0:
            return 0
        index = self._line_index(end - 1)
        return self.line_starts[index + 1] if index + 1 < len(self.line_starts) else len(self.data)

    def _blank(self, start: int, end: int) -> bool:
        return not self.data[start:end].strip()

    def _snap_start(self, offset: int, floor: int) -> int:
        start = self._line_start(offset)
        return start if start >= floor and self._blank(start, offset) else offset

    def _snap_end(self, offset: int, ceiling: int) -> int:
        end = self._line_end(offset)
        return end if end <= ceiling and self._blank(offset, end) else offset

    def _trim(self, start: int, end: int):
        """Shrink to whole lines containing non-whitespace; None if only whitespace."""
        segment = self.data[start:end]
        stripped = segment.lstrip()
        if not stripped:
            return None
        first = start + (len(segment) - len(stripped))
        last = start + len(segment.rstrip())
        return max(start, self._line_start(first)), min(end, self._line_end(last))

    def _attach_leading_comments(self, start: int, floor: int) -> int:
        """Extend ``start`` upwards over a contiguous comment block (no blank line)."""
        index = self._line_index(start)
        while index > 0 and self.line_starts[index - 1] >= floor:
            line = self.data[self.line_starts[index - 1]:self.line_starts[index]].strip()
            if not line or not line.startswith(self.comment_prefixes):
                break
            index -= 1
        return self.line_starts[index]

    def _tokens(self, start: int, end: int) -> int:
        return estimate_tokens(self.data[start:end].decode("utf-8"))

    # ------------------------------------------------------------ structure

    @staticmethod
    def _containment(symbols: Sequence[Symbol]) -> List[_SymbolNode]:
        roots: List[_SymbolNode] = []
        stack: List[_SymbolNode] = []
        for symbol in sorted(symbols, key=lambda s: (s.start_byte, -s.end_byte)):
            node = _SymbolNode(symbol)
            while stack and symbol.start_byte >= stack[-1].symbol.end_byte:
                stack.pop()
            if stack and symbol.end_byte <= stack[-1].symbol.end_byte:
                stack[-1].children.append(node)
            else:
                roots.append(node)
            stack.append(node)
        return roots

    def _context(self, ancestors: Sequence[Symbol], owner: Optional[Symbol] = None) -> Optional[str]:
        """Enclosing signatures, capped at a quarter of the limit so content always fits."""
        chain = list(ancestors) + ([owner] if owner is not None else [])
        if not chain:
            return None
        context = "\n".join(s.signature or f"{s.kind} {s.name}" for s in chain)
        cap = self.max_tokens // 4
        if estimate_tokens(context) <= cap:
            return context
        # Keep the innermost signatures (most specific), then shorten from the start.
        lines = context.split("\n")
        while len(lines) > 1 and estimate_tokens("\n".join(lines)) > cap:
            lines.pop(0)
        context = lines[0] if len(lines) == 1 else "\n".join(lines)
        while context and estimate_tokens(context + " …") > cap:
            context = context[: max(0, int(len(context) * 0.8))]
        return f"{context} …" if context else None

    def _budget(self, context: Optional[str]) -> int:
        used = estimate_tokens(context) if context else 0
        return max(_MIN_BUDGET, self.max_tokens - used)

    # ------------------------------------------------------------ walk

    def run(self) -> List[_Piece]:
        self._container(0, len(self.data), None, self._containment(self.result.symbols), [])
        self.pieces.sort(key=lambda p: p.start)
        return self._merge_trailers(self.pieces)

    def _container(self, start: int, end: int, owner: Optional[Symbol],
                   children: List[_SymbolNode], ancestors: List[Symbol]) -> None:
        if owner is not None:
            whole_context = self._context(ancestors)
            if self._tokens(start, end) <= self._budget(whole_context):
                self._emit(start, end, owner, whole_context)
                return
            if not children:
                parts_context = self._context(ancestors, owner)
                self._split(start, end, owner, parts_context)
                return

        inner_ancestors = ancestors + ([owner] if owner is not None else [])
        cursor = start
        for i, child in enumerate(children):
            symbol = child.symbol
            child_start = self._snap_start(symbol.start_byte, cursor)
            if child_start == self._line_start(child_start):
                child_start = self._attach_leading_comments(child_start, cursor)
            child_start = max(cursor, child_start)
            limit = children[i + 1].symbol.start_byte if i + 1 < len(children) else end
            child_end = max(child_start, self._snap_end(min(symbol.end_byte, end), min(limit, end)))
            if child_start > cursor:
                self._gap(cursor, child_start, owner, ancestors)
            self._container(child_start, child_end, symbol, child.children, inner_ancestors)
            cursor = max(cursor, child_end)
        if cursor < end:
            self._gap(cursor, end, owner, ancestors)

    def _gap(self, start: int, end: int, owner: Optional[Symbol], ancestors: List[Symbol]) -> None:
        bounds = self._trim(start, end)
        if bounds is None:
            return
        start, end = bounds
        context = self._context(ancestors, owner)
        if self._tokens(start, end) <= self._budget(context):
            self._emit(start, end, owner, context)
        else:
            self._split(start, end, owner, context)

    # ------------------------------------------------------------ splitting

    def _split(self, start: int, end: int, owner: Optional[Symbol], context: Optional[str]) -> None:
        budget = self._budget(context)
        node = self._node_for(start, end)
        self._split_node(start, end, node, owner, context, budget, 0)

    def _node_for(self, start: int, end: int) -> Node:
        bounds = self._trim(start, end) or (start, end)
        segment = self.data[bounds[0]:bounds[1]]
        first = bounds[0] + (len(segment) - len(segment.lstrip()))
        last = bounds[0] + len(segment.rstrip())
        return self.root.descendant_for_byte_range(first, max(first, last - 1)) or self.root

    def _boundary(self, offset: int) -> Optional[int]:
        line_start = self._line_start(offset)
        return line_start if self._blank(line_start, offset) else None

    def _split_node(self, start: int, end: int, node: Node, owner: Optional[Symbol],
                    context: Optional[str], budget: int, depth: int,
                    prefix: Optional[int] = None) -> None:
        """Split ``[prefix or start, end)``. Boundaries come from ``node`` within
        ``[start, end)``; ``prefix`` is a small leading part (e.g. a function signature)
        carried into the first piece instead of becoming a chunk of its own."""
        begin = prefix if prefix is not None else start
        if self._tokens(begin, end) <= budget:
            self._emit_trimmed(begin, end, owner, context)
            return
        if depth > _MAX_DEPTH:
            self._split_lines(begin, end, owner, context, budget)
            return

        kids = [k for k in node.named_children if k.end_byte > start and k.start_byte < end]
        boundaries = sorted({
            b for b in (self._boundary(k.start_byte) for k in kids)
            if b is not None and start < b < end
        })
        if not boundaries:
            # No statement starts on its own line here: descend into the largest child
            # (e.g. from `export_statement` to the function, then to its body).
            largest = max(kids, key=lambda k: k.end_byte - k.start_byte, default=None)
            if largest is not None and (largest.end_byte - largest.start_byte) < (node.end_byte - node.start_byte):
                self._split_node(start, end, largest, owner, context, budget, depth + 1, prefix)
            else:
                self._split_lines(begin, end, owner, context, budget)
            return

        edges = [start] + boundaries + [end]
        acc: Optional[List[int]] = [prefix, start] if prefix is not None else None
        for a, b in zip(edges, edges[1:]):
            candidate = [acc[0] if acc else a, b]
            if self._tokens(*candidate) <= budget:
                acc = candidate
                continue
            if self._tokens(a, b) <= budget:
                if acc:
                    self._emit_trimmed(acc[0], acc[1], owner, context)
                acc = [a, b]
                continue
            # This segment alone is too large: split it, carrying a small pending
            # header (signature, decorators) into its first piece.
            carry = None
            if acc and self._tokens(acc[0], a) <= budget // 4:
                carry = acc[0]
            elif acc:
                self._emit_trimmed(acc[0], acc[1], owner, context)
            acc = None
            self._split_node(a, b, self._node_for(a, b), owner, context, budget, depth + 1, carry)
        if acc:
            self._emit_trimmed(acc[0], acc[1], owner, context)

    def _split_lines(self, start: int, end: int, owner: Optional[Symbol],
                     context: Optional[str], budget: int) -> None:
        first, last = self._line_index(start), self._line_index(max(start, end - 1))
        acc: Optional[List[int]] = None
        for index in range(first, last + 1):
            a = max(start, self.line_starts[index])
            b = min(end, self.line_starts[index + 1] if index + 1 < len(self.line_starts) else len(self.data))
            candidate = [acc[0] if acc else a, b]
            if self._tokens(*candidate) <= budget:
                acc = candidate
                continue
            if acc:
                self._emit_trimmed(acc[0], acc[1], owner, context)
                acc = None
            if self._tokens(a, b) <= budget:
                acc = [a, b]
            else:
                self._split_chars(a, b, owner, context, budget)
        if acc:
            self._emit_trimmed(acc[0], acc[1], owner, context)

    def _split_chars(self, start: int, end: int, owner: Optional[Symbol],
                     context: Optional[str], budget: int) -> None:
        """Last resort for one overlong line. Every character is estimated at no more
        than ``CHUNK_TOKEN_ESTIMATE_MULTIPLIER`` tokens, so this always fits."""
        text = self.data[start:end].decode("utf-8")
        step = max(1, int(budget / max(settings.CHUNK_TOKEN_ESTIMATE_MULTIPLIER, 1e-9)))
        offset = start
        for i in range(0, len(text), step):
            piece = text[i:i + step].encode("utf-8")
            self._emit(offset, offset + len(piece), owner, context)
            offset += len(piece)

    # ------------------------------------------------------------ emission

    def _emit_trimmed(self, start: int, end: int, owner: Optional[Symbol], context: Optional[str]) -> None:
        bounds = self._trim(start, end)
        if bounds is not None:
            self._emit(bounds[0], bounds[1], owner, context)

    def _emit(self, start: int, end: int, owner: Optional[Symbol], context: Optional[str]) -> None:
        if end > start and not self._blank(start, end):
            self.pieces.append(_Piece(start, end, owner, context))

    def _merge_trailers(self, pieces: List[_Piece]) -> List[_Piece]:
        merged: List[_Piece] = []
        for piece in pieces:
            previous = merged[-1] if merged else None
            if (
                previous is not None
                and _TRAILER.match(self.data[piece.start:piece.end])
                and self._tokens(piece.start, piece.end) <= _TINY_TRAILER_TOKENS
                and self._blank(previous.end, piece.start)
                and self._tokens(previous.start, piece.end) <= self._budget(previous.context)
            ):
                previous.end = piece.end
                continue
            merged.append(piece)
        return merged

    def _only_imports(self, start: int, end: int) -> bool:
        kinds = [k.type for k in self.root.named_children if k.start_byte < end and k.end_byte > start]
        return bool(kinds) and all(k in _IMPORT_NODES for k in kinds) and any(k != "comment" for k in kinds)


def chunk_file(
    parsed: ParsedFile,
    *,
    repository_id: Optional[uuid.UUID] = None,
    commit_sha: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> List[Chunk]:
    """Split a parsed file into ordered, exact, syntax-aware chunks."""
    limit = max_tokens or settings.CHUNK_MAX_TOKENS
    chunker = _Chunker(parsed, limit)
    pieces = chunker.run()
    result = parsed.result
    data = parsed.source

    counts: dict = {}
    for piece in pieces:
        if piece.owner is not None:
            counts[piece.owner.qualified_name] = counts.get(piece.owner.qualified_name, 0) + 1
    seen: dict = {}

    chunks: List[Chunk] = []
    for piece in pieces:
        content = data[piece.start:piece.end].decode("utf-8")
        owner = piece.owner
        if owner is not None:
            index = seen.get(owner.qualified_name, 0)
            seen[owner.qualified_name] = index + 1
            chunk_type, part_count = owner.kind, counts[owner.qualified_name]
        else:
            index, part_count = 0, 1
            chunk_type = ChunkType.IMPORTS if chunker._only_imports(piece.start, piece.end) else ChunkType.MODULE
        key = f"{repository_id}|{commit_sha}|{result.path}|{piece.start}|{piece.end}"
        chunks.append(
            Chunk(
                id=uuid.uuid5(_CHUNK_ID_NAMESPACE, key),
                file_path=result.path,
                language=result.language,
                chunk_type=chunk_type,
                start_line=chunker._line_index(piece.start) + 1,
                end_line=chunker._line_index(piece.end - 1) + 1,
                start_byte=piece.start,
                end_byte=piece.end,
                content=content,
                token_count=estimate_tokens(content),
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                repository_id=repository_id,
                commit_sha=commit_sha,
                symbol_name=owner.name if owner else None,
                qualified_name=owner.qualified_name if owner else None,
                symbol_kind=owner.kind if owner else None,
                parent_symbol=owner.parent if owner else None,
                context=piece.context,
                context_token_count=estimate_tokens(piece.context) if piece.context else 0,
                part_index=index,
                part_count=part_count,
                has_parse_errors=result.has_errors,
            )
        )
    return chunks


def chunk_source(
    path: str,
    language: str,
    text: str,
    *,
    repository_id: Optional[uuid.UUID] = None,
    commit_sha: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> List[Chunk]:
    """Parse and chunk decoded source text (as produced by discovery)."""
    return chunk_file(
        parse_file(path, language, text),
        repository_id=repository_id,
        commit_sha=commit_sha,
        max_tokens=max_tokens,
    )
