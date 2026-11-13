import uuid
from typing import Any, List, Optional
from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import GUID, AdaptiveVector, TimestampMixin


class IndexedFile(Base, TimestampMixin):
    __tablename__ = "indexed_files"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(1024), index=True, nullable=False)
    language: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    repository: Mapped["Repository"] = relationship("Repository", back_populates="indexed_files")
    chunks: Mapped[List["CodeChunk"]] = relationship("CodeChunk", back_populates="file", cascade="all, delete-orphan")
    symbols: Mapped[List["CodeSymbol"]] = relationship("CodeSymbol", back_populates="file", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_indexed_files_repo_path", "repository_id", "file_path", unique=True),
    )


class CodeChunk(Base, TimestampMixin):
    __tablename__ = "code_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("indexed_files.id", ondelete="CASCADE"), index=True, nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol_name: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    symbol_type: Mapped[str] = mapped_column(String(50), default="chunk", index=True, nullable=False)  # function, class, method, import, block
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    
    # 768-dimensional Gemini vector embedding
    embedding: Mapped[Optional[Any]] = mapped_column(AdaptiveVector(768), nullable=True)

    # Relationships
    file: Mapped["IndexedFile"] = relationship("IndexedFile", back_populates="chunks")
    repository: Mapped["Repository"] = relationship("Repository", back_populates="code_chunks")

    __table_args__ = (
        Index("ix_code_chunks_repo_symbol", "repository_id", "symbol_name"),
    )


class CodeSymbol(Base, TimestampMixin):
    __tablename__ = "code_symbols"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("indexed_files.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    symbol_type: Mapped[str] = mapped_column(String(50), nullable=False)  # function, class, method, interface
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    signature: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    docstring: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    file: Mapped["IndexedFile"] = relationship("IndexedFile", back_populates="symbols")


class Dependency(Base, TimestampMixin):
    __tablename__ = "dependencies"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_symbol_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("code_symbols.id", ondelete="SET NULL"), nullable=True
    )
    target_symbol_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("code_symbols.id", ondelete="SET NULL"), nullable=True
    )
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("indexed_files.id", ondelete="CASCADE"), index=True, nullable=False
    )
    target_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("indexed_files.id", ondelete="SET NULL"), nullable=True
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    relationship_type: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False
    )  # IMPORTS, CALLS, DEFINES, INHERITS, IMPLEMENTS, USES

    # Relationships
    repository: Mapped["Repository"] = relationship("Repository", back_populates="dependencies")
