import uuid
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import GUID, TimestampMixin


class PullRequest(Base, TimestampMixin):
    __tablename__ = "pull_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    pr_number: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author_username: Mapped[str] = mapped_column(String(255), nullable=False)
    author_avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(50), default="open", index=True, nullable=False)  # open, closed, merged
    base_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    head_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    base_sha: Mapped[str] = mapped_column(String(100), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(100), nullable=False)
    github_pr_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    diff_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    repository: Mapped["Repository"] = relationship("Repository", back_populates="pull_requests")
    analyses: Mapped[List["PRAnalysis"]] = relationship("PRAnalysis", back_populates="pull_request", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_pull_requests_repo_number", "repository_id", "pr_number", unique=True),
    )


class PRAnalysis(Base, TimestampMixin):
    __tablename__ = "pr_analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("pull_requests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    head_sha: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    base_sha: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="pending", index=True, nullable=False
    )  # pending, running, completed, failed
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_findings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    pull_request: Mapped["PullRequest"] = relationship("PullRequest", back_populates="analyses")
    findings: Mapped[List["Finding"]] = relationship("Finding", back_populates="analysis", cascade="all, delete-orphan")
    impact_relationships: Mapped[List["ImpactRelationship"]] = relationship("ImpactRelationship", back_populates="analysis", cascade="all, delete-orphan")


class Finding(Base, TimestampMixin):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    pr_analysis_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("pr_analyses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    category: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False
    )  # bug, security, performance, breaking_change, regression, missing_test
    severity: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False
    )  # critical, high, medium, low, info
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_path: Mapped[str] = mapped_column(String(1024), index=True, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    suggestion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Relationships
    analysis: Mapped["PRAnalysis"] = relationship("PRAnalysis", back_populates="findings")


class ImpactRelationship(Base, TimestampMixin):
    __tablename__ = "impact_relationships"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    pr_analysis_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("pr_analyses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_symbol: Mapped[str] = mapped_column(String(255), nullable=False)
    source_file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    affected_symbol: Mapped[str] = mapped_column(String(255), nullable=False)
    affected_file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    impact_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # direct, indirect, breaking, potential
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    analysis: Mapped["PRAnalysis"] = relationship("PRAnalysis", back_populates="impact_relationships")
