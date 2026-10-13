# app/tools/base.py
#
# MCP tool'iga o'xshash shakl: nom/tavsif/JSON-schema/handler. Hozircha OpenAI'ning
# native function-calling'i orqali in-process chaqiriladi (yangi bog'liqlik yo'q),
# lekin bu shakl haqiqiy MCP serveriga almashtirishga tayyor kengaytirish nuqtasi —
# handler'ni MCP client chaqiruviga almashtirish yetarli bo'ladi.
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[list[dict]]]

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
