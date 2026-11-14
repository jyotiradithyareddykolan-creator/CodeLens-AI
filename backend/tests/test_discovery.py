"""Discovery orchestration tests using an in-memory fake GitHub (no network)."""

import logging

import pytest

from app.core.config import settings
from app.indexing.discovery import (
    DiscoveredFile,
    DiscoveryError,
    discover_files,
    load_file,
    screen_content,
)
from app.indexing.file_filter import SkipReason
from app.services.github_service import GitHubRateLimitError, GitHubServiceError

COMMIT = "c" * 40


def sha(n: int) -> str:
    return f"{n:040x}"


def blob(path: str, n: int, size: int = 100, mode: str = "100644") -> dict:
    return {"path": path, "mode": mode, "type": "blob", "sha": sha(n), "size": size}


def tree(path: str, n: int) -> dict:
    return {"path": path, "mode": "040000", "type": "tree", "sha": sha(n)}


class FakeGitHub:
    """trees[(sha, recursive)] -> payload; blobs[sha] -> bytes or Exception."""

    def __init__(self, trees, blobs=None):
        self.trees = trees
        self.blobs = blobs or {}
        self.tree_calls = []

    async def get_tree(self, installation_id, owner, repo, tree_sha, recursive):
        self.tree_calls.append((tree_sha, recursive))
        return self.trees[(tree_sha, recursive)]

    async def get_blob(self, installation_id, owner, repo, blob_sha):
        value = self.blobs[blob_sha]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.asyncio
async def test_discovery_uses_single_recursive_call_and_filters_paths():
    entries = [
        tree("app", 1),
        blob("app/main.py", 2),
        blob("app/util.ts", 3),
        blob("README.md", 4),
        blob("node_modules/x/index.js", 5),
        blob("static/app.min.js", 6),
        blob(".env", 7),
        blob("link.py", 8, mode="120000"),
        {"path": "vendored", "mode": "160000", "type": "commit", "sha": sha(9)},
        blob("big.py", 10, size=settings.MAX_FILE_SIZE_BYTES + 1),
    ]
    gh = FakeGitHub({(COMMIT, True): {"sha": COMMIT, "tree": entries, "truncated": False}})
    result = await discover_files(gh, 1, "o", "r", COMMIT)

    assert result.commit_sha == COMMIT
    assert [(f.path, f.language, f.blob_sha) for f in result.files] == [
        ("app/main.py", "python", sha(2)),
        ("app/util.ts", "typescript", sha(3)),
    ]
    assert result.skipped == {
        "unsupported_language": 1,
        "excluded_directory": 1,
        "excluded_pattern": 1,
        "sensitive_filename": 1,
        "symlink": 1,
        "submodule": 1,
        "too_large": 1,
    }
    assert result.used_truncation_fallback is False
    assert gh.tree_calls == [(COMMIT, True)]


@pytest.mark.asyncio
async def test_truncated_tree_falls_back_to_directory_walk_and_prunes_excluded_dirs():
    gh = FakeGitHub(
        {
            (COMMIT, True): {"tree": [blob("partial.py", 99)], "truncated": True},
            (COMMIT, False): {
                "tree": [
                    blob("setup.py", 1),
                    tree("src", 10),
                    tree("node_modules", 20),  # must be pruned, never fetched
                    tree("big", 30),
                ],
                "truncated": False,
            },
            # src: a recursive listing fits.
            (sha(10), True): {
                "tree": [tree("pkg", 11), blob("pkg/a.py", 12), blob("b.js", 13)],
                "truncated": False,
            },
            # big: recursive listing truncated again -> list one level and recurse.
            (sha(30), True): {"tree": [], "truncated": True},
            (sha(30), False): {"tree": [blob("top.ts", 31), tree("inner", 32)], "truncated": False},
            (sha(32), True): {"tree": [blob("deep.tsx", 33)], "truncated": False},
        }
    )
    result = await discover_files(gh, 1, "o", "r", COMMIT)

    assert [f.path for f in result.files] == [
        "big/inner/deep.tsx",
        "big/top.ts",
        "setup.py",
        "src/b.js",
        "src/pkg/a.py",
    ]
    assert result.used_truncation_fallback is True
    assert result.skipped == {"excluded_directory": 1}
    assert (sha(20), True) not in gh.tree_calls and (sha(20), False) not in gh.tree_calls
    # The partial recursive result is discarded, not mixed in.
    assert "partial.py" not in [f.path for f in result.files]
    assert result.tree_requests == len(gh.tree_calls) == 6


@pytest.mark.asyncio
async def test_discovery_requires_pinned_commit_sha():
    with pytest.raises(DiscoveryError):
        await discover_files(FakeGitHub({}), 1, "o", "r", "main")


@pytest.mark.asyncio
async def test_tree_request_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(settings, "INDEX_MAX_TREE_REQUESTS", 2)
    gh = FakeGitHub(
        {
            (COMMIT, True): {"tree": [], "truncated": True},
            (COMMIT, False): {"tree": [tree("a", 1)], "truncated": False},
            (sha(1), True): {"tree": [], "truncated": False},
        }
    )
    with pytest.raises(DiscoveryError):
        await discover_files(gh, 1, "o", "r", COMMIT)


@pytest.mark.asyncio
async def test_candidate_file_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(settings, "INDEX_MAX_CANDIDATE_FILES", 2)
    entries = [blob(f"m{i}.py", i + 1) for i in range(3)]
    gh = FakeGitHub({(COMMIT, True): {"tree": entries, "truncated": False}})
    with pytest.raises(DiscoveryError):
        await discover_files(gh, 1, "o", "r", COMMIT)


@pytest.mark.asyncio
async def test_entries_with_invalid_shas_are_skipped():
    entries = [{"path": "ok.py", "mode": "100644", "type": "blob", "sha": "nope", "size": 5}]
    gh = FakeGitHub({(COMMIT, True): {"tree": entries, "truncated": False}})
    result = await discover_files(gh, 1, "o", "r", COMMIT)
    assert result.files == [] and result.skipped == {"invalid_path": 1}


FILE = DiscoveredFile(path="app/settings.py", blob_sha=sha(1), size=10, language="python")


def test_screen_content_returns_text_for_clean_files():
    loaded = screen_content(FILE, b"DEBUG = False\n")
    assert loaded.included and loaded.text == "DEBUG = False\n"


def test_files_with_secrets_are_excluded_and_value_is_never_logged(caplog):
    secret = "".join(["gh", "p_", "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"])
    data = f'x = 1\nGITHUB_TOKEN = "{secret}"\n'.encode()
    with caplog.at_level(logging.DEBUG):
        loaded = screen_content(FILE, data)

    assert not loaded.included
    assert loaded.skip_reason == SkipReason.SECRET_DETECTED
    assert loaded.secret_rules == ("github_token",)
    assert loaded.text is None
    assert secret not in caplog.text
    assert secret not in repr(loaded)
    assert "app/settings.py" in caplog.text and "line 2" in caplog.text


@pytest.mark.asyncio
async def test_load_file_handles_fetch_errors_and_propagates_rate_limits():
    gh = FakeGitHub({}, blobs={sha(1): GitHubServiceError("HTTP 500", 500)})
    loaded = await load_file(gh, 1, "o", "r", FILE)
    assert loaded.skip_reason == SkipReason.FETCH_FAILED

    gh = FakeGitHub({}, blobs={sha(1): GitHubRateLimitError("limited", 300)})
    with pytest.raises(GitHubRateLimitError):
        await load_file(gh, 1, "o", "r", FILE)

    gh = FakeGitHub({}, blobs={sha(1): b"\x00\x01binary"})
    assert (await load_file(gh, 1, "o", "r", FILE)).skip_reason == SkipReason.BINARY
