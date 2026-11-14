import uuid
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import GUID, TimestampMixin


class Repository(Base, TimestampMixin):
    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("github_installations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    github_repo_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), unique=True, index=True, nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), default="main", nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    html_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Indexing State
    is_indexed: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    indexing_status: Mapped[str] = mapped_column(
        String(50), default="unindexed", index=True, nullable=False
    )  # unindexed, pending, indexing, indexed, failed
    last_indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_indexed_commit: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    total_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    installation: Mapped["GitHubInstallation"] = relationship("GitHubInstallation", back_populates="repositories")
    branches: Mapped[List["RepositoryBranch"]] = relationship("RepositoryBranch", back_populates="repository", cascade="all, delete-orphan")
    commits: Mapped[List["Commit"]] = relationship("Commit", back_populates="repository", cascade="all, delete-orphan")
    indexed_files: Mapped[List["IndexedFile"]] = relationship("IndexedFile", back_populates="repository", cascade="all, delete-orphan")
    code_chunks: Mapped[List["CodeChunk"]] = relationship("CodeChunk", back_populates="repository", cascade="all, delete-orphan")
    pull_requests: Mapped[List["PullRequest"]] = relationship("PullRequest", back_populates="repository", cascade="all, delete-orphan")
    dependencies: Mapped[List["Dependency"]] = relationship("Dependency", back_populates="repository", cascade="all, delete-orphan")
    conversations: Mapped[List["Conversation"]] = relationship("Conversation", back_populates="repository", cascade="all, delete-orphan")
    chunk_embeddings: Mapped[List["ChunkEmbedding"]] = relationship("ChunkEmbedding", back_populates="repository", cascade="all, delete-orphan")
    indexing_jobs: Mapped[List["IndexingJob"]] = relationship("IndexingJob", back_populates="repository", cascade="all, delete-orphan", order_by="IndexingJob.created_at")


class RepositoryBranch(Base, TimestampMixin):
    __tablename__ = "repository_branches"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    latest_commit_sha: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    repository: Mapped["Repository"] = relationship("Repository", back_populates="branches")


class Commit(Base, TimestampMixin):
    __tablename__ = "commits"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sha: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    author_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    committed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    repository: Mapped["Repository"] = relationship("Repository", back_populates="commits")

    __table_args__ = (
        Index("ix_commits_repo_sha", "repository_id", "sha"),
    )
