import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
import httpx
from jose import jwt

from app.core.config import settings


GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
# Hard stop for paginated listings so a misbehaving API cannot loop forever.
MAX_PAGES = 50


class GitHubServiceError(Exception):
    """Custom exception raised for GitHub App API communication errors."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


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


class GitHubService:
    def __init__(self):
        # Cache for installation tokens: {installation_id: (token_str, expiry_epoch)}
        self._installation_token_cache: Dict[int, Tuple[str, float]] = {}

    def _get_private_key(self) -> Optional[str]:
        """Resolves the RSA private key from either direct string or file path."""
        if settings.GITHUB_PRIVATE_KEY:
            return settings.GITHUB_PRIVATE_KEY

        if settings.GITHUB_PRIVATE_KEY_PATH and os.path.exists(settings.GITHUB_PRIVATE_KEY_PATH):
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

        now = int(time.time())
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
        now = time.time()
        cached = self._installation_token_cache.get(installation_id)
        if cached:
            token, expiry = cached
            # If token still valid for more than 2 minutes, return it
            if expiry - now > 120:
                return token

        app_jwt = self.create_app_jwt()
        url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=headers)
            if response.status_code != 201:
                raise GitHubServiceError(
                    f"Failed to obtain installation token for installation {installation_id}: "
                    f"{_error_detail(response)}",
                    response.status_code,
                )

            data = response.json()
            token = data["token"]
            expires_at_str = data["expires_at"]  # e.g. "2026-11-13T10:00:00Z"
            
            # Parse ISO timestamp to epoch
            try:
                expiry_dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                expiry_epoch = expiry_dt.timestamp()
            except Exception:
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
    ) -> httpx.Response:
        """Makes an authenticated request using the installation's token."""
        token = await self.get_installation_access_token(installation_id)
        req_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if headers:
            req_headers.update(headers)

        url = f"https://api.github.com{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.request(method, url, headers=req_headers, params=params)

    async def list_installation_repositories(self, installation_id: int) -> List[Dict[str, Any]]:
        """Lists all repositories accessible to a specific installation (all pages)."""
        repositories: List[Dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            response = await self._make_installation_request(
                installation_id,
                "GET",
                "/installation/repositories",
                params={"per_page": 100, "page": page},
            )
            if response.status_code != 200:
                raise GitHubServiceError(
                    f"Failed to fetch installation repositories: {_error_detail(response)}",
                    response.status_code,
                )
            batch = response.json().get("repositories", [])
            repositories.extend(batch)
            if len(batch) < 100 or 'rel="next"' not in response.headers.get("link", ""):
                break
        return repositories

    async def get_repository_by_id(self, installation_id: int, github_repo_id: int) -> Optional[Dict[str, Any]]:
        """Fetches a repository by numeric ID using the installation's token.

        Installation tokens can only see repositories granted to that installation, so a
        successful response also proves the repository belongs to the installation.
        Returns None when the repository is not accessible.
        """
        response = await self._make_installation_request(
            installation_id, "GET", f"/repositories/{int(github_repo_id)}"
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
        async with httpx.AsyncClient(timeout=15.0) as client:
            for page in range(1, MAX_PAGES + 1):
                response = await client.get(
                    f"{GITHUB_API_URL}/user/installations",
                    headers=headers,
                    params={"per_page": 100, "page": page},
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
            installation_id, "GET", f"/repos/{owner}/{repo}"
        )
        if response.status_code != 200:
            raise GitHubServiceError(f"Failed to fetch repository metadata: {_error_detail(response)}", response.status_code)
        return response.json()

    async def get_branches(self, installation_id: int, owner: str, repo: str) -> List[Dict[str, Any]]:
        """Lists branches for a given repository."""
        response = await self._make_installation_request(
            installation_id, "GET", f"/repos/{owner}/{repo}/branches"
        )
        if response.status_code != 200:
            raise GitHubServiceError(f"Failed to fetch repository branches: {_error_detail(response)}", response.status_code)
        return response.json()

    async def get_pull_request(self, installation_id: int, owner: str, repo: str, pull_number: int) -> Dict[str, Any]:
        """Fetches Pull Request metadata."""
        response = await self._make_installation_request(
            installation_id, "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}"
        )
        if response.status_code != 200:
            raise GitHubServiceError(f"Failed to fetch PR #{pull_number}: {_error_detail(response)}", response.status_code)
        return response.json()

    async def get_pull_request_diff(self, installation_id: int, owner: str, repo: str, pull_number: int) -> str:
        """Retrieves raw Git unified diff for a Pull Request."""
        headers = {"Accept": "application/vnd.github.v3.diff"}
        response = await self._make_installation_request(
            installation_id, "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}", headers=headers
        )
        if response.status_code != 200:
            raise GitHubServiceError(f"Failed to fetch diff for PR #{pull_number}: {_error_detail(response)}", response.status_code)
        return response.text

    async def get_file_content(
        self, installation_id: int, owner: str, repo: str, file_path: str, ref: str
    ) -> Optional[str]:
        """Retrieves raw content of a specific file at a given commit/branch ref."""
        headers = {"Accept": "application/vnd.github.v3.raw"}
        response = await self._make_installation_request(
            installation_id, "GET", f"/repos/{owner}/{repo}/contents/{quote(file_path, safe='/')}",
            params={"ref": ref},
            headers=headers
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GitHubServiceError(f"Failed to fetch file {file_path}: {_error_detail(response)}", response.status_code)
        return response.text


github_service = GitHubService()
