"""Tree-sitter language registry.

To add a language: install its grammar package, register a loader here, add an
extractor, and map file extensions in ``file_filter.LANGUAGES_BY_EXTENSION``.
"""

from functools import lru_cache
from typing import Callable, Dict

from tree_sitter import Language, Parser

_LOADERS: Dict[str, Callable[[], object]] = {}


def _register(name: str):
    def decorator(fn: Callable[[], object]) -> Callable[[], object]:
        _LOADERS[name] = fn
        return fn

    return decorator


@_register("python")
def _python():
    import tree_sitter_python

    return tree_sitter_python.language()


@_register("javascript")
def _javascript():
    import tree_sitter_javascript

    return tree_sitter_javascript.language()


@_register("typescript")
def _typescript():
    import tree_sitter_typescript

    return tree_sitter_typescript.language_typescript()


@_register("tsx")
def _tsx():
    import tree_sitter_typescript

    return tree_sitter_typescript.language_tsx()


class UnsupportedLanguageError(ValueError):
    pass


def supported_languages() -> frozenset:
    return frozenset(_LOADERS)


@lru_cache(maxsize=None)
def get_language(name: str) -> Language:
    """Grammar objects are immutable and safe to share across threads."""
    loader = _LOADERS.get(name)
    if loader is None:
        raise UnsupportedLanguageError(f"No parser registered for language {name!r}")
    return Language(loader())


def new_parser(name: str) -> Parser:
    """Parsers hold mutable state, so each parse gets its own (creation is cheap)."""
    return Parser(get_language(name))
