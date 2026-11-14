"""Helpers shared by the language extractors."""

import re
from typing import Iterator, List, Optional, Tuple

from tree_sitter import Node

MAX_SIGNATURE_CHARS = 500
MAX_DOCSTRING_CHARS = 2000
MAX_ERROR_LINES = 20
_WHITESPACE = re.compile(r"\s+")


def text(node: Optional[Node]) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def start_line(node: Node) -> int:
    return node.start_point[0] + 1


def end_line(node: Node) -> int:
    """Inclusive 1-based end line. A node ending at column 0 ends on the previous line."""
    row, column = node.end_point
    if column == 0 and row > node.start_point[0]:
        return row
    return row + 1


def has_child_type(node: Node, child_type: str) -> bool:
    return any(child.type == child_type for child in node.children)


def signature(source: bytes, node: Node, body: Optional[Node], strip_suffix: str = "") -> Optional[str]:
    """Declaration text from ``node`` up to its body, whitespace-collapsed and truncated."""
    end = body.start_byte if body is not None else node.end_byte
    raw = source[node.start_byte:end].decode("utf-8", errors="replace")
    collapsed = _WHITESPACE.sub(" ", raw).strip()
    if strip_suffix and collapsed.endswith(strip_suffix):
        collapsed = collapsed[: -len(strip_suffix)].rstrip()
    if not collapsed:
        return None
    return collapsed[:MAX_SIGNATURE_CHARS]


def truncate_doc(doc: str) -> Optional[str]:
    doc = doc.strip()
    return doc[:MAX_DOCSTRING_CHARS] if doc else None


def string_literal_value(node: Optional[Node]) -> Optional[str]:
    """Value of a simple JS/TS string literal (``"x"`` / ``'x'``)."""
    if node is None or node.type != "string":
        return None
    fragments = [text(c) for c in node.children if c.type == "string_fragment"]
    return "".join(fragments)


def error_lines(root: Node) -> Tuple[int, ...]:
    """Lines of ERROR / MISSING nodes, without descending into healthy subtrees."""
    lines: List[int] = []
    stack = [root]
    while stack and len(lines) < MAX_ERROR_LINES:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            line = start_line(node)
            if line not in lines:
                lines.append(line)
            continue
        if node.has_error:
            stack.extend(reversed(node.children))
    return tuple(sorted(lines))


def named_children(node: Optional[Node]) -> Iterator[Node]:
    if node is None:
        return iter(())
    return (child for child in node.children if child.is_named)
