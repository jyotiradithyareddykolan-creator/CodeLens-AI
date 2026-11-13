import uuid
from typing import Any, Optional
from sqlalchemy import Float, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base
from app.models.base import GUID, TimestampMixin


class BackgroundTask(Base, TimestampMixin):
    __tablename__ = "background_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    task_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="PENDING", index=True, nullable=False
    )  # PENDING, STARTED, SUCCESS, FAILURE, RETRY
    progress_percentage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
