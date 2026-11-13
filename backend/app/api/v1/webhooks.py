from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import verify_github_signature
from app.database.session import get_async_db
from app.services.webhook_service import webhook_service

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/github", status_code=status.HTTP_200_OK, summary="GitHub App Webhook Endpoint")
async def github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
    x_github_delivery: str = Header(None, alias="X-GitHub-Delivery"),
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
):
    """
    Receives and processes GitHub App webhook events:
    1. Validates X-Hub-Signature-256 HMAC SHA-256 signature.
    2. Enforces delivery idempotency via X-GitHub-Delivery ID.
    3. Asynchronously handles installation, PR, and push events.
    """
    # 1. Read raw body for HMAC verification
    raw_body = await request.body()

    # Production must fail closed when webhook verification is not configured.
    if not settings.GITHUB_WEBHOOK_SECRET:
        if settings.ENVIRONMENT == "production":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="GitHub webhook verification is not configured.",
            )
    else:
        if not x_hub_signature_256:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing X-Hub-Signature-256 header",
            )
        is_valid = verify_github_signature(
            raw_body, x_hub_signature_256, settings.GITHUB_WEBHOOK_SECRET
        )
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid X-Hub-Signature-256 signature",
            )

    if not x_github_delivery:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Delivery header",
        )

    if not x_github_event:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Event header",
        )

    # Parse JSON payload
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload in webhook body",
        )

    # Process webhook event
    result = await webhook_service.process_webhook(
        db=db,
        delivery_id=x_github_delivery,
        event_type=x_github_event,
        payload=payload,
    )

    return result
