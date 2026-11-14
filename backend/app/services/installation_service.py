"""Persistence of GitHub App installations and their link to CodeLens users.

An installation is linked to a CodeLens user in two ways:

1. During sign-in: the user's GitHub App user access token is used to list the
   installations that user can access (``GET /user/installations``). GitHub
   performs the authorization check, so a user can never claim an installation
   they cannot see.
2. From ``installation`` webhooks: when the GitHub account that installed the App
   (``sender``) is already a CodeLens user, the installation is linked to them.

Current limitation: an installation has a single owning user. Additional members
of an organization that already linked the installation will not see it until
multi-user membership is modelled.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GitHubInstallation, User

logger = logging.getLogger(__name__)


def _parse_installation(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate and normalise the fields we persist from a GitHub installation object."""
    inst_id = data.get("id")
    account = data.get("account") or {}
    login = account.get("login")
    if not isinstance(inst_id, int) or isinstance(inst_id, bool) or inst_id <= 0:
        return None
    if not isinstance(login, str) or not login:
        return None
    account_type = account.get("type")
    permissions = data.get("permissions")
    return {
        "installation_id": inst_id,
        "account_login": login[:255],
        "account_type": account_type if account_type in ("User", "Organization") else "User",
        "permissions": permissions if isinstance(permissions, dict) else None,
    }


async def upsert_installation_for_user(
    db: AsyncSession, user: User, data: Dict[str, Any]
) -> Optional[GitHubInstallation]:
    """Create or refresh an installation record owned by ``user``.

    An installation already owned by a different user is left untouched.
    """
    fields = _parse_installation(data)
    if fields is None:
        return None

    result = await db.execute(
        select(GitHubInstallation).where(
            GitHubInstallation.installation_id == fields["installation_id"]
        )
    )
    installation = result.scalar_one_or_none()

    if installation is None:
        installation = GitHubInstallation(user_id=user.id, **fields)
        db.add(installation)
        await db.flush()
        logger.info("Linked GitHub installation %s to user %s", fields["installation_id"], user.id)
        return installation

    if installation.user_id != user.id:
        logger.info(
            "Installation %s is already linked to another user; not re-assigning",
            fields["installation_id"],
        )
        return None

    installation.account_login = fields["account_login"]
    installation.account_type = fields["account_type"]
    installation.permissions = fields["permissions"]
    return installation


async def link_user_installations(
    db: AsyncSession, user: User, installations: Iterable[Dict[str, Any]]
) -> List[GitHubInstallation]:
    """Link every installation GitHub reports as accessible to ``user``."""
    linked: List[GitHubInstallation] = []
    for data in installations:
        installation = await upsert_installation_for_user(db, user, data)
        if installation is not None:
            linked.append(installation)
    return linked


async def upsert_installation_from_webhook(
    db: AsyncSession, payload: Dict[str, Any]
) -> Optional[GitHubInstallation]:
    """Handle installation.created / new_permissions_accepted webhooks.

    The installation is linked to the ``sender`` if that GitHub user has signed in to
    CodeLens before; otherwise it will be linked on their next sign-in.
    """
    installation_data = payload.get("installation") or {}
    sender_id = (payload.get("sender") or {}).get("id")
    if not isinstance(sender_id, int):
        return None

    result = await db.execute(select(User).where(User.github_id == sender_id))
    user = result.scalar_one_or_none()
    if user is None:
        logger.info(
            "Installation %s created by a GitHub user who has not signed in yet; deferring link",
            installation_data.get("id"),
        )
        return None
    return await upsert_installation_for_user(db, user, installation_data)
