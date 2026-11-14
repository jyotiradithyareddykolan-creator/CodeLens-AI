"""JavaScript, TypeScript and TSX symbol, import and export extraction.

One extractor serves all three grammars; TypeScript-only node types simply never
appear in JavaScript trees.
"""

import re
from dataclasses import replace
from typing import List, Optional, Sequence, Set, Tuple

from tree_sitter import Node

from app.indexing.parsing.common import (
    end_line,
    has_child_type,
    named_children,
    signature,
    start_line,
    string_literal_value,
    text,
    truncate_doc,
)
from app.indexing.parsing.models import Export, Import, Symbol, SymbolKind

FUNCTION_DECLARATIONS = ("function_declaration", "generator_function_declaration")
CLASS_DECLARATIONS = ("class_declaration", "abstract_class_declaration")
FUNCTION_VALUES = ("arrow_function", "function_expression", "function", "generator_function")
CLASS_VALUES = ("class",)
VARIABLE_DECLARATIONS = ("lexical_declaration", "variable_declaration")
TYPE_DECLARATIONS = {
    "interface_declaration": SymbolKind.INTERFACE,
    "type_alias_declaration": SymbolKind.TYPE_ALIAS,
    "enum_declaration": SymbolKind.ENUM,
}
COMMONJS_TARGET = re.compile(r"^(?:module\.)?exports(?:\.([A-Za-z_$][\w$]*))?$")


def _clean_comment(raw: str) -> str:
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        for prefix in ("/**", "/*", "*/", "//", "*"):
            if line.startswith(prefix):
                line = line[len(prefix):]
                break
        if line.endswith("*/"):
            line = line[:-2]
        lines.append(line.strip())
    return "\n".join(lines).strip()


class EcmaScriptExtractor:
    def __init__(self, source: bytes, root: Node):
        self.source = source
        self.root = root
        self.symbols: List[Symbol] = []
        self.imports: List[Import] = []
        self.exports: List[Export] = []
        # Local names exported via `export { x }` / `export default x`.
        self.exported_locals: Set[str] = set()

    def run(self) -> Tuple[Tuple[Symbol, ...], Tuple[Import, ...], Tuple[Export, ...]]:
        self._walk(self.root, None, None)
        self._collect_require_and_dynamic_imports()
        symbols = [
            replace(s, is_exported=True)
            if s.parent is None and s.name in self.exported_locals and not s.is_exported
            else s
            for s in self.symbols
        ]
        return tuple(symbols), tuple(self.imports), tuple(self.exports)

    # ---------------------------------------------------------------- traversal

    def _walk(self, node: Node, parent: Optional[str], parent_kind: Optional[str]) -> None:
        for child in named_children(node):
            self._visit(child, parent, parent_kind)

    def _visit(self, node: Node, parent: Optional[str], parent_kind: Optional[str],
               outer: Optional[Node] = None, exported: bool = False,
               decorators: Sequence[str] = ()) -> List[str]:
        """Handle one node. Returns the names it declares (used for export records)."""
        kind = node.type
        outer = outer or node

        if kind == "import_statement":
            self._import(node)
        elif kind == "export_statement":
            self._export(node, parent, parent_kind)
        elif kind in FUNCTION_DECLARATIONS:
            name = text(node.child_by_field_name("name"))
            if name:
                self._function(node, outer, name, node, parent, parent_kind, exported, decorators)
                return [name]
        elif kind in CLASS_DECLARATIONS:
            name = text(node.child_by_field_name("name"))
            if name:
                self._class(node, outer, name, parent, exported, decorators)
                return [name]
        elif kind in TYPE_DECLARATIONS:
            name = text(node.child_by_field_name("name"))
            if name:
                self._add(node, outer, name, TYPE_DECLARATIONS[kind], parent,
                          body=node.child_by_field_name("body") or node.child_by_field_name("value"),
                          exported=exported)
                return [name]
        elif kind in ("internal_module", "module"):
            name = text(node.child_by_field_name("name"))
            body = node.child_by_field_name("body")
            if name:
                qualified = self._add(node, outer, name, SymbolKind.NAMESPACE, parent, body=body, exported=exported)
                self._walk(body, qualified, SymbolKind.NAMESPACE)
                return [name]
        elif kind in VARIABLE_DECLARATIONS:
            return self._variables(node, outer, parent, parent_kind, exported)
        elif kind in ("method_definition", "abstract_method_signature"):
            name = text(node.child_by_field_name("name"))
            if name:
                self._function(node, outer, name, node, parent, parent_kind, exported, decorators,
                               force_kind=SymbolKind.METHOD)
        elif kind in ("public_field_definition", "field_definition"):
            name = text(node.child_by_field_name("name") or node.child_by_field_name("property"))
            value = node.child_by_field_name("value")
            if name and value is not None and value.type in FUNCTION_VALUES:
                self._function(value, outer, name, node, parent, parent_kind, exported, decorators,
                               force_kind=SymbolKind.METHOD)
        elif kind == "expression_statement":
            self._expression_statement(node, parent, parent_kind)
        else:
            # Containers (blocks, if/try, ambient declarations, callbacks ...).
            self._walk(node, parent, parent_kind)
        return []

    # ---------------------------------------------------------------- symbols

    def _add(self, node: Node, outer: Node, name: str, kind: str, parent: Optional[str], *,
             body: Optional[Node] = None, sig_node: Optional[Node] = None, exported: bool = False,
             is_async: bool = False, decorators: Sequence[str] = (), bases: Sequence[str] = (),
             strip_suffix: str = "") -> str:
        qualified = f"{parent}.{name}" if parent else name
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
                signature=signature(self.source, sig_node or node, body, strip_suffix=strip_suffix),
                docstring=self._leading_comment(outer),
                is_async=is_async,
                is_exported=exported,
                decorators=tuple(decorators) + self._decorators(node),
                bases=tuple(bases),
            )
        )
        return qualified

    def _function(self, fn: Node, outer: Node, name: str, sig_node: Node, parent: Optional[str],
                  parent_kind: Optional[str], exported: bool, decorators: Sequence[str],
                  force_kind: Optional[str] = None) -> str:
        kind = force_kind or (
            SymbolKind.METHOD if parent_kind == SymbolKind.CLASS else SymbolKind.FUNCTION
        )
        body = fn.child_by_field_name("body")
        qualified = self._add(
            fn, outer, name, kind, parent, body=body, sig_node=sig_node, exported=exported,
            is_async=has_child_type(fn, "async"), decorators=decorators, strip_suffix="=>",
        )
        if body is not None:
            self._walk(body, qualified, kind)
        return qualified

    def _class(self, node: Node, outer: Node, name: str, parent: Optional[str],
               exported: bool, decorators: Sequence[str]) -> str:
        body = node.child_by_field_name("body")
        qualified = self._add(
            node, outer, name, SymbolKind.CLASS, parent, body=body, exported=exported,
            decorators=decorators, bases=self._bases(node),
        )
        self._walk(body, qualified, SymbolKind.CLASS)
        return qualified

    def _variables(self, node: Node, outer: Node, parent: Optional[str],
                   parent_kind: Optional[str], exported: bool) -> List[str]:
        names: List[str] = []
        declarators = [c for c in named_children(node) if c.type == "variable_declarator"]
        for declarator in declarators:
            name_node = declarator.child_by_field_name("name")
            value = declarator.child_by_field_name("value")
            if name_node is not None and name_node.type == "identifier":
                names.append(text(name_node))
            if value is None:
                continue
            # A single declarator owns the whole statement (keeps `export const` in range).
            span = outer if len(declarators) == 1 else declarator
            sig_node = node if len(declarators) == 1 else declarator
            if name_node is not None and name_node.type == "identifier" and value.type in FUNCTION_VALUES:
                self._function(value, span, text(name_node), sig_node, parent, parent_kind, exported, ())
            elif name_node is not None and name_node.type == "identifier" and value.type in CLASS_VALUES:
                self._class(value, span, text(name_node), parent, exported, ())
            else:
                self._visit(value, parent, parent_kind)
        return names

    def _bases(self, node: Node) -> Tuple[str, ...]:
        bases: List[str] = []
        for heritage in (c for c in node.children if c.type == "class_heritage"):
            for clause in named_children(heritage):
                if clause.type in ("extends_clause", "implements_clause"):
                    bases.extend(
                        text(c) for c in named_children(clause) if c.type != "type_arguments"
                    )
                else:  # JavaScript: `extends <expression>` directly
                    bases.append(text(clause))
        return tuple(b for b in bases if b)

    @staticmethod
    def _decorators(node: Node) -> Tuple[str, ...]:
        return tuple(text(c).lstrip("@").strip() for c in node.children if c.type == "decorator")

    @staticmethod
    def _leading_comment(outer: Node) -> Optional[str]:
        """Contiguous comment block directly above a declaration (JSDoc or //)."""
        comments: List[Node] = []
        expected_end = start_line(outer) - 1
        prev = outer.prev_sibling
        while prev is not None and prev.type == "comment" and end_line(prev) >= expected_end:
            comments.append(prev)
            expected_end = start_line(prev) - 1
            prev = prev.prev_sibling
        if not comments:
            return None
        return truncate_doc("\n".join(_clean_comment(text(c)) for c in reversed(comments)))

    # ---------------------------------------------------------------- imports

    def _import(self, node: Node) -> None:
        line = start_line(node)
        source = string_literal_value(node.child_by_field_name("source"))
        type_only = has_child_type(node, "type")
        clause = next((c for c in node.children if c.type in ("import_clause", "import_require_clause")), None)

        if clause is not None and clause.type == "import_require_clause":
            module = string_literal_value(clause.child_by_field_name("source"))
            alias = next((text(c) for c in named_children(clause) if c.type == "identifier"), None)
            if module:
                self._add_import(module, line, None, alias, "require", False)
            return
        if not source:
            return
        if clause is None:
            self._add_import(source, line, None, None, "import", type_only)
            return
        for part in named_children(clause):
            if part.type == "identifier":
                self._add_import(source, line, "default", text(part), "import", type_only)
            elif part.type == "namespace_import":
                alias = next((text(c) for c in named_children(part) if c.type == "identifier"), None)
                self._add_import(source, line, "*", alias, "import", type_only)
            elif part.type == "named_imports":
                for spec in (s for s in named_children(part) if s.type == "import_specifier"):
                    imported = text(spec.child_by_field_name("name"))
                    alias = text(spec.child_by_field_name("alias")) or None
                    self._add_import(source, line, imported, alias, "import",
                                     type_only or has_child_type(spec, "type"))

    def _add_import(self, module: str, line: int, imported: Optional[str], alias: Optional[str],
                    kind: str, type_only: bool) -> None:
        self.imports.append(
            Import(module=module, line=line, imported=imported, alias=alias, kind=kind,
                   is_relative=module.startswith("."), is_type_only=type_only)
        )

    def _collect_require_and_dynamic_imports(self) -> None:
        """``require("x")`` and ``import("x")`` anywhere in the file."""
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node.type == "call_expression":
                function = node.child_by_field_name("function")
                args = [a for a in named_children(node.child_by_field_name("arguments"))]
                module = string_literal_value(args[0]) if len(args) == 1 else None
                is_require = function is not None and function.type == "identifier" and text(function) == "require"
                is_dynamic = function is not None and function.type == "import"
                if module and (is_require or is_dynamic):
                    self._record_call_import(node, module, "require" if is_require else "dynamic")
            stack.extend(named_children(node))
        self.imports.sort(key=lambda i: (i.line, i.module))

    def _record_call_import(self, call: Node, module: str, kind: str) -> None:
        line = start_line(call)
        holder = call.parent
        if holder is not None and holder.type == "await_expression":
            holder = holder.parent
        target = holder.child_by_field_name("name") if holder is not None and holder.type == "variable_declarator" else None
        if target is not None and target.type == "identifier":
            self._add_import(module, line, None, text(target), kind, False)
        elif target is not None and target.type == "object_pattern":
            for prop in named_children(target):
                if prop.type == "shorthand_property_identifier_pattern":
                    self._add_import(module, line, text(prop), None, kind, False)
                elif prop.type == "pair_pattern":
                    self._add_import(module, line, text(prop.child_by_field_name("key")),
                                     text(prop.child_by_field_name("value")) or None, kind, False)
        else:
            self._add_import(module, line, None, None, kind, False)

    # ---------------------------------------------------------------- exports

    def _export(self, node: Node, parent: Optional[str], parent_kind: Optional[str]) -> None:
        line = start_line(node)
        is_default = has_child_type(node, "default")
        source = string_literal_value(node.child_by_field_name("source"))
        decorators = self._decorators(node)
        declaration = node.child_by_field_name("declaration")
        value = node.child_by_field_name("value")
        clause = next((c for c in node.children if c.type == "export_clause"), None)

        if declaration is not None:
            names = self._visit(declaration, parent, parent_kind, outer=node, exported=True,
                                decorators=decorators)
            if parent is not None:
                # `export` inside a TS namespace exports from the namespace, not the module.
                return
            for name in names:
                self.exports.append(
                    Export(name="default" if is_default else name, line=line,
                           kind="default" if is_default else "declaration", local_name=name)
                )
        elif value is not None:
            self._export_default_value(node, value, parent, parent_kind)
        elif clause is not None:
            for spec in (s for s in named_children(clause) if s.type == "export_specifier"):
                local = text(spec.child_by_field_name("name"))
                public = text(spec.child_by_field_name("alias")) or local
                if source:
                    self.exports.append(Export(name=public, line=line, kind="reexport", local_name=local, source=source))
                else:
                    self.exports.append(Export(name=public, line=line, kind="named", local_name=local))
                    self.exported_locals.add(local)
        elif source:
            namespace = next((c for c in node.children if c.type == "namespace_export"), None)
            if namespace is not None:
                alias = next((text(c) for c in named_children(namespace)), "*")
                self.exports.append(Export(name=alias, line=line, kind="reexport", local_name="*", source=source))
            else:
                self.exports.append(Export(name="*", line=line, kind="reexport_all", source=source))
        else:
            # TypeScript `export = Foo;`
            target = next((c for c in named_children(node) if c.type == "identifier"), None)
            if target is not None:
                self.exports.append(Export(name="default", line=line, kind="commonjs", local_name=text(target)))
                self.exported_locals.add(text(target))

    def _export_default_value(self, node: Node, value: Node, parent: Optional[str],
                              parent_kind: Optional[str]) -> None:
        line = start_line(node)
        if value.type == "identifier":
            local = text(value)
            self.exported_locals.add(local)
        elif value.type in FUNCTION_VALUES or value.type in CLASS_VALUES:
            local = text(value.child_by_field_name("name")) or "default"
            if value.type in CLASS_VALUES:
                self._class(value, node, local, parent, True, self._decorators(node))
            else:
                self._function(value, node, local, node, parent, parent_kind, True, ())
        else:
            local = None
            self._visit(value, parent, parent_kind)
        self.exports.append(Export(name="default", line=line, kind="default", local_name=local))

    def _expression_statement(self, node: Node, parent: Optional[str], parent_kind: Optional[str]) -> None:
        expr = next(named_children(node), None)
        if expr is None:
            return
        if expr.type == "internal_module":
            self._visit(expr, parent, parent_kind, outer=node)
            return
        if expr.type == "assignment_expression" and parent is None:
            match = COMMONJS_TARGET.match(text(expr.child_by_field_name("left")))
            right = expr.child_by_field_name("right")
            if match and right is not None:
                self._commonjs_export(node, match.group(1), right, parent_kind)
                return
        self._walk(node, parent, parent_kind)

    def _commonjs_export(self, node: Node, name: Optional[str], right: Node, parent_kind: Optional[str]) -> None:
        line = start_line(node)
        if name:  # exports.foo = ... / module.exports.foo = ...
            if right.type in FUNCTION_VALUES:
                self._function(right, node, name, node, None, parent_kind, True, ())
            local = text(right) if right.type == "identifier" else name
            self.exports.append(Export(name=name, line=line, kind="commonjs", local_name=local))
            return
        if right.type == "object":  # module.exports = { a, b: c }
            for prop in named_children(right):
                if prop.type == "shorthand_property_identifier":
                    self.exports.append(Export(name=text(prop), line=line, kind="commonjs", local_name=text(prop)))
                    self.exported_locals.add(text(prop))
                elif prop.type == "pair":
                    key = text(prop.child_by_field_name("key"))
                    val = prop.child_by_field_name("value")
                    local = text(val) if val is not None and val.type == "identifier" else None
                    self.exports.append(Export(name=key, line=line, kind="commonjs", local_name=local))
                    if local:
                        self.exported_locals.add(local)
            return
        local = text(right) if right.type == "identifier" else None
        if local:
            self.exported_locals.add(local)
        elif right.type in FUNCTION_VALUES or right.type in CLASS_VALUES:
            local = text(right.child_by_field_name("name")) or "default"
            if right.type in CLASS_VALUES:
                self._class(right, node, local, None, True, ())
            else:
                self._function(right, node, local, node, None, parent_kind, True, ())
        self.exports.append(Export(name="default", line=line, kind="commonjs", local_name=local))
