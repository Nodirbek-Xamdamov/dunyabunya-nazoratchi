# app/store.py
from datetime import datetime, timezone
from app.models import ChatMessage

class ChatStore:
    def __init__(self):
        # Conversation xabarlarini vaqtincha xotirada saqlash uchun dictionary
        self._storage: dict[str, list[ChatMessage]] = {}

    async def add_message(self, conversation_id: str, role: str, content: str) -> ChatMessage:
        if conversation_id not in self._storage:
            self._storage[conversation_id] = []
        msg = ChatMessage(role=role, content=content, created_at=datetime.now(timezone.utc))
        self._storage[conversation_id].append(msg)
        return msg

    async def get_messages(self, conversation_id: str) -> list[ChatMessage]:
        return self._storage.get(conversation_id, [])

chat_store = ChatStore()