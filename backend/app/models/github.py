import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy import BigInteger, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import GUID, TimestampMixin


class GitHubInstallation(Base, TimestampMixin):
    __tablename__ = "github_installations"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    installation_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(50), default="User", nullable=False)  # User or Organization
    permissions: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="installations")
    repositories: Mapped[List["Repository"]] = relationship(
        "Repository", back_populates="installation", cascade="all, delete-orphan"
    )


class WebhookEvent(Base, TimestampMixin):
    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    delivery_id: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="received", index=True, nullable=False
    )  # received, processed, failed, ignored
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
