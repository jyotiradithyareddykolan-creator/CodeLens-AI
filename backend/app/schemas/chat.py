import uuid
from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class CitationSchema(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    snippet: str
    symbol_name: Optional[str] = None


class ChatMessageRequest(BaseModel):
    repository_id: uuid.UUID
    conversation_id: Optional[uuid.UUID] = None
    message: str = Field(..., min_length=1)


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    citations: Optional[List[CitationSchema]] = None
    created_at: datetime


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    title: str
    created_at: datetime
    messages: List[MessageResponse] = Field(default_factory=list)
