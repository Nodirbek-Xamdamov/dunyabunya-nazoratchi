# app/models.py
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field

class ChatMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str  # 'user' yoki 'bot'
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SaveMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(alias="conversationId")
    role: str = Field(alias="sender")  # 'user' yoki 'bot'
    content: str = Field(alias="text")

class ChatHistoryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(alias="conversationId")
    messages: list[ChatMessage]

class AskRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(alias="conversationId")
    user_message: str = Field(alias="message")

class AskResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(alias="conversationId")
    answer: str