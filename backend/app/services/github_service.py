"""GitHub App REST client (read-only).

All requests go through :meth:`GitHubService._send`, which provides:

* retries with exponential backoff and jitter for transient failures
  (network errors, timeouts, HTTP 500/502/503/504);
* primary and secondary rate-limit handling (HTTP 403/429 with ``Retry-After``,
  ``X-RateLimit-Remaining: 0`` / ``X-RateLimit-Reset``), waiting up to
  ``GITHUB_MAX_RETRY_WAIT_SECONDS`` before failing with :class:`GitHubRateLimitError`;
* one automatic refresh of a rejected (401) installation token.

Error messages never contain tokens, request headers or raw response bodies.
CodeLens only performs GET requests against repository content.
"""

import asyncio
import logging
import os
import random
import re
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from jose import jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

GITHUB_API_VERSION = "2022-11-28"
# Hard stop for paginated listings so a misbehaving API cannot loop forever.
MAX_PAGES = 50
RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
# Git object IDs: SHA-1 (40 hex) or SHA-256 (64 hex) repositories.
GIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class GitHubServiceError(Exception):
    """Raised for GitHub API communication errors. Messages are safe to log."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class GitHubRateLimitError(GitHubServiceError):
    """Rate limit still exhausted after the maximum permitted wait."""

    def __init__(self, message: str, retry_after_seconds: Optional[float] = None):
        super().__init__(message, 429)
        self.retry_after_seconds = retry_after_seconds


class GitHubNotFoundError(GitHubServiceError):
    """The requested object does not exist or is not visible to the installation."""

    def __init__(self, message: str):
        super().__init__(message, 404)


def _error_detail(response: httpx.Response) -> str:
    """Short, non-sensitive description of a GitHub error response.

    Only GitHub's documented ``message`` field is surfaced (never raw bodies or headers).
    """
    message = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            message = str(body.get("message", ""))[:200]
    except ValueError:
        pass
    return f"HTTP {response.status_code}" + (f": {message}" if message else "")


def is_git_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(GIT_SHA_RE.match(value))


def _segment(value: str) -> str:
    """URL-encode a single path segment (owner or repository name)."""
    return quote(str(value), safe="")


class GitHubService:
    def __init__(
        self,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ):
        # ``transport``/``sleep``/``clock`` are injectable for tests.
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        # Cache for installation tokens: {installation_id: (token_str, expiry_epoch)}
        self._installation_token_cache: Dict[int, Tuple[str, float]] = {}

    # ------------------------------------------------------------------ transport

    def _client(self, timeout: Optional[float] = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout or settings.GITHUB_TIMEOUT_SECONDS,
            transport=self._transport,
            follow_redirects=True,
        )

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with jitter: ~1s, 2s, 4s, 8s ... capped at 30s."""
        return min(30.0, 2.0 ** attempt) * (0.5 + random.random() / 2)

    def _rate_limit_wait(self, response: httpx.Response) -> Optional[float]:
        """Seconds to wait if ``response`` is a rate-limit response, else None."""
        if response.status_code not in (403, 429):
            return None
        headers = response.headers
        retry_after = headers.get("retry-after")
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                return 60.0
        if headers.get("x-ratelimit-remaining") == "0":
            try:
                return max(0.0, float(headers.get("x-ratelimit-reset", "0")) - self._clock()) + 1.0
            except ValueError:
                return 60.0
        if response.status_code == 429:
            return 60.0
        # Secondary rate limits are 403s whose message mentions the limit.
        if "rate limit" in _error_detail(response).lower():
            return 60.0
        return None

    async def _send(
        self,
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        description: str = "GitHub request",
    ) -> httpx.Response:
        """Send a request with retry, backoff and rate-limit handling.

        Returns the final response (which may be a non-2xx status for the caller to
        interpret). Raises GitHubServiceError on persistent network failures and
        GitHubRateLimitError when the rate limit cannot be waited out.
        """
        max_retries = max(0, settings.GITHUB_MAX_RETRIES)
        async with self._client(timeout) as client:
            for attempt in range(max_retries + 1):
                try:
                    response = await client.request(method, url, headers=headers, params=params)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt >= max_retries:
                        raise GitHubServiceError(
                            f"{description} failed after {attempt + 1} attempts ({type(exc).__name__})"
                        ) from None
                    delay = self._backoff(attempt)
                    logger.warning(
                        "%s: %s, retrying in %.1fs (attempt %d/%d)",
                        description, type(exc).__name__, delay, attempt + 1, max_retries,
                    )
                    await self._sleep(delay)
                    continue

                wait = self._rate_limit_wait(response)
                if wait is not None:
                    if attempt >= max_retries or wait > settings.GITHUB_MAX_RETRY_WAIT_SECONDS:
                        raise GitHubRateLimitError(
                            f"{description}: GitHub rate limit exceeded "
                            f"(retry after ~{int(wait)}s)",
                            retry_after_seconds=wait,
                        )
                    logger.warning(
                        "%s: rate limited, waiting %.0fs (attempt %d/%d)",
                        description, wait, attempt + 1, max_retries,
                    )
                    await self._sleep(wait)
                    continue

                if response.status_code in RETRYABLE_STATUS and attempt < max_retries:
                    delay = self._backoff(attempt)
                    logger.warning(
                        "%s: HTTP %d, retrying in %.1fs (attempt %d/%d)",
                        description, response.status_code, delay, attempt + 1, max_retries,
                    )
                    await self._sleep(delay)
                    continue

                return response
        raise GitHubServiceError(f"{description} failed")  # pragma: no cover

    # ------------------------------------------------------------- authentication

    def _get_private_key(self) -> Optional[str]:
        """Resolves the RSA private key from either direct string or file path."""
        if settings.GITHUB_PRIVATE_KEY:
            return settings.GITHUB_PRIVATE_KEY

        # isfile, not exists: a Docker bind mount of a missing host file appears as a directory.
        if settings.GITHUB_PRIVATE_KEY_PATH and os.path.isfile(settings.GITHUB_PRIVATE_KEY_PATH):
            with open(settings.GITHUB_PRIVATE_KEY_PATH, "r", encoding="utf-8") as f:
                return f.read()

        return None

    def create_app_jwt(self) -> str:
        """
        Creates an RS256-signed JWT representing the GitHub App itself.
        Valid for 10 minutes maximum per GitHub requirements.
        """
        app_id = settings.GITHUB_APP_ID
        private_key = self._get_private_key()

        if not app_id or not private_key:
            raise GitHubServiceError(
                "GitHub App credentials missing: GITHUB_APP_ID and GITHUB_PRIVATE_KEY (or GITHUB_PRIVATE_KEY_PATH) must be set."
            )

        now = int(self._clock())
        payload = {
            "iat": now - 60,  # 60s in the past to compensate for clock drift
            "exp": now + (10 * 60),  # 10 minutes
            "iss": str(app_id),
        }

        try:
            return jwt.encode(payload, private_key, algorithm="RS256")
        except Exception as e:
            # Do not include the exception text: it may echo key material.
            raise GitHubServiceError(f"Failed to generate GitHub App JWT ({type(e).__name__})")

    async def get_installation_access_token(self, installation_id: int) -> str:
        """
        Mints or returns a cached short-lived access token for a specific GitHub App installation.
        """
        now = self._clock()
        cached = self._installation_token_cache.get(installation_id)
        if cached:
            token, expiry = cached
            # If token still valid for more than 2 minutes, return it
            if expiry - now > 120:
                return token

        app_jwt = self.create_app_jwt()
        response = await self._send(
            "POST",
            f"{settings.GITHUB_API_URL}/app/installations/{int(installation_id)}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
            timeout=15.0,
            description=f"Installation token for {installation_id}",
        )
        if response.status_code != 201:
            raise GitHubServiceError(
                f"Failed to obtain installation token for installation {installation_id}: "
                f"{_error_detail(response)}",
                response.status_code,
            )

        data = response.json()
        token = data["token"]
        try:
            expiry_epoch = datetime.fromisoformat(
                str(data["expires_at"]).replace("Z", "+00:00")
            ).timestamp()
        except (KeyError, ValueError):
            expiry_epoch = now + 3500  # Default to ~58 minutes

        self._installation_token_cache[installation_id] = (token, expiry_epoch)
        return token

    async def _make_installation_request(
        self,
        installation_id: int,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        description: str = "GitHub request",
    ) -> httpx.Response:
        """Makes an authenticated request using the installation's token.

        A 401 (token revoked or expired early) clears the cached token and retries once.
        """
        for token_attempt in range(2):
            token = await self.get_installation_access_token(installation_id)
            req_headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            }
            if headers:
                req_headers.update(headers)
            response = await self._send(
                method,
                f"{settings.GITHUB_API_URL}{path}",
                headers=req_headers,
                params=params,
                description=description,
            )
            if response.status_code == 401 and token_attempt == 0:
                self._installation_token_cache.pop(installation_id, None)
                continue
            return response
        return response  # pragma: no cover

    async def _paginate(
        self,
        installation_id: int,
        path: str,
        item_key: Optional[str],
        description: str,
    ) -> List[Dict[str, Any]]:
        """Collect every page of a list endpoint (``per_page=100``)."""
        items: List[Dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            response = await self._make_installation_request(
                installation_id, "GET", path,
                params={"per_page": 100, "page": page},
                description=description,
            )
            if response.status_code != 200:
                raise GitHubServiceError(
                    f"{description} failed: {_error_detail(response)}", response.status_code
                )
            body = response.json()
            batch = body.get(item_key, []) if item_key else body
            items.extend(batch)
            if len(batch) < 100 or 'rel="next"' not in response.headers.get("link", ""):
                break
        return items

    # ------------------------------------------------------------ repository APIs

    async def list_installation_repositories(self, installation_id: int) -> List[Dict[str, Any]]:
        """Lists all repositories accessible to a specific installation (all pages)."""
        return await self._paginate(
            installation_id, "/installation/repositories", "repositories",
            "List installation repositories",
        )

    async def get_repository_by_id(self, installation_id: int, github_repo_id: int) -> Optional[Dict[str, Any]]:
        """Fetches a repository by numeric ID using the installation's token.

        Installation tokens can only see repositories granted to that installation, so a
        successful response also proves the repository belongs to the installation.
        Returns None when the repository is not accessible.
        """
        response = await self._make_installation_request(
            installation_id, "GET", f"/repositories/{int(github_repo_id)}",
            description=f"Get repository {github_repo_id}",
        )
        if response.status_code in (403, 404):
            return None
        if response.status_code != 200:
            raise GitHubServiceError(
                f"Failed to fetch repository {github_repo_id}: {_error_detail(response)}",
                response.status_code,
            )
        return response.json()

    async def list_user_installations(self, user_access_token: str) -> List[Dict[str, Any]]:
        """Lists this GitHub App's installations that the signed-in user can access.

        Requires a user access token issued by the GitHub App's own OAuth client
        (GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET must belong to the GitHub App).
        """
        installations: List[Dict[str, Any]] = []
        headers = {
            "Authorization": f"Bearer {user_access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        for page in range(1, MAX_PAGES + 1):
            response = await self._send(
                "GET",
                f"{settings.GITHUB_API_URL}/user/installations",
                headers=headers,
                params={"per_page": 100, "page": page},
                timeout=15.0,
                description="List user installations",
            )
            if response.status_code != 200:
                raise GitHubServiceError(
                    f"Failed to list user installations: {_error_detail(response)}",
                    response.status_code,
                )
            batch = response.json().get("installations", [])
            installations.extend(batch)
            if len(batch) < 100 or 'rel="next"' not in response.headers.get("link", ""):
                break
        return installations

    async def get_repository_metadata(self, installation_id: int, owner: str, repo: str) -> Dict[str, Any]:
        """Fetches repository metadata from GitHub."""
        response = await self._make_installation_request(
            installation_id, "GET", f"/repos/{_segment(owner)}/{_segment(repo)}",
            description="Get repository metadata",
        )
        if response.status_code != 200:
            raise GitHubServiceError(
                f"Failed to fetch repository metadata: {_error_detail(response)}", response.status_code
            )
        return response.json()

    async def get_branches(self, installation_id: int, owner: str, repo: str) -> List[Dict[str, Any]]:
        """Lists all branches for a given repository (all pages)."""
        return await self._paginate(
            installation_id, f"/repos/{_segment(owner)}/{_segment(repo)}/branches", None,
            "List repository branches",
        )

    async def resolve_commit_sha(self, installation_id: int, owner: str, repo: str, ref: str) -> str:
        """Resolve a branch, tag or SHA to the full commit SHA it points to *now*.

        Indexing is pinned to the returned SHA so a run sees one consistent snapshot even
        if the branch moves while it is running.
        """
        if not ref or len(ref) > 255 or ".." in ref or ref.startswith("/"):
            raise GitHubServiceError("Invalid git ref")
        response = await self._make_installation_request(
            installation_id, "GET",
            f"/repos/{_segment(owner)}/{_segment(repo)}/commits/{quote(ref, safe='/')}",
            headers={"Accept": "application/vnd.github.sha"},
            description="Resolve commit",
        )
        if response.status_code in (404, 422):
            raise GitHubNotFoundError(f"Git ref not found: {_error_detail(response)}")
        if response.status_code != 200:
            raise GitHubServiceError(
                f"Failed to resolve ref: {_error_detail(response)}", response.status_code
            )
        sha = response.text.strip()
        if not is_git_sha(sha):
            raise GitHubServiceError("GitHub returned an invalid commit SHA")
        return sha

    async def get_tree(
        self, installation_id: int, owner: str, repo: str, tree_sha: str, recursive: bool
    ) -> Dict[str, Any]:
        """Fetch a git tree (``tree_sha`` may be a commit SHA for the root tree).

        Returns GitHub's payload: ``{"sha", "tree": [...], "truncated": bool}``. Recursive
        listings are truncated by GitHub above ~100,000 entries / 7 MB.
        """
        if not is_git_sha(tree_sha):
            raise GitHubServiceError("Invalid tree SHA")
        response = await self._make_installation_request(
            installation_id, "GET",
            f"/repos/{_segment(owner)}/{_segment(repo)}/git/trees/{tree_sha}",
            params={"recursive": "1"} if recursive else None,
            description="Get git tree",
        )
        if response.status_code in (404, 409):
            raise GitHubNotFoundError(f"Git tree not found: {_error_detail(response)}")
        if response.status_code != 200:
            raise GitHubServiceError(
                f"Failed to fetch git tree: {_error_detail(response)}", response.status_code
            )
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("tree"), list):
            raise GitHubServiceError("GitHub returned a malformed git tree")
        return data

    async def get_blob(self, installation_id: int, owner: str, repo: str, blob_sha: str) -> bytes:
        """Fetch raw blob bytes by SHA (content-addressed, so cacheable and dedupable)."""
        if not is_git_sha(blob_sha):
            raise GitHubServiceError("Invalid blob SHA")
        response = await self._make_installation_request(
            installation_id, "GET",
            f"/repos/{_segment(owner)}/{_segment(repo)}/git/blobs/{blob_sha}",
            headers={"Accept": "application/vnd.github.raw+json"},
            description="Get git blob",
        )
        if response.status_code == 404:
            raise GitHubNotFoundError("Git blob not found")
        if response.status_code != 200:
            raise GitHubServiceError(
                f"Failed to fetch git blob: {_error_detail(response)}", response.status_code
            )
        return response.content

    async def get_pull_request(self, installation_id: int, owner: str, repo: str, pull_number: int) -> Dict[str, Any]:
        """Fetches Pull Request metadata."""
        response = await self._make_installation_request(
            installation_id, "GET",
            f"/repos/{_segment(owner)}/{_segment(repo)}/pulls/{int(pull_number)}",
            description="Get pull request",
        )
        if response.status_code != 200:
            raise GitHubServiceError(
                f"Failed to fetch PR #{pull_number}: {_error_detail(response)}", response.status_code
            )
        return response.json()

    async def get_pull_request_diff(self, installation_id: int, owner: str, repo: str, pull_number: int) -> str:
        """Retrieves raw Git unified diff for a Pull Request."""
        response = await self._make_installation_request(
            installation_id, "GET",
            f"/repos/{_segment(owner)}/{_segment(repo)}/pulls/{int(pull_number)}",
            headers={"Accept": "application/vnd.github.v3.diff"},
            description="Get pull request diff",
        )
        if response.status_code != 200:
            raise GitHubServiceError(
                f"Failed to fetch diff for PR #{pull_number}: {_error_detail(response)}",
                response.status_code,
            )
        return response.text

    async def get_file_content(
        self, installation_id: int, owner: str, repo: str, file_path: str, ref: str
    ) -> Optional[str]:
        """Retrieves raw content of a specific file at a given commit/branch ref."""
        response = await self._make_installation_request(
            installation_id, "GET",
            f"/repos/{_segment(owner)}/{_segment(repo)}/contents/{quote(file_path, safe='/')}",
            params={"ref": ref},
            headers={"Accept": "application/vnd.github.v3.raw"},
            description="Get file content",
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GitHubServiceError(
                f"Failed to fetch file {file_path}: {_error_detail(response)}", response.status_code
            )
        return response.text


github_service = GitHubService()
