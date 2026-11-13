import uuid
from typing import Optional
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token
from app.database.session import get_async_db
from app.models import User

router = APIRouter(prefix="/auth", tags=["Authentication"])


async def get_current_user(
    db: AsyncSession = Depends(get_async_db),
    authorization: Optional[str] = Header(None),
) -> Optional[User]:
    """Retrieves current user from Bearer JWT session token."""
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload:
        return None

    user_id_str = payload.get("sub")
    if not user_id_str:
        return None

    try:
        user_uuid = uuid.UUID(user_id_str)
        stmt = select(User).where(User.id == user_uuid)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    except Exception:
        return None


@router.get("/github", summary="Get GitHub OAuth Authorization URL")
async def github_oauth_login():
    """Generates GitHub OAuth login redirect URL."""
    if not settings.GITHUB_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GITHUB_CLIENT_ID is not configured in environment.",
        )

    redirect_uri = f"{settings.BACKEND_URL}{settings.API_V1_STR}/auth/callback"
    oauth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={settings.GITHUB_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=read:user,user:email"
    )
    return {"url": oauth_url}


@router.get("/callback", summary="GitHub OAuth Callback Handler")
async def github_oauth_callback(
    code: str = Query(..., description="Authorization code from GitHub"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Exchanges GitHub OAuth authorization code for user access token,
    retrieves user profile, and registers or updates the user in CodeLens AI.
    """
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GitHub OAuth credentials not configured.",
        )

    # 1. Exchange code for access token
    token_url = "https://github.com/login/oauth/access_token"
    token_payload = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "client_secret": settings.GITHUB_CLIENT_SECRET,
        "code": code,
    }
    headers = {"Accept": "application/json"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(token_url, json=token_payload, headers=headers)
        token_data = token_resp.json()
        access_token = token_data.get("access_token")

        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to obtain access token from GitHub: {token_data.get('error_description', 'Unknown error')}",
            )

        # 2. Fetch User Profile
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        if user_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to retrieve GitHub user profile",
            )
        profile = user_resp.json()

    github_id = profile["id"]
    username = profile["login"]
    email = profile.get("email")
    avatar_url = profile.get("avatar_url")

    # 3. Find or create user
    stmt = select(User).where(User.github_id == github_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            github_id=github_id,
            username=username,
            email=email,
            avatar_url=avatar_url,
        )
        db.add(user)
    else:
        user.username = username
        user.email = email
        user.avatar_url = avatar_url

    await db.commit()
    await db.refresh(user)

    # 4. Generate CodeLens Session JWT
    session_token = create_access_token(subject=str(user.id))

    # Redirect user back to frontend dashboard with token in URL param (or JSON response if API client)
    frontend_redirect = f"{settings.FRONTEND_URL}/auth/callback?token={session_token}"
    return RedirectResponse(url=frontend_redirect)


@router.get("/me", summary="Get Authenticated User Profile")
async def get_me(current_user: Optional[User] = Depends(get_current_user)):
    """Returns profile of currently authenticated user or demo user."""
    if not current_user:
        # Graceful guest/demo profile
        return {
            "authenticated": False,
            "user": {
                "username": "guest",
                "avatar_url": None,
                "is_active": True,
            }
        }

    return {
        "authenticated": True,
        "user": {
            "id": str(current_user.id),
            "username": current_user.username,
            "email": current_user.email,
            "avatar_url": current_user.avatar_url,
            "is_active": current_user.is_active,
        }
    }


@router.post("/logout", summary="Log Out")
async def logout():
    """Logs out current session."""
    return {"status": "logged_out", "message": "Successfully logged out"}
