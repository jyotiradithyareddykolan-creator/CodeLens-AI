"""Shared test helpers.

Keep this module free of side effects: conftest.py owns the test database and the
dependency overrides, and must only be imported by pytest itself (importing it as
``tests.conftest`` would create a second, empty in-memory database).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models import GitHubInstallation, User


async def make_user_with_installation(
    session: AsyncSession,
    github_id: int,
    username: str,
    installation_id: int,
    account_login: str = "codelens-org",
):
    """Create a user plus a linked GitHub App installation; return (user, installation, headers)."""
    user = User(github_id=github_id, username=username)
    session.add(user)
    await session.flush()
    installation = GitHubInstallation(
        user_id=user.id,
        installation_id=installation_id,
        account_login=account_login,
        account_type="Organization",
        permissions={"contents": "read", "pull_requests": "read"},
    )
    session.add(installation)
    await session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(subject=str(user.id))}"}
    return user, installation, headers
