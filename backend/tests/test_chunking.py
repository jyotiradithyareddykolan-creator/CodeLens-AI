"""Syntax-aware chunking tests (real Tree-sitter grammars, no mocks)."""

import hashlib
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.indexing.chunking import Chunk, ChunkType, chunk_file, chunk_source
from app.indexing.discovery import DiscoveredFile, screen_content
from app.indexing.file_filter import LANGUAGES_BY_EXTENSION
from app.indexing.parsing import parse_file
from app.indexing.tokens import estimate_tokens

REPO = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40
REPO_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


def check_invariants(text: str, chunks, max_tokens: int = None) -> None:
    """Exact slices, ordered, non-overlapping, full non-whitespace coverage, size limits."""
    limit = max_tokens or settings.CHUNK_MAX_TOKENS
    data = text.encode("utf-8")
    covered = bytearray(len(data))
    previous_end = 0
    ids = set()
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.start_byte >= previous_end, "chunks overlap or are out of order"
        assert c.end_byte > c.start_byte
        assert data[c.start_byte:c.end_byte].decode("utf-8") == c.content
        assert c.start_line == data[: c.start_byte].count(b"\n") + 1
        assert c.end_line == data[: c.end_byte - 1].count(b"\n") + 1
        assert c.content.strip(), "whitespace-only chunk"
        assert c.token_count == estimate_tokens(c.content)
        assert c.context_token_count == (estimate_tokens(c.context) if c.context else 0)
        assert c.total_token_count <= limit, (c.qualified_name, c.total_token_count, limit)
        assert c.content_hash == hashlib.sha256(c.content.encode("utf-8")).hexdigest()
        assert 0 <= c.part_index < c.part_count
        assert c.id not in ids
        ids.add(c.id)
        for i in range(c.start_byte, c.end_byte):
            covered[i] += 1
        previous_end = c.end_byte
    uncovered = bytes(b for b, n in zip(data, covered) if n == 0)
    assert not uncovered.strip(), f"code missing from chunks: {uncovered.strip()[:80]!r}"
    assert max(covered, default=0) <= 1, "code duplicated across chunks"


def symbols_of(chunks):
    return [(c.chunk_type, c.qualified_name) for c in chunks]


def by_symbol(chunks, name):
    return [c for c in chunks if c.qualified_name == name]


# ---------------------------------------------------------------------- python

PY_SMALL = '''"""Module docstring."""
import os
from typing import List

LIMIT = 10


# Adds numbers.
# Second comment line.
@cache
@other(x=1)
def add(a: int, b: int) -> int:
    """Return the sum."""
    def helper():
        return a
    return a + b


class Store(Base):
    """Keeps items."""

    def __init__(self):
        self.items: List[int] = []

    def put(self, item):
        self.items.append(item)


if __name__ == "__main__":
    print(add(1, 2))
'''


def test_python_small_file_one_chunk_per_top_level_symbol():
    chunks = chunk_source("m.py", "python", PY_SMALL, repository_id=REPO_ID, commit_sha=COMMIT)
    check_invariants(PY_SMALL, chunks)
    assert symbols_of(chunks) == [
        (ChunkType.MODULE, None),
        ("function", "add"),
        ("class", "Store"),
        (ChunkType.MODULE, None),
    ]
    add = chunks[1]
    # Leading comment block and decorators belong to the function chunk.
    assert add.content.startswith("# Adds numbers.\n# Second comment line.\n@cache\n@other(x=1)\ndef add")
    assert add.start_line == 8 and add.end_line == 16
    # Nested function stays inside its parent chunk.
    assert "def helper():" in add.content
    assert add.symbol_kind == "function" and add.parent_symbol is None and add.context is None
    store = chunks[2]
    assert "def put(self, item):" in store.content and store.part_count == 1
    assert all(c.repository_id == REPO_ID and c.commit_sha == COMMIT for c in chunks)
    assert all(c.language == "python" and c.file_path == "m.py" for c in chunks)


def test_imports_only_region_is_typed_imports():
    src = "import os\nimport sys\nfrom a import b\n\n\ndef f():\n    return os\n"
    chunks = chunk_source("i.py", "python", src)
    check_invariants(src, chunks)
    assert chunks[0].chunk_type == ChunkType.IMPORTS
    assert chunks[0].content == "import os\nimport sys\nfrom a import b\n"
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 3)


def make_big_function(statements: int = 150) -> str:
    body = "".join(f"    value_{i} = transform(value_{max(i - 1, 0)}, {i}, 'label {i}')\n" for i in range(statements))
    return f"def big(seed):\n    \"\"\"Long function.\"\"\"\n    value_0 = seed\n{body}    return value_{statements - 1}\n"


def test_large_function_splits_at_statement_boundaries():
    src = "import os\n\n\n" + make_big_function()
    chunks = chunk_source("big.py", "python", src, max_tokens=200)
    check_invariants(src, chunks, 200)
    parts = by_symbol(chunks, "big")
    assert len(parts) > 3
    assert [p.part_index for p in parts] == list(range(len(parts)))
    assert all(p.part_count == len(parts) for p in parts)
    # The signature travels with the first statements, not as a chunk of its own.
    assert parts[0].content.startswith("def big(seed):\n    \"\"\"Long function.\"\"\"\n    value_0 = seed\n")
    # Every part starts at the beginning of a statement line.
    for p in parts[1:]:
        assert p.content.startswith("    value_") or p.content.startswith("    return")
        assert p.context == "def big(seed)"
    assert parts[-1].content.rstrip().endswith("return value_149")


def test_function_exactly_at_limit_is_kept_whole_and_one_over_splits():
    src = make_big_function(20)
    whole = chunk_source("f.py", "python", src, max_tokens=2048)
    assert len(whole) == 1
    size = whole[0].token_count
    exact = chunk_source("f.py", "python", src, max_tokens=size)
    assert len(exact) == 1 and exact[0].content == src
    over = chunk_source("f.py", "python", src, max_tokens=size - 1)
    assert len(over) > 1
    check_invariants(src, over, size - 1)


def make_big_class(methods: int = 30, big_method: bool = False) -> str:
    out = ['class Service(BaseService, Mixin):\n    """Service docs."""\n\n    retries = 3\n    timeout = 30\n\n']
    for i in range(methods):
        out.append(f"    def method_{i}(self, value):\n        result = self.helper(value, {i})\n        return result\n\n")
    if big_method:
        out.append("    def giant(self):\n" + "".join(f"        step_{i} = self.run({i}, 'stage {i}')\n" for i in range(120)) + "        return step_0\n\n")
    out.append("    limit = 99\n")
    return "".join(out)


def test_large_class_splits_into_header_methods_and_trailing_members():
    src = make_big_class()
    chunks = chunk_source("svc.py", "python", src, max_tokens=150)
    check_invariants(src, chunks, 150)
    header = chunks[0]
    assert header.chunk_type == "class" and header.qualified_name == "Service"
    assert header.content.startswith('class Service(BaseService, Mixin):\n    """Service docs."""')
    assert "retries = 3" in header.content
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert [m.qualified_name for m in methods] == [f"Service.method_{i}" for i in range(30)]
    for m in methods:
        assert m.parent_symbol == "Service"
        assert m.context == "class Service(BaseService, Mixin)"
        assert m.content.startswith(f"    def {m.symbol_name}(self, value):")
    trailing = chunks[-1]
    assert trailing.qualified_name == "Service" and trailing.content == "    limit = 99\n"
    assert trailing.part_index == trailing.part_count - 1


def test_oversized_method_inside_oversized_class_gets_nested_context():
    src = make_big_class(methods=3, big_method=True)
    chunks = chunk_source("svc.py", "python", src, max_tokens=150)
    check_invariants(src, chunks, 150)
    giant = by_symbol(chunks, "Service.giant")
    assert len(giant) > 1
    assert giant[0].content.startswith("    def giant(self):\n        step_0")
    assert all(p.context == "class Service(BaseService, Mixin)\ndef giant(self)" for p in giant)


def test_oversized_single_statement_is_split_inside_its_block():
    inner = "".join(f"        handle_{i}(payload, {i})\n" for i in range(150))
    src = f"def router(payload):\n    if payload.kind == 'a':\n{inner}    return None\n"
    chunks = chunk_source("r.py", "python", src, max_tokens=150)
    check_invariants(src, chunks, 150)
    assert len(chunks) > 2
    assert chunks[0].content.startswith("def router(payload):\n    if payload.kind == 'a':\n        handle_0")


def test_single_overlong_line_is_split_without_losing_characters():
    literal = "é" * 300 + "x" * 3000
    src = f'BLOB = "{literal}"\n\n\ndef after():\n    return BLOB\n'
    chunks = chunk_source("blob.py", "python", src, max_tokens=100)
    check_invariants(src, chunks, 100)
    module_parts = [c for c in chunks if c.chunk_type == ChunkType.MODULE]
    assert len(module_parts) > 5
    assert all(c.start_line == c.end_line == 1 for c in module_parts)
    assert "".join(c.content for c in module_parts).rstrip("\n") == f'BLOB = "{literal}"'
    assert chunks[-1].qualified_name == "after"


def test_file_without_symbols():
    src = "import os\n\nprint(os.getcwd())\nfor i in range(3):\n    print(i)\n"
    chunks = chunk_source("script.py", "python", src)
    check_invariants(src, chunks)
    assert [c.chunk_type for c in chunks] == [ChunkType.MODULE]
    assert chunks[0].content == src


@pytest.mark.parametrize("src", ["", "\n\n   \n", "\r\n\r\n"])
def test_empty_and_whitespace_only_files_produce_no_chunks(src):
    assert chunk_source("empty.py", "python", src) == []


def test_unicode_and_crlf_offsets_and_lines_are_exact():
    src = '# café ☕\r\nimport os\r\n\r\n\r\ndef grüße(name):\r\n    return f"Hallo {name} 👋"\r\n\r\n\r\nclass Ünïcode:\r\n    pass\r\n'
    chunks = chunk_source("u.py", "python", src)
    check_invariants(src, chunks)
    fn = by_symbol(chunks, "grüße")[0]
    assert (fn.start_line, fn.end_line) == (5, 6)
    assert fn.content == 'def grüße(name):\r\n    return f"Hallo {name} 👋"\r\n'
    cls = by_symbol(chunks, "Ünïcode")[0]
    assert (cls.start_line, cls.end_line) == (9, 10)


def test_malformed_python_still_chunks_completely():
    src = "def ok():\n    return 1\n\ndef broken(:\n    pass\n\nclass Fine:\n    x = 1\n"
    chunks = chunk_source("bad.py", "python", src)
    check_invariants(src, chunks)
    assert all(c.has_parse_errors for c in chunks)
    assert "ok" in {c.qualified_name for c in chunks}


# ------------------------------------------------------------------ javascript

JS = '''const express = require("express");

/**
 * Build the router.
 * @returns {Router}
 */
function buildRouter(app) {
  const inner = () => app.use(express.json());
  return inner;
}

const handler = async (req, res) => {
  res.send("ok");
};

class Controller extends Base {
  list() { return []; }
  create = (body) => body;
}

module.exports = { buildRouter, handler, Controller };
'''


def test_javascript_functions_arrows_classes_and_jsdoc():
    chunks = chunk_source("app.js", "javascript", JS)
    check_invariants(JS, chunks)
    assert symbols_of(chunks) == [
        (ChunkType.MODULE, None),
        ("function", "buildRouter"),
        ("function", "handler"),
        ("class", "Controller"),
        (ChunkType.MODULE, None),
    ]
    assert chunks[1].content.startswith("/**\n * Build the router.")
    assert chunks[1].start_line == 3
    assert chunks[2].content == 'const handler = async (req, res) => {\n  res.send("ok");\n};\n'


def test_split_js_class_closing_brace_is_not_a_chunk_of_its_own():
    methods = "".join(f"  method{i}(value) {{\n    return this.run(value, {i});\n  }}\n\n" for i in range(30))
    src = f"export class Big extends Base {{\n  constructor() {{ super(); }}\n\n{methods}}}\n"
    chunks = chunk_source("big.js", "javascript", src, max_tokens=100)
    check_invariants(src, chunks, 100)
    assert not any(c.content.strip() in ("}", "};") for c in chunks)
    assert chunks[0].content.startswith("export class Big extends Base {")
    assert chunks[-1].content.rstrip().endswith("}\n}") or chunks[-1].content.rstrip().endswith("}")


# ------------------------------------------------------------------ typescript

TS = '''import { Injectable } from "@angular/core";
import type { User } from "./models";

/** Possible roles. */
export enum Role { Admin = "admin", Member = "member" }

export interface Repo<T> {
  get(id: string): Promise<T>;
  save(item: T): Promise<void>;
}

export type UserId = string;

@Injectable()
export class UserService {
  constructor(private readonly repo: Repo<User>) {}

  async find(id: UserId): Promise<User> {
    return this.repo.get(id);
  }
}

export const isAdmin = (u: User): boolean => u.role === Role.Admin;
'''


def test_typescript_declarations_are_individual_chunks():
    chunks = chunk_source("svc.ts", "typescript", TS, commit_sha=COMMIT)
    check_invariants(TS, chunks)
    assert symbols_of(chunks) == [
        (ChunkType.IMPORTS, None),
        ("enum", "Role"),
        ("interface", "Repo"),
        ("type_alias", "UserId"),
        ("class", "UserService"),
        ("function", "isAdmin"),
    ]
    role = by_symbol(chunks, "Role")[0]
    assert role.content.startswith("/** Possible roles. */\nexport enum Role")
    service = by_symbol(chunks, "UserService")[0]
    assert service.content.startswith("@Injectable()\nexport class UserService {")
    lines = TS.splitlines()
    assert service.end_line == lines.index("}", lines.index("export class UserService {")) + 1


def test_large_typescript_class_methods_get_class_context():
    methods = "".join(
        f"  async op{i}(input: Input): Promise<Output> {{\n    const r = await this.client.call('{i}', input);\n    return r;\n  }}\n\n"
        for i in range(20)
    )
    src = f"export abstract class Client<T> extends Base<T> implements Api {{\n  private cache = new Map<string, T>();\n\n{methods}}}\n"
    chunks = chunk_source("client.ts", "typescript", src, max_tokens=120)
    check_invariants(src, chunks, 120)
    ops = [c for c in chunks if c.chunk_type == "method"]
    assert len(ops) == 20
    # Context is the parser's signature: the declaration itself, without `export`.
    assert all(c.context == "abstract class Client<T> extends Base<T> implements Api" for c in ops)


def test_long_context_is_capped_so_content_still_fits():
    params = ", ".join(f"parameter_number_{i}: int = {i}" for i in range(40))
    body = "".join(f"    total += parameter_number_{i % 40} * {i}\n" for i in range(80))
    src = f"def wide({params}):\n    total = 0\n{body}    return total\n"
    chunks = chunk_source("w.py", "python", src, max_tokens=120)
    check_invariants(src, chunks, 120)
    later = [c for c in chunks if c.part_index > 0]
    assert later and all(c.context.endswith("…") and c.context_token_count <= 30 for c in later)


# ------------------------------------------------------------------------- tsx

TSX = '''import React, { useState } from "react";

type Props = { title: string; items: string[] };

/** Renders a list. */
export function List({ title, items }: Props) {
  const [open, setOpen] = useState(true);
  return (
    <section>
      <h2 onClick={() => setOpen(!open)}>{title}</h2>
      {open && items.map((i) => <li key={i}>{i}</li>)}
    </section>
  );
}

export const Empty = () => <p>Nothing here</p>;

export default List;
'''


def test_tsx_components():
    chunks = chunk_source("List.tsx", "tsx", TSX)
    check_invariants(TSX, chunks)
    assert symbols_of(chunks) == [
        (ChunkType.IMPORTS, None),
        ("type_alias", "Props"),
        ("function", "List"),
        ("function", "Empty"),
        (ChunkType.MODULE, None),
    ]
    assert by_symbol(chunks, "List")[0].content.startswith("/** Renders a list. */\nexport function List(")


def test_large_jsx_component_splits_inside_jsx():
    rows = "".join(f'      <Row key="{i}" label="Row number {i}" value={{data[{i}]}} />\n' for i in range(120))
    src = f"export function Table({{ data }}) {{\n  return (\n    <table>\n{rows}    </table>\n  );\n}}\n"
    chunks = chunk_source("Table.tsx", "tsx", src, max_tokens=150)
    check_invariants(src, chunks, 150)
    assert len(chunks) > 3
    assert all(c.qualified_name == "Table" for c in chunks)


def test_malformed_typescript_still_chunks_completely():
    src = "export function ok() { return 1 }\nfunction broken( {\nexport class Later { run() {} }\n"
    chunks = chunk_source("bad.ts", "typescript", src)
    check_invariants(src, chunks)
    assert all(c.has_parse_errors for c in chunks)


# ------------------------------------------------------------------ identity & config

def test_chunk_ids_are_deterministic_and_commit_scoped():
    a = chunk_source("m.py", "python", PY_SMALL, repository_id=REPO_ID, commit_sha=COMMIT)
    b = chunk_source("m.py", "python", PY_SMALL, repository_id=REPO_ID, commit_sha=COMMIT)
    c = chunk_source("m.py", "python", PY_SMALL, repository_id=REPO_ID, commit_sha="b" * 40)
    assert [x.id for x in a] == [x.id for x in b]
    assert not {x.id for x in a} & {x.id for x in c}
    # Same content at another commit keeps the same hash, so embeddings can be reused.
    assert [x.content_hash for x in a] == [x.content_hash for x in c]


def test_chunk_size_setting_is_validated():
    for bad in (10, 5000):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, CHUNK_MAX_TOKENS=bad)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, CHUNK_TOKEN_ESTIMATE_MULTIPLIER=10)


def test_default_limit_comes_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "CHUNK_MAX_TOKENS", 120)
    src = make_big_function(60)
    chunks = chunk_source("f.py", "python", src)
    check_invariants(src, chunks, 120)
    assert len(chunks) > 1


# ------------------------------------------------------------------ token estimator

def test_token_estimator_basics(monkeypatch):
    assert estimate_tokens("") == 0
    assert estimate_tokens("x") == 1
    # camelCase and snake_case pieces are counted separately.
    assert estimate_tokens("getUserById") >= 4
    assert estimate_tokens("12345") == 5  # one per digit
    sample = "def handler(request):\n    return {'status': 200, 'body': request.json()}\n"
    # Never more than one token per character (the char-split fallback relies on this).
    assert estimate_tokens(sample) <= len(sample)
    assert estimate_tokens(sample + sample) >= estimate_tokens(sample)
    base = estimate_tokens(sample)
    monkeypatch.setattr(settings, "CHUNK_TOKEN_ESTIMATE_MULTIPLIER", 2.0)
    assert estimate_tokens(sample) == pytest.approx(base * 2, abs=1)


def test_token_estimator_upper_bound_holds_for_unicode():
    text = "日本語のコメント 👋 émoji"
    assert len(text) // 2 <= estimate_tokens(text) <= len(text)


# ------------------------------------------------------------------ integration

def test_discovery_parser_and_chunker_integration():
    """Discovered blob -> content screening -> parse -> chunks with repo/commit context."""
    source = (TS + "\n").encode("utf-8")
    discovered = DiscoveredFile(path="src/svc.ts", blob_sha="c" * 40, size=len(source), language="typescript")
    loaded = screen_content(discovered, source)
    assert loaded.included
    parsed = parse_file(discovered.path, discovered.language, loaded.text)
    chunks = chunk_file(parsed, repository_id=REPO_ID, commit_sha=COMMIT)
    check_invariants(loaded.text, chunks)
    # Chunk symbols line up exactly with parser symbols.
    symbols = {s.qualified_name: s for s in parsed.result.symbols}
    for chunk in chunks:
        if chunk.qualified_name and chunk.part_count == 1:
            symbol = symbols[chunk.qualified_name]
            assert chunk.start_byte <= symbol.start_byte and symbol.end_byte <= chunk.end_byte
            assert chunk.start_line <= symbol.start_line and symbol.end_line <= chunk.end_line
            assert chunk.symbol_kind == symbol.kind and chunk.parent_symbol == symbol.parent
    assert {c.file_path for c in chunks} == {"src/svc.ts"}


def _repo_sources():
    for base, suffixes in (("backend/app", (".py",)), ("backend/tests", (".py",)), ("frontend/src", (".ts", ".tsx"))):
        for path in sorted((REPO / base).rglob("*")):
            if path.suffix in suffixes and "__pycache__" not in path.parts:
                yield path


@pytest.mark.parametrize("max_tokens", [500, 96, 64])
def test_chunks_this_projects_own_source(max_tokens):
    """Every project file chunks with exact coverage at the default and a tiny limit."""
    files = list(_repo_sources())
    assert len(files) > 50
    for path in files:
        text = path.read_text(encoding="utf-8")
        chunks = chunk_source(path.relative_to(REPO).as_posix(), LANGUAGES_BY_EXTENSION[path.suffix], text,
                              max_tokens=max_tokens)
        check_invariants(text, chunks, max_tokens)


def test_several_declarations_on_one_line_are_not_lost_or_duplicated():
    src = 'setup(); function a() { return 1 } const b = () => 2; class C {}\nexport { a, b, C };\n'
    chunks = chunk_source("one.js", "javascript", src)
    check_invariants(src, chunks)
    names = {c.qualified_name for c in chunks}
    assert {"a", "b", "C"} <= names
    assert all(c.start_line == 1 for c in chunks if c.qualified_name in {"a", "b", "C"})


@pytest.mark.parametrize("max_tokens", [500, 64])
def test_randomly_corrupted_project_files_still_chunk_exactly(max_tokens):
    """Mutation stress test: deleting random lines creates malformed syntax everywhere."""
    import random

    rng = random.Random(1234)
    files = [p for p in _repo_sources()][:: 3]
    assert len(files) >= 15
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [line for line in lines if rng.random() > 0.15]
        text = "".join(kept)
        chunks = chunk_source(path.name, LANGUAGES_BY_EXTENSION[path.suffix], text, max_tokens=max_tokens)
        check_invariants(text, chunks, max_tokens)
