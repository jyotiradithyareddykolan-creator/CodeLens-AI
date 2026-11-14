import logging
from typing import Any, Dict, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    GitHubInstallation,
    PullRequest,
    PRAnalysis,
    Repository,
    WebhookEvent,
)
from app.services.installation_service import upsert_installation_from_webhook
from app.workers.celery_app import (
    ANALYZE_PULL_REQUEST_TASK_NAME,
    INCREMENTAL_REINDEX_TASK_NAME,
    dispatch_registered_task,
)

logger = logging.getLogger(__name__)


class WebhookService:
    async def process_webhook(
        self,
        db: AsyncSession,
        delivery_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Processes an incoming GitHub webhook event with strict delivery idempotency.
        """
        # 1. Idempotency Check: check if delivery_id was already received
        existing = await db.execute(
            select(WebhookEvent).where(WebhookEvent.delivery_id == delivery_id)
        )
        if existing.scalar_one_or_none():
            logger.info(f"Duplicate webhook delivery ignored: {delivery_id}")
            return {
                "status": "ignored",
                "reason": "duplicate",
                "delivery_id": delivery_id,
            }

        # 2. Record WebhookEvent in database
        webhook_record = WebhookEvent(
            delivery_id=delivery_id,
            event_type=event_type,
            payload=payload,
            status="received",
        )
        db.add(webhook_record)
        await db.flush()

        action = payload.get("action", "")
        result_status = "processed"
        error_msg = None

        try:
            # 3. Route event to specific handler
            if event_type == "installation":
                await self._handle_installation_event(db, action, payload)
            elif event_type == "installation_repositories":
                await self._handle_installation_repositories_event(db, action, payload)
            elif event_type == "pull_request":
                await self._handle_pull_request_event(db, action, payload)
            elif event_type == "push":
                await self._handle_push_event(db, payload)
            else:
                logger.info(f"Unhandled webhook event type: {event_type}.{action}")
                result_status = "ignored"

        except Exception as e:
            logger.error(f"Error handling webhook {event_type}.{action} ({delivery_id}): {str(e)}", exc_info=True)
            result_status = "failed"
            error_msg = str(e)

        webhook_record.status = result_status
        webhook_record.error_message = error_msg
        await db.commit()

        return {
            "status": result_status,
            "event_type": event_type,
            "action": action,
            "delivery_id": delivery_id,
            "error": error_msg if settings.DEBUG else None,
        }

    async def _handle_installation_event(
        self, db: AsyncSession, action: str, payload: Dict[str, Any]
    ) -> None:
        """Handles installation.created / new_permissions_accepted / deleted."""
        installation_data = payload.get("installation", {})
        inst_id = installation_data.get("id")
        if not inst_id:
            return

        if action in ("created", "new_permissions_accepted", "unsuspend"):
            await upsert_installation_from_webhook(db, payload)
        elif action == "deleted":
            stmt = select(GitHubInstallation).where(GitHubInstallation.installation_id == inst_id)
            result = await db.execute(stmt)
            inst = result.scalar_one_or_none()
            if inst:
                await db.delete(inst)
                logger.info(f"Removed GitHub Installation {inst_id}")

    async def _handle_installation_repositories_event(
        self, db: AsyncSession, action: str, payload: Dict[str, Any]
    ) -> None:
        """Handles installation_repositories.added, installation_repositories.removed."""
        inst_id = payload.get("installation", {}).get("id")
        if not inst_id:
            return

        if action == "removed":
            removed_repos = payload.get("repositories_removed", [])
            for r in removed_repos:
                repo_gh_id = r.get("id")
                if repo_gh_id:
                    stmt = select(Repository).where(Repository.github_repo_id == repo_gh_id)
                    res = await db.execute(stmt)
                    repo = res.scalar_one_or_none()
                    if repo:
                        await db.delete(repo)
                        logger.info(f"Disconnected removed repository {r.get('full_name')}")

    async def _handle_pull_request_event(
        self, db: AsyncSession, action: str, payload: Dict[str, Any]
    ) -> None:
        """
        Handles pull_request.opened, pull_request.synchronize, pull_request.reopened, pull_request.closed.
        """
        pr_data = payload.get("pull_request", {})
        repo_data = payload.get("repository", {})
        repo_gh_id = repo_data.get("id")

        if not repo_gh_id or not pr_data:
            return

        # Find repository in database
        stmt = select(Repository).where(Repository.github_repo_id == repo_gh_id)
        result = await db.execute(stmt)
        repo = result.scalar_one_or_none()
        if not repo:
            logger.info(f"Received PR event for unconnected repository {repo_data.get('full_name')}")
            return

        pr_number = pr_data.get("number")
        base_sha = pr_data.get("base", {}).get("sha", "")
        head_sha = pr_data.get("head", {}).get("sha", "")
        base_branch = pr_data.get("base", {}).get("ref", "")
        head_branch = pr_data.get("head", {}).get("ref", "")
        title = pr_data.get("title", f"PR #{pr_number}")
        body = pr_data.get("body", "")
        author = pr_data.get("user", {}).get("login", "unknown")
        avatar_url = pr_data.get("user", {}).get("avatar_url")
        html_url = pr_data.get("html_url")

        # Find or create PullRequest
        pr_stmt = select(PullRequest).where(
            PullRequest.repository_id == repo.id,
            PullRequest.pr_number == pr_number,
        )
        pr_res = await db.execute(pr_stmt)
        pr = pr_res.scalar_one_or_none()

        if not pr:
            pr = PullRequest(
                repository_id=repo.id,
                pr_number=pr_number,
                title=title,
                description=body,
                author_username=author,
                author_avatar_url=avatar_url,
                state="open",
                base_branch=base_branch,
                head_branch=head_branch,
                base_sha=base_sha,
                head_sha=head_sha,
                github_pr_url=html_url,
            )
            db.add(pr)
            await db.flush()
        else:
            # Update existing PR state
            pr.title = title
            pr.description = body
            pr.base_sha = base_sha
            pr.head_sha = head_sha
            pr.base_branch = base_branch
            pr.head_branch = head_branch
            if action == "closed":
                pr.state = "merged" if pr_data.get("merged") else "closed"
            else:
                pr.state = "open"

        # If opened, synchronized (new commit), or reopened: enqueue PR Analysis
        if action in ["opened", "synchronize", "reopened"]:
            analysis = PRAnalysis(
                pull_request_id=pr.id,
                head_sha=head_sha,
                base_sha=base_sha,
                status="pending",
            )
            db.add(analysis)
            await db.flush()

            # Enqueue Celery PR analysis task
            if settings.ENVIRONMENT != "testing":
                try:
                    dispatched = dispatch_registered_task(
                        ANALYZE_PULL_REQUEST_TASK_NAME, str(analysis.id)
                    )
                    if dispatched:
                        logger.info(f"Enqueued PR analysis for PR #{pr_number} (Analysis {analysis.id})")
                    else:
                        logger.error(
                            "PR analysis task is not registered; analysis remains pending: %s",
                            ANALYZE_PULL_REQUEST_TASK_NAME,
                        )
                except Exception as e:
                    logger.warning(f"Failed to enqueue Celery task (Redis may be offline): {e}")

    async def _handle_push_event(self, db: AsyncSession, payload: Dict[str, Any]) -> None:
        """Handles push webhook event to trigger incremental indexing."""
        repo_data = payload.get("repository", {})
        repo_gh_id = repo_data.get("id")
        ref = payload.get("ref", "")  # e.g. "refs/heads/main"

        if not repo_gh_id:
            return

        stmt = select(Repository).where(Repository.github_repo_id == repo_gh_id)
        result = await db.execute(stmt)
        repo = result.scalar_one_or_none()
        if not repo:
            return

        branch_name = ref.replace("refs/heads/", "")
        # Only index pushes to the default branch
        if branch_name == repo.default_branch:
            head_commit = payload.get("after")
            if settings.ENVIRONMENT != "testing":
                try:
                    dispatched = dispatch_registered_task(
                        INCREMENTAL_REINDEX_TASK_NAME, str(repo.id), head_commit
                    )
                    if dispatched:
                        logger.info(f"Enqueued incremental reindex for {repo.full_name} at {head_commit}")
                    else:
                        logger.error(
                            "Incremental indexing task is not registered; push was recorded without dispatch: %s",
                            INCREMENTAL_REINDEX_TASK_NAME,
                        )
                except Exception as e:
                    logger.warning(f"Failed to enqueue Celery push task: {e}")


webhook_service = WebhookService()
