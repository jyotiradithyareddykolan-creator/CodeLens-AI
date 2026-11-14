import hmac
import logging
import secrets
import uuid
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token
from app.database.session import get_async_db
from app.models import User
from app.services.github_service import GitHubServiceError, github_service
from app.services.installation_service import link_user_installations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

OAUTH_STATE_COOKIE = "codelens_oauth_state"


async def get_current_user(
    db: AsyncSession = Depends(get_async_db),
    authorization: Optional[str] = Header(None),
) -> Optional[User]:
    """Retrieves current user from Bearer JWT session token (None when absent/invalid)."""
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization[len("Bearer "):].strip()
    payload = decode_access_token(token)
    if not payload:
        return None

    user_id_str = payload.get("sub")
    if not user_id_str:
        return None

    try:
        user_uuid = uuid.UUID(user_id_str)
    except (ValueError, TypeError):
        return None
    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


async def require_current_user(
    current_user: Optional[User] = Depends(get_current_user),
) -> User:
    """Dependency for endpoints that require an authenticated, active user."""
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


def _callback_url() -> str:
    return f"{settings.BACKEND_URL}{settings.API_V1_STR}/auth/callback"


def _frontend_redirect(fragment: dict) -> RedirectResponse:
    """Redirect to the SPA. Values travel in the URL fragment, which browsers never
    send to servers, so session tokens do not end up in access logs or Referer headers."""
    response = RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/callback#{urlencode(fragment)}",
        status_code=status.HTTP_302_FOUND,
    )
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/")
    return response


def _require_oauth_configured(need_secret: bool = False) -> None:
    if not settings.GITHUB_CLIENT_ID or (need_secret and not settings.GITHUB_CLIENT_SECRET):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub sign-in is not configured.",
        )


@router.get("/github", summary="Get GitHub Sign-in URL")
async def github_oauth_login_url():
    """Returns the backend URL that starts GitHub sign-in.

    The browser must navigate to it (not fetch it) so the anti-CSRF state cookie is
    set on the backend origin that later receives the OAuth callback.
    """
    _require_oauth_configured()
    return {"url": f"{settings.BACKEND_URL}{settings.API_V1_STR}/auth/github/login"}


@router.get("/github/login", summary="Start GitHub Sign-in")
async def github_oauth_login():
    """Sets a one-time state cookie and redirects to GitHub's authorization page.

    Also used as the GitHub App "Setup URL", so users who just installed the App are
    signed in and their new installation is linked automatically.
    """
    _require_oauth_configured()

    state = secrets.token_urlsafe(32)
    query = urlencode(
        {
            "client_id": settings.GITHUB_CLIENT_ID,
            "redirect_uri": _callback_url(),
            "state": state,
        }
    )
    response = RedirectResponse(
        url=f"https://github.com/login/oauth/authorize?{query}",
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=settings.OAUTH_STATE_EXPIRE_SECONDS,
        httponly=True,
        secure=settings.BACKEND_URL.startswith("https://"),
        samesite="lax",
        path="/",
    )
    return response


@router.get("/callback", summary="GitHub OAuth Callback Handler")
async def github_oauth_callback(
    code: Optional[str] = Query(None, max_length=512),
    state: Optional[str] = Query(None, max_length=512),
    error: Optional[str] = Query(None, max_length=128),
    codelens_oauth_state: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Verifies the state parameter, exchanges the authorization code for a GitHub user
    token, registers or updates the user, links their accessible GitHub App
    installations and redirects to the frontend with a CodeLens session token.

    The GitHub user token is used only within this request and is never stored.
    """
    _require_oauth_configured(need_secret=True)

    if error:
        return _frontend_redirect({"error": "access_denied"})

    if (
        not state
        or not codelens_oauth_state
        or not hmac.compare_digest(state, codelens_oauth_state)
    ):
        return _frontend_redirect({"error": "invalid_state"})

    if not code:
        return _frontend_redirect({"error": "missing_code"})

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                "https://github.com/login/oauth/access_token",
                json={
                    "client_id": settings.GITHUB_CLIENT_ID,
                    "client_secret": settings.GITHUB_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": _callback_url(),
                },
                headers={"Accept": "application/json"},
            )
            access_token = (
                token_resp.json().get("access_token") if token_resp.status_code == 200 else None
            )
            if not access_token:
                logger.warning("GitHub OAuth code exchange failed (HTTP %s)", token_resp.status_code)
                return _frontend_redirect({"error": "token_exchange_failed"})

            user_resp = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if user_resp.status_code != 200:
                logger.warning("GitHub profile request failed (HTTP %s)", user_resp.status_code)
                return _frontend_redirect({"error": "profile_failed"})
            profile = user_resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("GitHub OAuth request error: %s", type(exc).__name__)
        return _frontend_redirect({"error": "github_unavailable"})

    github_id = profile.get("id")
    username = profile.get("login")
    if not isinstance(github_id, int) or not isinstance(username, str) or not username:
        return _frontend_redirect({"error": "profile_failed"})

    result = await db.execute(select(User).where(User.github_id == github_id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            github_id=github_id,
            username=username[:255],
            email=profile.get("email"),
            avatar_url=profile.get("avatar_url"),
        )
        db.add(user)
        await db.flush()
    else:
        if not user.is_active:
            return _frontend_redirect({"error": "account_disabled"})
        user.username = username[:255]
        user.email = profile.get("email")
        user.avatar_url = profile.get("avatar_url")

    # Link the GitHub App installations this user can access. A failure here must not
    # block sign-in; installations are also linked by webhooks and on the next sign-in.
    try:
        installations = await github_service.list_user_installations(access_token)
        await link_user_installations(db, user, installations)
    except (GitHubServiceError, httpx.HTTPError) as exc:
        logger.warning(
            "Could not list GitHub App installations for user %s: %s",
            user.id,
            exc if isinstance(exc, GitHubServiceError) else type(exc).__name__,
        )

    await db.commit()

    session_token = create_access_token(subject=str(user.id))
    return _frontend_redirect({"token": session_token})


@router.get("/me", summary="Get Authenticated User Profile")
async def get_me(current_user: Optional[User] = Depends(get_current_user)):
    """Returns the profile of the authenticated user, or an unauthenticated guest marker."""
    if not current_user:
        return {
            "authenticated": False,
            "user": {
                "username": "guest",
                "avatar_url": None,
                "is_active": True,
            },
        }

    return {
        "authenticated": True,
        "user": {
            "id": str(current_user.id),
            "username": current_user.username,
            "email": current_user.email,
            "avatar_url": current_user.avatar_url,
            "is_active": current_user.is_active,
        },
    }


@router.post("/logout", summary="Log Out")
async def logout():
    """Session tokens are stateless JWTs; the client discards its token on logout."""
    return {"status": "logged_out", "message": "Successfully logged out"}
