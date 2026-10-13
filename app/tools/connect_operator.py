# app/tools/connect_operator.py
from app.telegram_operator import notify_operator
from app.tools.base import Tool


async def _handler(conversation_id: str, customer_message: str = "") -> list[dict]:
    sent = await notify_operator(conversation_id, (customer_message or "").strip())
    return [{"sent": sent}]


connect_operator_tool = Tool(
    name="connect_operator_tool",
    description=(
        "Mijoz operatorga/odamga ulanishni ANIQ xohlaganda chaqiriladi: yo mijoz "
        "to'g'ridan-to'g'ri so'rasa (\"operatorga ulang\", \"odam bilan "
        "gaplashsam bo'ladimi\" — darhol, tasdiqlashsiz), yo bot taklif qilgach "
        "mijoz tasdiqlasa (\"ha\", \"ulang\"). O'zingdan taklif qilma. "
        "Mijozning asosiy savolini operatorga Telegram orqali yuboradi."
    ),
    parameters={
        "type": "object",
        "properties": {
            "customer_message": {
                "type": "string",
                "description": "Mijozning operatorga yetkazilishi kerak bo'lgan asosiy savoli/so'rovi.",
            },
        },
        "required": ["customer_message"],
    },
    handler=_handler,
)
