# app/catalog.py
#
# Mahsulot katalogi. 29-sentabrdan sayt bazasi Supabase'dan o'z serverimizga
# ko'chirildi, Supabase'dagi "Products" jadvali endi yangilanmaydi — yagona
# manba sayt API'si (settings.products_api_url, kalitsiz). Unda faqat saytda
# ko'rinib turgan mahsulotlar bor, shuning uchun bot saytda yo'q mahsulotni
# tavsiya qila olmaydi.
#
# Har bir savolda API'ga borilmaydi: ro'yxat refresh_loop() orqali har
# settings.products_refresh_seconds'da to'liq yuklanib, xotirada saqlanadi.
# Yuklash xato bersa eski ro'yxat bilan ishlashda davom etiladi.
import asyncio
import logging

import httpx2

from app.aliases import expand_word
from app.config import settings

logger = logging.getLogger(__name__)

# API bitta sahifada bundan ko'p qaytarmaydi.
_PAGE_LIMIT = 200
# Ro'yxat hali bo'sh bo'lsa (masalan ishga tushishda API javob bermagan),
# to'liq intervalni kutmasdan tezroq qayta urinamiz.
_EMPTY_RETRY_SECONDS = 30
_ROOT_CATEGORY_PREFIX = "Hammasi / "

_products: list[dict] = []


def _normalize(item: dict) -> dict:
    category = item.get("category") or ""
    if category.startswith(_ROOT_CATEGORY_PREFIX):
        category = category[len(_ROOT_CATEGORY_PREFIX):]
    return {
        "id": item["id"],
        # Odoo'dan kelgan nomlarda ba'zan qo'sh probel bor ("Drel mikser  PW110").
        "name": " ".join((item.get("name") or "").split()),
        "name_uz": " ".join((item.get("nameUz") or "").split()),
        "price": item.get("price"),
        "in_stock": bool(item.get("inStock")),
        "category": category,
        "brand": item.get("brand"),
    }


def _set_products(items: list[dict]) -> None:
    global _products
    by_id = {item["id"]: item for item in items}  # sahifalar orasida takror kelsa
    _products = [_normalize(item) for item in by_id.values()]


async def fetch_all_products() -> list[dict]:
    items: list[dict] = []
    page = 1
    async with httpx2.AsyncClient(timeout=15) as client:
        while True:
            response = await client.get(
                settings.products_api_url,
                params={"page": page, "limit": _PAGE_LIMIT},
            )
            response.raise_for_status()
            data = response.json()
            items += data["items"]
            if len(items) >= data["total"] or not data["items"]:
                return items
            page += 1


async def refresh_products() -> None:
    _set_products(await fetch_all_products())
    logger.info("Katalog yangilandi: %d ta mahsulot.", len(_products))


async def refresh_loop() -> None:
    while True:
        await asyncio.sleep(settings.products_refresh_seconds if _products else _EMPTY_RETRY_SECONDS)
        try:
            await refresh_products()
        except Exception:
            logger.exception("Katalogni yuklashda xato, eski ro'yxat bilan davom etamiz (%d ta).", len(_products))


def _search_text(product: dict) -> str:
    return f"{product['name']} {product['name_uz']} {product['brand'] or ''}".casefold()


def _to_result(product: dict, lang: str) -> dict:
    # id ichki kod — natijaga alohida qo'shilmaydi ("ichki kodlarni oshkor
    # qilma" qoidasi), faqat saytdagi ochiq havola ichida ketadi.
    name = product["name"] if lang == "ru" else (product["name_uz"] or product["name"])
    price = product["price"]
    return {
        "name": name,
        # Saytda narxi 0 bo'lgan mahsulotlar bor — "0 so'm" deb aytilmasligi
        # uchun narx noma'lum (null) sifatida beriladi.
        "price": price if price and price > 0 else None,
        "in_stock": product["in_stock"],
        "category": product["category"],
        "brand": product["brand"],
        "link": f"{settings.site_base_url.rstrip('/')}/{lang}/catalog/{product['id']}",
    }


def _in_stock_first(products: list[dict]) -> list[dict]:
    return sorted(products, key=lambda p: not p["in_stock"])


def search_products(product_name: str, lang: str = "uz") -> list[dict]:
    # Butun iborani BITTA substring sifatida qidirish juda mo'rt: LLM ko'pincha
    # so'zlarni qayta tartiblab beradi (masalan mahsulot "AVR CHINT (...)"
    # bo'lsa, LLM "CHINT AVR" deb so'rashi mumkin). Shuning uchun so'rov
    # so'zlarga ajratiladi va har bir so'z (tartibidan qat'i nazar) nom yoki
    # brendda uchrashi kerak.
    #
    # Har bir so'z app/aliases.py orqali kengaytiriladi (masalan "siment" ->
    # ["siment", "цемент"]), chunki mijoz ishlatadigan so'z (imlo xato, boshqa
    # til/alifbo) ko'pincha Odoo'dan kelgan haqiqiy nom bilan mos kelmaydi —
    # bitta so'z ichidagi variantlar OR, so'zlar orasida esa AND.
    lang = lang if lang in ("uz", "ru") else "uz"
    words = product_name.split()
    if not words:
        return []

    word_variants = [[v.casefold() for v in expand_word(w)] for w in words]

    direct = [
        p for p in _products
        if all(any(v in _search_text(p) for v in variants) for variants in word_variants)
    ][:5]

    if direct:
        # Mijoz aniq mahsulot topgan bo'lsa ham, xuddi shu kategoriyadagi
        # boshqa (brend/o'lcham/zavod) variantlarni ham qo'shamiz — bot
        # ularni alternativa sifatida taklif qila oladi (system_prompt.txt
        # 6-qoida). Omborda borlari birinchi.
        matched_ids = {p["id"] for p in direct}
        categories = {p["category"] for p in direct if p["category"]}
        alternatives = _in_stock_first(
            [p for p in _products if p["category"] in categories and p["id"] not in matched_ids]
        )[:3]
        return [_to_result(p, lang) for p in direct + alternatives]

    # To'g'ridan-to'g'ri hech narsa topilmadi: ehtimol mijoz umumiy/janr
    # so'z ishlatgan ("sement bormi?") va bunday so'z aynan bironta mahsulot
    # nomida emas, balki kategoriya nomida uchraydi (masalan "Цемент и
    # смеси"). Shu holda kategoriyadagi variantlarni qaytaramiz — LLM ularni
    # sanab beradi (system_prompt.txt 4-qoida).
    all_variants = [v for variants in word_variants for v in variants]
    category_matches = _in_stock_first(
        [p for p in _products if any(v in p["category"].casefold() for v in all_variants)]
    )[:8]
    return [_to_result(p, lang) for p in category_matches]
