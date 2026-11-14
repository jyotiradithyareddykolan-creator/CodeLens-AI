"""Resolve import specifiers to repository file paths (best effort, no I/O).

Python: relative imports from the importer's package; absolute imports matched
against repository paths, also when the package lives under a sub-directory
(``backend/app/...``). ``from pkg import name`` also tries ``pkg/name.py``.

JavaScript/TypeScript: relative specifiers with extension/index resolution
(including ``./x.js`` -> ``x.ts`` for TypeScript ESM), and the common ``@/`` alias for
``src/``. Bare package imports (``react``) are external and resolve to ``None``.
"""

import posixpath
from typing import Iterable, List, Optional, Set

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".d.ts")


class PathIndex:
    """Fast exact and suffix lookups over the repository's indexed paths."""

    def __init__(self, paths: Iterable[str]):
        self.paths: Set[str] = set(paths)
        self._by_name: dict = {}
        for path in self.paths:
            self._by_name.setdefault(posixpath.basename(path), []).append(path)

    def exact(self, candidate: str) -> Optional[str]:
        return candidate if candidate in self.paths else None

    def by_suffix(self, candidate: str, importer: str) -> Optional[str]:
        """A path ending with ``/candidate``; prefer the one closest to the importer."""
        if self.exact(candidate):
            return candidate
        matches = [p for p in self._by_name.get(posixpath.basename(candidate), []) if p.endswith("/" + candidate)]
        if not matches:
            return None
        return max(matches, key=lambda p: (len(posixpath.commonprefix([p, importer])), -len(p)))


def _python_candidates(module: str, imported: Optional[str], importer: str) -> List[str]:
    level = len(module) - len(module.lstrip("."))
    rest = module[level:].replace(".", "/")
    out: List[str] = []
    if level:
        base = posixpath.dirname(importer)
        for _ in range(level - 1):
            base = posixpath.dirname(base)
        target = posixpath.join(base, rest) if rest else base
    else:
        target = rest
    if not target:
        return out
    if rest:
        out += [f"{target}.py", f"{target}/__init__.py"]
    if imported and imported != "*":
        out += [f"{target}/{imported}.py", f"{target}/{imported}/__init__.py"]
    if not rest:
        out.append(f"{target}/__init__.py")
    return [posixpath.normpath(c) for c in out]


def _js_candidates(module: str, importer: str) -> List[str]:
    if module.startswith("."):
        target = posixpath.normpath(posixpath.join(posixpath.dirname(importer), module))
    elif module.startswith("@/"):
        target = "src/" + module[2:]
    else:
        return []
    out = [target]
    root, ext = posixpath.splitext(target)
    if ext in (".js", ".jsx", ".mjs", ".cjs"):
        out += [root + e for e in (".ts", ".tsx", ".mts", ".cts")]
    out += [target + e for e in JS_EXTENSIONS]
    out += [f"{target}/index{e}" for e in JS_EXTENSIONS]
    return out


def resolve_import(importer: str, language: str, module: str, imported: Optional[str],
                   index: PathIndex) -> Optional[str]:
    if not module:
        return None
    if language == "python":
        relative = module.startswith(".")
        for candidate in _python_candidates(module, imported, importer):
            if candidate.startswith(".."):
                continue
            found = index.exact(candidate) if relative else index.by_suffix(candidate, importer)
            if found:
                return found
        return None
    candidates = _js_candidates(module, importer)
    alias = module.startswith("@/")
    for candidate in candidates:
        if candidate.startswith(".."):
            continue
        found = index.by_suffix(candidate, importer) if alias else index.exact(candidate)
        if found:
            return found
    return None
