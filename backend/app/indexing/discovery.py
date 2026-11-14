"""Commit-pinned repository file discovery.

Flow used by the indexing pipeline (Milestone 6):

1. ``resolve_commit_sha`` pins the run to one commit.
2. :func:`discover_files` lists the tree at that commit and applies path filters,
   without downloading file contents.
3. :func:`load_file` downloads each candidate blob and applies content filters and
   secret screening, returning text only for files that are safe to index.

Truncated trees: GitHub truncates recursive tree listings for very large
repositories. We then walk the tree directory by directory, trying a recursive
listing for each subtree first and descending further only where that is truncated
too. Excluded directories (``node_modules``, ``dist`` ...) are pruned without being
fetched.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from app.core.config import settings
from app.indexing.file_filter import (
    PathDecision,
    SkipReason,
    classify_content,
    classify_path,
    is_excluded_directory,
)
from app.indexing.secret_scanner import scan_text
from app.services.github_service import (
    GitHubRateLimitError,
    GitHubService,
    GitHubServiceError,
    is_git_sha,
)

logger = logging.getLogger(__name__)


class DiscoveryError(Exception):
    """Discovery could not complete (limits exceeded or malformed data). Safe to log."""


@dataclass(frozen=True)
class DiscoveredFile:
    path: str
    blob_sha: str
    size: int
    language: str


@dataclass
class DiscoveryResult:
    commit_sha: str
    files: List[DiscoveredFile]
    skipped: Dict[str, int] = field(default_factory=dict)
    used_truncation_fallback: bool = False
    tree_requests: int = 0

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())


@dataclass(frozen=True)
class LoadedFile:
    file: DiscoveredFile
    text: Optional[str] = None
    skip_reason: Optional[SkipReason] = None
    # Rule IDs and line numbers only; never the matched values.
    secret_rules: Tuple[str, ...] = ()

    @property
    def included(self) -> bool:
        return self.text is not None


class _TreeWalker:
    def __init__(self, github: GitHubService, installation_id: int, owner: str, repo: str):
        self.github = github
        self.installation_id = installation_id
        self.owner = owner
        self.repo = repo
        self.requests = 0
        self.pruned_directories = 0

    async def _get_tree(self, sha: str, recursive: bool) -> dict:
        self.requests += 1
        if self.requests > settings.INDEX_MAX_TREE_REQUESTS:
            raise DiscoveryError(
                f"Repository tree needs more than {settings.INDEX_MAX_TREE_REQUESTS} API requests"
            )
        return await self.github.get_tree(self.installation_id, self.owner, self.repo, sha, recursive)

    async def entries(self, commit_sha: str) -> Tuple[List[dict], bool]:
        """Return (all non-tree entries with full paths, used_fallback)."""
        root = await self._get_tree(commit_sha, recursive=True)
        if not root.get("truncated"):
            return [e for e in root["tree"] if e.get("type") != "tree"], False

        logger.info(
            "Recursive tree for %s/%s@%s is truncated; walking directories",
            self.owner, self.repo, commit_sha[:12],
        )
        collected: List[dict] = []
        root_flat = await self._get_tree(commit_sha, recursive=False)
        await self._walk_listing(root_flat, "", collected)
        return collected, True

    async def _walk_listing(self, listing: dict, prefix: str, out: List[dict]) -> None:
        if listing.get("truncated"):
            raise DiscoveryError("A single directory listing exceeded GitHub's tree limits")
        for entry in listing["tree"]:
            path = f"{prefix}{entry.get('path', '')}"
            if entry.get("type") == "tree":
                if is_excluded_directory(path):
                    self.pruned_directories += 1
                    continue
                await self._walk_subtree(entry.get("sha", ""), f"{path}/", out)
            else:
                out.append({**entry, "path": path})
            self._check_size(out)

    async def _walk_subtree(self, sha: str, prefix: str, out: List[dict]) -> None:
        if not is_git_sha(sha):
            raise DiscoveryError("Malformed tree entry")
        subtree = await self._get_tree(sha, recursive=True)
        if not subtree.get("truncated"):
            for entry in subtree["tree"]:
                if entry.get("type") != "tree":
                    out.append({**entry, "path": f"{prefix}{entry.get('path', '')}"})
            self._check_size(out)
            return
        await self._walk_listing(await self._get_tree(sha, recursive=False), prefix, out)

    @staticmethod
    def _check_size(out: List[dict]) -> None:
        # Generous bound on raw entries; candidate files are limited separately.
        if len(out) > settings.INDEX_MAX_CANDIDATE_FILES * 10:
            raise DiscoveryError("Repository has too many files to index")


def filter_entries(entries: Iterable[dict]) -> Tuple[List[DiscoveredFile], Counter]:
    """Apply path-level filters to raw tree entries."""
    files: List[DiscoveredFile] = []
    skipped: Counter = Counter()
    for entry in entries:
        path = entry.get("path")
        sha = entry.get("sha")
        size = entry.get("size")
        decision: PathDecision = classify_path(
            path if isinstance(path, str) else "",
            mode=str(entry.get("mode", "100644")),
            entry_type=str(entry.get("type", "blob")),
            size=size if isinstance(size, int) else None,
        )
        if decision.included and not is_git_sha(sha):
            decision = PathDecision(skip_reason=SkipReason.INVALID_PATH)
        if not decision.included:
            skipped[decision.skip_reason.value] += 1
            continue
        files.append(
            DiscoveredFile(path=path, blob_sha=sha, size=size or 0, language=decision.language)
        )
    files.sort(key=lambda f: f.path)
    return files, skipped


async def discover_files(
    github: GitHubService, installation_id: int, owner: str, repo: str, commit_sha: str
) -> DiscoveryResult:
    """List indexable files at ``commit_sha`` (metadata only, no contents)."""
    if not is_git_sha(commit_sha):
        raise DiscoveryError("Discovery must be pinned to a full commit SHA")

    walker = _TreeWalker(github, installation_id, owner, repo)
    entries, used_fallback = await walker.entries(commit_sha)
    files, skipped = filter_entries(entries)
    if walker.pruned_directories:
        skipped[SkipReason.EXCLUDED_DIRECTORY.value] += walker.pruned_directories

    if len(files) > settings.INDEX_MAX_CANDIDATE_FILES:
        raise DiscoveryError(
            f"Repository has {len(files)} indexable files, above the limit of "
            f"{settings.INDEX_MAX_CANDIDATE_FILES}"
        )

    logger.info(
        "Discovered %d indexable files in %s/%s@%s (%d skipped, %d tree requests%s)",
        len(files), owner, repo, commit_sha[:12], sum(skipped.values()), walker.requests,
        ", truncation fallback" if used_fallback else "",
    )
    return DiscoveryResult(
        commit_sha=commit_sha,
        files=files,
        skipped=dict(skipped),
        used_truncation_fallback=used_fallback,
        tree_requests=walker.requests,
    )


def screen_content(file: DiscoveredFile, data: bytes) -> LoadedFile:
    """Apply content filters and secret screening to downloaded bytes."""
    result = classify_content(data)
    if isinstance(result, SkipReason):
        return LoadedFile(file=file, skip_reason=result)

    findings = scan_text(result)
    if findings:
        rules = tuple(sorted({f.rule_id for f in findings}))
        # Path, rule IDs and line numbers only. Never log matched values.
        logger.warning(
            "Excluded %s from indexing: possible secret (%s; first at line %d)",
            file.path, ", ".join(rules), findings[0].line,
        )
        return LoadedFile(file=file, skip_reason=SkipReason.SECRET_DETECTED, secret_rules=rules)
    return LoadedFile(file=file, text=result)


async def load_file(
    github: GitHubService, installation_id: int, owner: str, repo: str, file: DiscoveredFile
) -> LoadedFile:
    """Download one blob and screen it. Fetch errors become FETCH_FAILED results,
    except rate-limit errors, which propagate so the caller can back off."""
    try:
        data = await github.get_blob(installation_id, owner, repo, file.blob_sha)
    except GitHubRateLimitError:
        raise
    except GitHubServiceError as exc:
        logger.warning("Could not fetch %s: %s", file.path, exc)
        return LoadedFile(file=file, skip_reason=SkipReason.FETCH_FAILED)
    return screen_content(file, data)
