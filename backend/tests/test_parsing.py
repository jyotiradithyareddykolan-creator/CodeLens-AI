"""Tree-sitter parsing tests (real grammars, no mocks)."""

from pathlib import Path

import pytest

from app.indexing.file_filter import LANGUAGES_BY_EXTENSION
from app.indexing.parsing import (
    Import,
    SymbolKind,
    UnsupportedLanguageError,
    parse_source,
    supported_languages,
)

REPO = Path(__file__).resolve().parents[2]


def by_name(result):
    return {s.qualified_name: s for s in result.symbols}


def line_of(source: str, needle: str) -> int:
    """1-based line of the first occurrence of ``needle``."""
    index = source.index(needle)
    return source.count("\n", 0, index) + 1


def assert_spans_are_exact(source: str, result) -> None:
    """Every symbol's byte span and line range must point at real source text."""
    data = source.encode("utf-8")
    lines = source.splitlines()
    for s in result.symbols:
        assert 1 <= s.start_line <= s.end_line <= max(result.line_count, 1), s
        snippet = data[s.start_byte:s.end_byte].decode("utf-8")
        assert snippet.strip(), s
        first_line = lines[s.start_line - 1]
        assert snippet.splitlines()[0].strip() in first_line or first_line.strip() in snippet, s
        assert data[: s.start_byte].count(b"\n") + 1 == s.start_line, s


# --------------------------------------------------------------------------- python

PY = '''"""Module docstring."""
import os, sys as system
import a.b.c
from . import sibling
from ..pkg.mod import thing as alias, other
from typing import *

__all__ = ["Service", "helper"]


def helper(x: int, y: int = 2) -> int:
    """Add things.

    Longer description.
    """
    def inner():
        return x
    return x + y


@decorator
@other.decorator(arg=1)
async def fetch(url):
    import json
    return await get(url)


class Service(Base, Mixin, metaclass=Meta):
    """A service."""

    def __init__(self):
        self.x = 1

    @property
    def value(self) -> int:
        return self.x

    class Config:
        frozen = True


def _private():
    pass


if TYPE_CHECKING:
    def guarded() -> None: ...
'''


def test_python_symbols_kinds_and_nesting():
    result = parse_source("svc.py", "python", PY)
    symbols = by_name(result)
    assert set(symbols) == {
        "helper", "helper.inner", "fetch", "Service", "Service.__init__", "Service.value",
        "Service.Config", "_private", "guarded",
    }
    assert symbols["helper"].kind == SymbolKind.FUNCTION
    assert symbols["helper.inner"].kind == SymbolKind.FUNCTION
    assert symbols["helper.inner"].parent == "helper"
    assert symbols["Service.__init__"].kind == SymbolKind.METHOD
    assert symbols["Service.Config"].kind == SymbolKind.CLASS
    assert symbols["Service.Config"].parent == "Service"
    assert symbols["fetch"].is_async and not symbols["helper"].is_async
    assert not result.has_errors


def test_python_line_ranges_signatures_docstrings_decorators():
    result = parse_source("svc.py", "python", PY)
    s = by_name(result)
    helper = s["helper"]
    assert helper.start_line == line_of(PY, "def helper")
    assert helper.end_line == line_of(PY, "    return x + y")
    assert helper.signature == "def helper(x: int, y: int = 2) -> int"
    assert helper.docstring == "Add things.\n\nLonger description."

    fetch = s["fetch"]
    # Range starts at the first decorator so chunks and citations include it.
    assert fetch.start_line == line_of(PY, "@decorator")
    assert fetch.decorators == ("decorator", "other.decorator(arg=1)")
    assert fetch.signature == "async def fetch(url)"

    service = s["Service"]
    assert service.bases == ("Base", "Mixin")
    assert service.docstring == "A service."
    assert service.end_line == line_of(PY, "        frozen = True")
    assert s["Service.value"].decorators == ("property",)
    assert_spans_are_exact(PY, result)


def test_python_imports():
    imports = parse_source("svc.py", "python", PY).imports
    assert imports == (
        Import(module="os", line=2, kind="import"),
        Import(module="sys", line=2, alias="system", kind="import"),
        Import(module="a.b.c", line=3, kind="import"),
        Import(module=".", line=4, imported="sibling", kind="from", is_relative=True),
        Import(module="..pkg.mod", line=5, imported="thing", alias="alias", kind="from", is_relative=True),
        Import(module="..pkg.mod", line=5, imported="other", kind="from", is_relative=True),
        Import(module="typing", line=6, imported="*", kind="from"),
        Import(module="json", line=line_of(PY, "    import json"), kind="import"),
    )


def test_python_exports_follow_dunder_all():
    result = parse_source("svc.py", "python", PY)
    assert [e.name for e in result.exports] == ["Service", "helper"]
    assert all(e.kind == "all_list" for e in result.exports)
    exported = {s.qualified_name for s in result.symbols if s.is_exported}
    assert exported == {"Service", "helper"}


def test_python_exports_default_to_public_top_level_names():
    src = "def public():\n    pass\n\ndef _hidden():\n    pass\n\nclass K:\n    def m(self):\n        pass\n"
    result = parse_source("m.py", "python", src)
    assert {e.name for e in result.exports} == {"public", "K"}
    assert {s.qualified_name for s in result.symbols if s.is_exported} == {"public", "K"}


def test_python_crlf_and_unicode_keep_exact_lines_and_bytes():
    src = '# café ☕ comment\r\n\r\ndef f():\r\n    return "é"\r\n\r\nclass Ü:\r\n    pass\r\n'
    result = parse_source("u.py", "python", src)
    s = by_name(result)
    assert (s["f"].start_line, s["f"].end_line) == (3, 4)
    assert (s["Ü"].start_line, s["Ü"].end_line) == (6, 7)
    data = src.encode("utf-8")
    assert data[s["f"].start_byte:s["f"].end_byte].decode().startswith("def f():")
    assert result.line_count == 7


def test_python_syntax_errors_are_reported_without_losing_valid_symbols():
    src = "def ok():\n    return 1\n\ndef broken(:\n    pass\n\nclass Fine:\n    pass\n"
    result = parse_source("bad.py", "python", src)
    assert result.has_errors
    assert result.error_lines and all(1 <= line <= 8 for line in result.error_lines)
    names = {s.name for s in result.symbols}
    assert {"ok", "Fine"} <= names


def test_empty_file():
    result = parse_source("empty.py", "python", "")
    assert result.symbols == () and result.imports == () and result.line_count == 0
    assert not result.has_errors


# ------------------------------------------------------------------ typescript

TS = '''import React, { useState as useS } from "react";
import * as path from "path";
import type { Props } from "./types";
import "./side-effect.css";
import fs = require("fs");

/**
 * Adds one.
 */
export const addOne = (n: number): number => n + 1;

@Injectable()
export class UserService extends BaseService<User> implements Disposable {
  private cache = new Map();
  handler = async (event: Event) => {
    return event;
  };

  constructor(private readonly repo: Repo) {
    super();
  }

  async findUser(id: string): Promise<User> {
    return this.repo.get(id);
  }

  static create(): UserService { return new UserService(null as any); }
}

export interface User { id: string; name: string }
export type UserId = string;
export enum Role { Admin, Member }

export abstract class Shape {
  abstract area(): number;
}

function overloaded(a: string): string;
function overloaded(a: number): number;
function overloaded(a: any): any { return a; }

export default function App(): void {}
export { overloaded, addOne as increment };
export { helper as help } from "./helpers";
export * from "./all";
export * as utils from "./utils";

namespace Internal {
  export function hidden() {}
}

async function lazy() {
  const mod = await import("./lazy");
  return mod;
}
'''


def test_typescript_symbols():
    result = parse_source("svc.ts", "typescript", TS)
    s = by_name(result)
    assert not result.has_errors
    assert s["addOne"].kind == SymbolKind.FUNCTION
    assert s["UserService"].kind == SymbolKind.CLASS
    assert s["UserService"].bases == ("BaseService", "Disposable")
    assert s["UserService"].decorators == ("Injectable()",)
    assert s["UserService.findUser"].kind == SymbolKind.METHOD
    assert s["UserService.findUser"].is_async
    assert s["UserService.handler"].kind == SymbolKind.METHOD and s["UserService.handler"].is_async
    assert s["UserService.constructor"].kind == SymbolKind.METHOD
    assert s["UserService.create"].signature == "static create(): UserService"
    assert "UserService.cache" not in s  # plain fields are not symbols
    assert s["User"].kind == SymbolKind.INTERFACE
    assert s["UserId"].kind == SymbolKind.TYPE_ALIAS
    assert s["Role"].kind == SymbolKind.ENUM
    assert s["Shape.area"].kind == SymbolKind.METHOD
    assert s["Internal"].kind == SymbolKind.NAMESPACE
    assert s["Internal.hidden"].parent == "Internal"
    # Overload signatures do not create duplicate symbols.
    assert [x.name for x in result.symbols].count("overloaded") == 1
    assert_spans_are_exact(TS, result)


def test_typescript_ranges_signatures_and_jsdoc():
    s = by_name(parse_source("svc.ts", "typescript", TS))
    add = s["addOne"]
    # `export const` statement is included in the range.
    assert add.start_line == add.end_line == line_of(TS, "export const addOne")
    assert add.signature == "const addOne = (n: number): number"
    assert add.docstring == "Adds one."
    svc = s["UserService"]
    assert svc.start_line == line_of(TS, "@Injectable()")
    assert svc.end_line == line_of(TS, "  static create()") + 1
    find = s["UserService.findUser"]
    assert (find.start_line, find.end_line) == (
        line_of(TS, "  async findUser"), line_of(TS, "  async findUser") + 2
    )
    assert find.signature == "async findUser(id: string): Promise<User>"


def test_typescript_imports():
    imports = parse_source("svc.ts", "typescript", TS).imports
    assert Import(module="react", line=1, imported="default", alias="React") in imports
    assert Import(module="react", line=1, imported="useState", alias="useS") in imports
    assert Import(module="path", line=2, imported="*", alias="path") in imports
    assert Import(module="./types", line=3, imported="Props", is_relative=True, is_type_only=True) in imports
    assert Import(module="./side-effect.css", line=4, is_relative=True) in imports
    assert Import(module="fs", line=5, alias="fs", kind="require") in imports
    assert Import(module="./lazy", line=line_of(TS, 'import("./lazy")'), alias="mod",
                  kind="dynamic", is_relative=True) in imports


def test_typescript_exports_and_exported_flags():
    result = parse_source("svc.ts", "typescript", TS)
    exports = {(e.name, e.kind, e.local_name, e.source) for e in result.exports}
    assert ("addOne", "declaration", "addOne", None) in exports
    assert ("UserService", "declaration", "UserService", None) in exports
    assert ("User", "declaration", "User", None) in exports
    assert ("default", "default", "App", None) in exports
    assert ("overloaded", "named", "overloaded", None) in exports
    assert ("increment", "named", "addOne", None) in exports
    assert ("help", "reexport", "helper", "./helpers") in exports
    assert ("*", "reexport_all", None, "./all") in exports
    assert ("utils", "reexport", "*", "./utils") in exports
    # Exports inside a namespace are not module exports.
    assert not any(e.name == "hidden" for e in result.exports)

    s = by_name(result)
    assert s["overloaded"].is_exported  # via `export { overloaded }`
    assert s["App"].is_exported and s["addOne"].is_exported
    assert not s["lazy"].is_exported
    assert not s["UserService.findUser"].is_exported


# ------------------------------------------------------------------ javascript

JS = '''const fs = require("fs");
const { join, resolve: res } = require("path");

class Widget extends EventEmitter {
  onClick = () => {};
  static async *stream() {}
}

function build() {
  function nested() {}
  return nested;
}

exports.helper = function () {};
module.exports = { build, Widget, renamed: build };
'''


def test_javascript_commonjs_classes_and_requires():
    result = parse_source("w.js", "javascript", JS)
    s = by_name(result)
    assert not result.has_errors
    assert s["Widget"].bases == ("EventEmitter",)
    assert s["Widget.onClick"].kind == SymbolKind.METHOD
    assert s["Widget.stream"].is_async
    assert s["build.nested"].parent == "build"
    assert s["helper"].is_exported
    assert s["build"].is_exported and s["Widget"].is_exported

    assert Import(module="fs", line=1, alias="fs", kind="require") in result.imports
    assert Import(module="path", line=2, imported="join", kind="require") in result.imports
    assert Import(module="path", line=2, imported="resolve", alias="res", kind="require") in result.imports

    exports = {(e.name, e.kind, e.local_name) for e in result.exports}
    assert ("helper", "commonjs", "helper") in exports
    assert ("build", "commonjs", "build") in exports
    assert ("renamed", "commonjs", "build") in exports
    assert_spans_are_exact(JS, result)


def test_javascript_esm_default_exports():
    anon = parse_source("a.js", "javascript", "export default async function () {}\n")
    assert [(s.name, s.is_async, s.is_exported) for s in anon.symbols] == [("default", True, True)]
    assert [(e.name, e.kind) for e in anon.exports] == [("default", "default")]

    ident = parse_source("b.js", "javascript", "function main() {}\nexport default main;\n")
    assert by_name(ident)["main"].is_exported
    assert [(e.name, e.local_name) for e in ident.exports] == [("default", "main")]


def test_jsx_parses_with_javascript_grammar():
    src = "export function Button({ label }) {\n  return <button>{label}</button>;\n}\n"
    result = parse_source("Button.jsx", "javascript", src)
    assert not result.has_errors
    assert by_name(result)["Button"].end_line == 3


# ------------------------------------------------------------------------- tsx

TSX = '''import React from "react";

interface Props { title: string }

export const Card = ({ title }: Props) => {
  const [open, setOpen] = React.useState(false);
  return <div onClick={() => setOpen(!open)}>{title}</div>;
};

export default function Page() {
  return <Card title="x" />;
}
'''


def test_tsx_components():
    result = parse_source("Card.tsx", "tsx", TSX)
    s = by_name(result)
    assert not result.has_errors
    assert (s["Card"].start_line, s["Card"].end_line) == (5, 8)
    assert s["Card"].is_exported and s["Page"].is_exported
    assert s["Props"].kind == SymbolKind.INTERFACE
    assert_spans_are_exact(TSX, result)


def test_tsx_bare_ampersand_in_jsx_text_still_extracts_symbols():
    """Grammar limitation: flagged as an error, but extraction is unaffected."""
    src = "export function Page() {\n  return <p>Redis & Celery</p>;\n}\nexport const After = () => 1;\n"
    result = parse_source("Page.tsx", "tsx", src)
    assert result.has_errors and result.error_lines == (2,)
    s = by_name(result)
    assert (s["Page"].start_line, s["Page"].end_line) == (1, 3)
    assert s["After"].is_exported


def test_typescript_syntax_errors_are_reported():
    src = "export function ok() { return 1 }\nfunction broken( {\nexport class Later {}\n"
    result = parse_source("bad.ts", "typescript", src)
    assert result.has_errors and result.error_lines
    assert "ok" in {s.name for s in result.symbols}


# ---------------------------------------------------------------- registry / repo

def test_every_filtered_language_has_a_parser():
    assert set(LANGUAGES_BY_EXTENSION.values()) <= supported_languages()


def test_unsupported_language_raises():
    with pytest.raises(UnsupportedLanguageError):
        parse_source("x.rb", "ruby", "puts 1")


def _repo_files():
    for base, suffixes in (("backend/app", (".py",)), ("frontend/src", (".ts", ".tsx"))):
        for path in sorted((REPO / base).rglob("*")):
            if path.suffix in suffixes and "__pycache__" not in path.parts:
                yield path


@pytest.mark.parametrize("path", list(_repo_files()), ids=lambda p: p.relative_to(REPO).as_posix())
def test_parses_this_projects_own_source_cleanly(path):
    language = LANGUAGES_BY_EXTENSION[path.suffix]
    source = path.read_text(encoding="utf-8")
    result = parse_source(path.as_posix(), language, source)
    if result.has_errors:
        # Known tree-sitter-typescript limitation: a bare "&" in JSX text (valid for
        # TypeScript) is reported as an error. Any other error is a real failure.
        lines = source.splitlines()
        offending = [lines[n - 1] for n in result.error_lines]
        assert language == "tsx" and all(" & " in line for line in offending), result.error_lines
    assert_spans_are_exact(source, result)
    if any(marker in source for marker in ("def ", "class ", "function ", "=>")):
        assert result.symbols or result.imports
