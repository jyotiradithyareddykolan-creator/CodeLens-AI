"""Syntax-aware source parsing with Tree-sitter (Phase 3, Milestone 2).

Usage::

    result = parse_source("app/main.py", "python", text)
    for symbol in result.symbols: ...

Parsing never raises for malformed source: Tree-sitter recovers from syntax errors,
the result is flagged with ``has_errors`` and extraction covers the valid parts.
"""

from app.indexing.parsing.common import error_lines
from app.indexing.parsing.ecmascript import EcmaScriptExtractor
from app.indexing.parsing.languages import (
    UnsupportedLanguageError,
    new_parser,
    supported_languages,
)
from app.indexing.parsing.models import Export, Import, ParseResult, Symbol, SymbolKind
from app.indexing.parsing.python import PythonExtractor

_EXTRACTORS = {
    "python": PythonExtractor,
    "javascript": EcmaScriptExtractor,
    "typescript": EcmaScriptExtractor,
    "tsx": EcmaScriptExtractor,
}


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def parse_source(path: str, language: str, text: str) -> ParseResult:
    """Parse ``text`` (already decoded, as produced by discovery) and extract structure."""
    extractor_cls = _EXTRACTORS.get(language)
    if extractor_cls is None:
        raise UnsupportedLanguageError(f"No extractor registered for language {language!r}")

    source = text.encode("utf-8")
    tree = new_parser(language).parse(source)
    root = tree.root_node
    symbols, imports, exports = extractor_cls(source, root).run()
    return ParseResult(
        path=path,
        language=language,
        line_count=_line_count(text),
        symbols=symbols,
        imports=imports,
        exports=exports,
        has_errors=root.has_error,
        error_lines=error_lines(root) if root.has_error else (),
    )


__all__ = [
    "Export",
    "Import",
    "ParseResult",
    "Symbol",
    "SymbolKind",
    "UnsupportedLanguageError",
    "parse_source",
    "supported_languages",
]
