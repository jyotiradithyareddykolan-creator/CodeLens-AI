"""Python symbol, import and export extraction."""

import ast
import inspect
from dataclasses import replace
from typing import List, Optional, Sequence, Set, Tuple

from tree_sitter import Node

from app.indexing.parsing.common import (
    end_line,
    has_child_type,
    named_children,
    signature,
    start_line,
    text,
    truncate_doc,
)
from app.indexing.parsing.models import Export, Import, Symbol, SymbolKind


def _string_value(node: Node) -> Optional[str]:
    """Value of a Python string literal (handles prefixes and implicit concatenation)."""
    raw = text(node)
    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    return value if isinstance(value, str) else None


class PythonExtractor:
    def __init__(self, source: bytes, root: Node):
        self.source = source
        self.root = root
        self.symbols: List[Symbol] = []
        self.imports: List[Import] = []
        self.all_names: Optional[List[str]] = None
        self.all_line: int = 0

    def run(self) -> Tuple[Tuple[Symbol, ...], Tuple[Import, ...], Tuple[Export, ...]]:
        self._walk(self.root, None, None, module_level=True)
        symbols, exports = self._exports()
        return tuple(symbols), tuple(self.imports), tuple(exports)

    # ---------------------------------------------------------------- traversal

    def _walk(self, node: Node, parent: Optional[str], parent_kind: Optional[str], module_level: bool) -> None:
        for child in named_children(node):
            kind = child.type
            if kind == "decorated_definition":
                definition = child.child_by_field_name("definition")
                decorators = tuple(
                    text(d).lstrip("@").strip() for d in child.children if d.type == "decorator"
                )
                if definition is not None:
                    self._definition(definition, child, decorators, parent, parent_kind)
            elif kind in ("function_definition", "class_definition"):
                self._definition(child, child, (), parent, parent_kind)
            elif kind == "import_statement":
                self._import(child)
            elif kind == "import_from_statement":
                self._import_from(child)
            elif kind == "expression_statement" and module_level:
                self._maybe_all(child)
            elif kind in ("if_statement", "try_statement", "with_statement", "block",
                          "else_clause", "elif_clause", "except_clause", "finally_clause",
                          "for_statement", "while_statement", "match_statement", "case_clause"):
                # Definitions guarded by if/try (e.g. optional imports) stay at this level.
                self._walk(child, parent, parent_kind, module_level)

    def _definition(self, node: Node, outer: Node, decorators: Sequence[str],
                    parent: Optional[str], parent_kind: Optional[str]) -> None:
        name = text(node.child_by_field_name("name"))
        if not name:
            return
        body = node.child_by_field_name("body")
        qualified = f"{parent}.{name}" if parent else name

        if node.type == "class_definition":
            kind = SymbolKind.CLASS
            bases = tuple(
                text(arg)
                for arg in named_children(node.child_by_field_name("superclasses"))
                if arg.type not in ("keyword_argument", "comment")
            )
            is_async = False
        else:
            kind = SymbolKind.METHOD if parent_kind == SymbolKind.CLASS else SymbolKind.FUNCTION
            bases = ()
            is_async = has_child_type(node, "async")

        self.symbols.append(
            Symbol(
                name=name,
                kind=kind,
                qualified_name=qualified,
                start_line=start_line(outer),
                end_line=end_line(outer),
                start_byte=outer.start_byte,
                end_byte=outer.end_byte,
                parent=parent,
                signature=signature(self.source, node, body, strip_suffix=":"),
                docstring=self._docstring(body),
                is_async=is_async,
                decorators=tuple(decorators),
                bases=bases,
            )
        )
        if body is not None:
            self._walk(body, qualified, kind, module_level=False)

    @staticmethod
    def _docstring(body: Optional[Node]) -> Optional[str]:
        first = next(named_children(body), None)
        if first is None or first.type != "expression_statement":
            return None
        literal = next(named_children(first), None)
        if literal is None or literal.type not in ("string", "concatenated_string"):
            return None
        value = _string_value(literal)
        return truncate_doc(inspect.cleandoc(value)) if value else None

    # ------------------------------------------------------------------ imports

    def _import(self, node: Node) -> None:
        line = start_line(node)
        for i, child in enumerate(node.children):
            if node.field_name_for_child(i) != "name":
                continue
            if child.type == "aliased_import":
                module = text(child.child_by_field_name("name"))
                alias = text(child.child_by_field_name("alias")) or None
            else:
                module, alias = text(child), None
            if module:
                self.imports.append(Import(module=module, line=line, alias=alias, kind="import"))

    def _import_from(self, node: Node) -> None:
        line = start_line(node)
        module = text(node.child_by_field_name("module_name"))
        if not module:
            return
        relative = module.startswith(".")
        found = False
        for i, child in enumerate(node.children):
            if child.type == "wildcard_import":
                self.imports.append(Import(module=module, line=line, imported="*", kind="from", is_relative=relative))
                found = True
            elif node.field_name_for_child(i) == "name":
                if child.type == "aliased_import":
                    imported = text(child.child_by_field_name("name"))
                    alias = text(child.child_by_field_name("alias")) or None
                else:
                    imported, alias = text(child), None
                self.imports.append(
                    Import(module=module, line=line, imported=imported, alias=alias,
                           kind="from", is_relative=relative)
                )
                found = True
        if not found:
            self.imports.append(Import(module=module, line=line, kind="from", is_relative=relative))

    # ------------------------------------------------------------------ exports

    def _maybe_all(self, statement: Node) -> None:
        """Capture ``__all__ = [...]`` / ``__all__ += [...]`` with literal strings."""
        expr = next(named_children(statement), None)
        if expr is None or expr.type not in ("assignment", "augmented_assignment"):
            return
        if text(expr.child_by_field_name("left")) != "__all__":
            return
        right = expr.child_by_field_name("right")
        if right is None or right.type not in ("list", "tuple"):
            return
        names = [v for v in (_string_value(c) for c in named_children(right) if c.type == "string") if v]
        if expr.type == "assignment":
            self.all_names = names
        else:
            self.all_names = (self.all_names or []) + names
        self.all_line = start_line(statement)

    def _exports(self) -> Tuple[List[Symbol], List[Export]]:
        """Python has no export syntax: ``__all__`` wins; otherwise public top-level names."""
        exports: List[Export] = []
        if self.all_names is not None:
            public: Set[str] = set(self.all_names)
            exports = [Export(name=n, line=self.all_line, kind="all_list", local_name=n) for n in self.all_names]
        else:
            public = {
                s.name for s in self.symbols if s.parent is None and not s.name.startswith("_")
            }
        symbols = []
        for s in self.symbols:
            if s.parent is None and s.name in public:
                s = replace(s, is_exported=True)
                if self.all_names is None:
                    exports.append(Export(name=s.name, line=s.start_line, kind="declaration", local_name=s.name))
            symbols.append(s)
        return symbols, exports
