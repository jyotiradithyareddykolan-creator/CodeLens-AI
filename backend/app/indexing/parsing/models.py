"""Language-neutral results of syntax-aware parsing.

Line numbers are 1-based and inclusive. Byte offsets index the UTF-8 encoded
source (as parsed) and are half-open ``[start_byte, end_byte)``; the chunker
(Milestone 3) uses them to cut exact source spans.
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple


class SymbolKind:
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    TYPE_ALIAS = "type_alias"
    ENUM = "enum"
    NAMESPACE = "namespace"


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    # Dotted path of enclosing classes/functions, e.g. "Service.run" or "outer.inner".
    qualified_name: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    parent: Optional[str] = None  # qualified name of the enclosing symbol
    signature: Optional[str] = None
    docstring: Optional[str] = None
    is_async: bool = False
    is_exported: bool = False
    decorators: Tuple[str, ...] = ()
    # Base classes / extends / implements, as written in source.
    bases: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Import:
    """One imported binding.

    ``imported`` is the name taken from the module: ``None`` for a whole-module or
    side-effect import, ``"*"`` for wildcard/namespace imports, ``"default"`` for JS
    default imports. ``alias`` is the local name when it differs or is required.
    """

    module: str
    line: int
    imported: Optional[str] = None
    alias: Optional[str] = None
    kind: str = "import"  # import | from | require | dynamic
    is_relative: bool = False
    is_type_only: bool = False


@dataclass(frozen=True)
class Export:
    """One exported name. ``name`` is the public name (``"default"`` for default exports,
    ``"*"`` for ``export * from``); ``local_name`` is the binding it refers to."""

    name: str
    line: int
    kind: str  # declaration | named | default | reexport | reexport_all | commonjs | all_list
    local_name: Optional[str] = None
    source: Optional[str] = None


@dataclass(frozen=True)
class ParseResult:
    path: str
    language: str
    line_count: int
    symbols: Tuple[Symbol, ...] = ()
    imports: Tuple[Import, ...] = ()
    exports: Tuple[Export, ...] = ()
    has_errors: bool = False
    # First few lines containing syntax errors (1-based); parsing still succeeds.
    error_lines: Tuple[int, ...] = field(default=())
