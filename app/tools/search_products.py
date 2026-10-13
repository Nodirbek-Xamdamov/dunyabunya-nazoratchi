# app/tools/search_products.py
from app.catalog import search_products
from app.tools.base import Tool


async def _handler(conversation_id: str, product_name: str = "", lang: str = "uz") -> list[dict]:
    product_name = (product_name or "").strip()
    if not product_name:
        return []
    return search_products(product_name, lang)


search_products_tool = Tool(
    name="search_products_tool",
    description=(
        "Saytdagi mahsulotlar katalogidan nomi bo'yicha qidiruv. name, price, "
        "in_stock, category, brand va link (saytdagi mahsulot sahifasi) qaytaradi."
    ),
    parameters={
        "type": "object",
        "properties": {
            "product_name": {
                "type": "string",
                "description": "Qidirilayotgan mahsulot nomi (foydalanuvchi so'zlagan holida).",
            },
            "lang": {
                "type": "string",
                "enum": ["uz", "ru"],
                "description": "Mijoz yozayotgan til: o'zbekcha (lotin yoki kirill) bo'lsa uz, ruscha bo'lsa ru.",
            },
        },
        "required": ["product_name"],
    },
    handler=_handler,
)
