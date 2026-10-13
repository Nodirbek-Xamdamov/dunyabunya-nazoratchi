"""
Guruh xabarlarini tasniflash (2026-10-13) -- xabar yig'ishdan ALOHIDA ishlaydigan fon vazifa.

Yig'uvchi (`app/bot/handlers/group_monitor.py`) xabarni bazaga "pending" qilib yozadi va shu
bilan ishi tugaydi. Bu yerdagi `classifier_loop` kutayotgan mijoz xabarlarini bazadan olib,
guruh bo'yicha paketlab modelga beradi:

  0. Ovozli xabar, audio, video (`GROUP_STT_ENABLED`): avval nutqi OpenAI transkripsiya API'si
     orqali matnga aylantiriladi (`app/stt.py`, `OPENAI_STT_MODEL`) va `messages.transcript`ga
     yoziladi; keyin u oddiy matnli xabar kabi tasniflanadi.
  1. Arzon qoida: matni yo'q yoki faqat emoji/belgidan iborat xabar (stiker, "👍", rasm izohsiz)
     modelga yuborilmaydi -- darhol "kompaniyaga aloqasiz" deb yoziladi (model = "rule").
  2. Qolganlari suhbat konteksti bilan (oldingi xabarlar, xodimlar javobi) bitta so'rovda ketadi.
     Model har bir xabar uchun JSON qaytaradi: kompaniyaga oidmi, toifa (8 tadan biri),
     sentiment (Shikoyat / Ijobiy / Neytral), bir jumlalik mazmun, menejer javobi kerakmi.
  3. Javob tekshiriladi (har xabarga aynan bitta natija, toifa va sentiment ro'yxatdan). Xato
     chiqqanlari shu zahoti bir marta qayta so'raladi; yana bo'lmasa keyingi aylanishda qayta
     uriniladi, `CLASSIFY_MAX_ATTEMPTS` martadan keyin "failed" bo'ladi.
  4. Har xabar FAQAT BIR MARTA tasniflanadi. Menejer javobi kerak bo'lganlari uchun "5 daqiqa
     qoidasi" tekshiruvi ochiladi (`app/monitor_sla.py`).

Kutayotgan xabarlar bazada turadi -- bot yoki model o'chib qolsa hech narsa yo'qolmaydi, ishga
tushgach davom etadi. Model va provayder: `app/llm_client.py` (`GROUP_AI_PROVIDER`/`GROUP_AI_MODEL`).
"""

import asyncio
import logging
import os
import tempfile
from datetime import date
from itertools import groupby

from aiogram import Bot

from app import llm_client, monitor_db, monitor_sla, stt
from app.config import settings
from app.llm_client import LLMUnavailable
from app.monitor_db import CATEGORIES, COMPLAINT, NEUTRAL, POSITIVE, POSITIVE_CATEGORY, SENTIMENTS, ClassificationResult, Message

logger = logging.getLogger(__name__)

_TARGET_TEXT_LIMIT = 600
_CONTEXT_TEXT_LIMIT = 300
_SUMMARY_LIMIT = 300
_MEDIA_LABELS = {
    "photo": "rasm", "video": "video", "voice": "ovozli xabar", "audio": "audio", "video_note": "video xabar",
    "document": "fayl", "sticker": "stiker", "animation": "gif",
}

SYSTEM_PROMPT = """\
Sen "dunyabunya" kompaniyasining mijozlar bilan ishlaydigan Telegram guruhlaridagi xabarlarni \
tasniflaysan. Kompaniya qurilish mahsulotlari (sement, g'isht, armatura, gipsokarton, kafel, \
bo'yoq va hokazo) sotadi va mijozlarga yetkazib beradi. Xabarlar o'zbekcha (lotin yoki kirill), \
ruscha yoki aralash, og'zaki uslubda va imlo xatolari bilan yoziladi. Natijangni dastur o'qiydi: \
undan kunlik hisobot tuziladi va "menejer mijozga 5 daqiqada javob berdimi" tekshiriladi.

Senga bitta guruhdagi suhbat beriladi. "#raqam" bilan boshlangan qatorlar -- tasniflanadigan \
MIJOZ xabarlari. Qolgan qatorlar (oldingi xabarlar, xodimlarning javobi) faqat kontekst: gap \
kimga va nima haqida ketayotganini tushunish uchun, ularni tasniflama. Har bir "#raqam" uchun \
aynan bitta natija qaytar; id -- o'sha raqam.

Har bir xabar uchun:

summary -- xabar mazmuni bitta qisqa jumlada, o'zbek tilida (lotin harflarida). Masalan: \
"Mijoz sement narxini so'radi", "Mijoz g'isht singan kelganidan shikoyat qildi".

is_company_related -- xabar dunyabunyaga (tovari, narxi, buyurtma, to'lov, yetkazib berish, \
xizmati, xodimlari) oid bo'lsa true. Guruh kompaniya va uning mijozlari uchun ochilgan, shuning \
uchun hech kimga qaratilmagan tovar, narx, buyurtma yoki yetkazib berish haqidagi savol \
kompaniyaga berilgan hisoblanadi. false bo'ladi: salom-alik ("assalomu alaykum", "добрый день"), \
"ok", "xo'p", "ha", "tushunarli", "понятно", reklama va spam, hazil, ob-havo, boshqa firma yoki \
bozor haqidagi gap, mavzudan tashqari suhbat. Diqqat: "rahmat, yetib keldi", "спасибо, всё \
привезли" kabi xabar aloqasiz EMAS -- bu ijobiy fikr (true). Suhbat oxiridagi quruq "rahmat", \
"ok rahmat", "спасибо" esa odob so'zi xolos -- false.

category -- quyidagi 8 tadan biri (is_company_related false bo'lsa "Boshqa" deb yoz):
1. "Dastavka va Logistika" -- yetkazib berish: qachon keladi, kechikdi, kelmadi, mashina, \
haydovchi yo'lda, manzil, yukni tushirish, yetkazish narxi.
   Misollar: "Buyurtmam qachon yetib keladi?"; "Uch kundan beri kutyapman, mashina kelmadi"; \
"Ertaga Chilonzorga yetkazib bera olasizmi?"; "Когда будет доставка?"; "Машина опоздала на три \
часа"; "Сколько стоит доставка до Сергели?"
2. "Mahsulot Sifati va Nuqsonlar" -- tovarning sifati: brak, singan, yorilgan, qotib qolgan, \
namlangan, sifati past, muddati o'tgan.
   Misollar: "Sementingiz qotib qolgan ekan"; "G'ishtning yarmi singan keldi"; "Kafel yorilgan \
chiqdi"; "Плитка пришла битая"; "Краска просроченная"; "Гипсокартон намок, качество плохое"
3. "Komplektatsiya va Hajm" -- miqdor va tarkib: kam yoki ortiqcha keldi, yetishmayapti, \
buyurtma to'liq emas, boshqa tovar, o'lcham yoki rang keldi.
   Misollar: "10 qop buyurtma qilgandim, 8 ta keldi"; "Armatura 12 lik so'ragandim, 10 lik olib \
kelishibdi"; "Ikki quti kafel yetishmayapti"; "Привезли не тот цвет"; "Не хватает двух мешков"; \
"Заказывал 50 листов, привезли 45"
4. "Xodimlar va Xizmat Ko'rsatish" -- xodimlarning muomalasi va xizmat darajasi: qo'pollik, \
telefonni ko'tarmaslik, javob bermaslik, va'dani bajarmaslik; menejer, operator, haydovchi, \
sotuvchi haqidagi gap.
   Misollar: "Menejer telefonni ko'tarmayapti"; "Haydovchingiz qo'pol gapirdi"; "Ikki kundan \
beri hech kim javob bermayapti"; "Менеджер не отвечает второй день"; "Оператор нагрубил"; \
"Обещали перезвонить и пропали"
5. "Narx va To'lovlar" -- narx, chegirma, qimmatlik, hisob-faktura, to'lov usuli, to'lov \
o'tmagani, qarz, akt sverka, ortiqcha pul olingani.
   Misollar: "Sement qanchadan?"; "Chegirma bo'ladimi?"; "To'lov qildim, tekshirib ko'ring"; \
"Schet-faktura yuboring"; "Сколько стоит арматура 12?"; "Оплатил, но оплата не видна"; "Почему \
в счёте цена выше?"
6. "Qaytarish va Kafolat" -- tovarni qaytarish, almashtirish, pulni qaytarish, kafolat.
   Misollar: "Ortib qolgan sementni qaytarib olasizmi?"; "Brak chiqsa almashtirasizmi?"; \
"Pulimni qaytaring"; "Можно вернуть лишний товар?"; "Замените бракованную плитку"; "Есть ли \
гарантия?"
7. "Ijobiy Fikrlar" -- mijoz mamnun: maqtov, chin dildan minnatdorlik, tovar yoki xizmatdan \
xursandlik.
   Misollar: "Rahmat, yetib keldi, hammasi joyida"; "Xizmatingizdan juda mamnunman"; "Tez \
yetkazdingiz, barakalla"; "Спасибо, всё привезли вовремя"; "Очень довольны качеством"; "Ребята, \
вы лучшие"
8. "Boshqa" -- kompaniyaga oid, lekin yuqoridagi yettitasiga to'g'ri kelmaydi: tovar \
bor-yo'qligi, buyurtma berish, ish vaqti, do'kon yoki ombor manzili, umumiy savol.
   Misollar: "Armatura 12 lik bormi?"; "50 qop sement buyurtma bermoqchiman"; "Shanba kuni \
ishlaysizlarmi?"; "Есть ли в наличии утеплитель?"; "Хочу заказать 2 тонны арматуры"; "Где \
находится ваш склад?"
"Boshqa"ni faqat qolgan yettitasidan hech biri to'g'ri kelmaganda tanla -- avval ularni birma-bir \
tekshir. Xabar bir nechta toifaga to'g'ri kelsa, mijozni ranjitgan yoki qiziqtirgan ASOSIY \
sababni tanla: "g'isht singan keldi, pulimni qaytaring" -- "Mahsulot Sifati va Nuqsonlar"; \
"kechikib keldi, haydovchi ham qo'pol" -- "Dastavka va Logistika".

sentiment:
- "Shikoyat" -- mijoz norozi: shikoyat qilyapti, e'tiroz bildiryapti, ranjiyapti ("uch kundan \
beri kutyapman", "bu qanaqa ish?", "сколько можно?").
- "Ijobiy" -- mijoz mamnun, maqtayapti, minnatdor. Bunda category doim "Ijobiy Fikrlar".
- "Neytral" -- oddiy savol, so'rov, buyurtma yoki ma'lumot ("sement qanchadan?", "ertaga olib \
ketaman").

needs_manager -- mijoz kompaniyadan javob kutyaptimi. true: tovar bor-yo'qligini yoki narxini \
so'rayapti, buyurtma beryapti, biror narsani iltimos qilyapti, javob talab qiladigan shikoyat \
yozyapti. false: suhbatni yakunlovchi xabar ("ok", "rahmat", "tushunarli", "ertaga olaman"), \
maqtov, xodimning savoliga berilgan oddiy javob ("Sergeli 5, 12-uy", "ha, to'ladim") va \
is_company_related false bo'lgan har qanday xabar."""

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                # Model maydonlarni shu tartibda yozadi: avval mazmunni aytadi, keyin tasniflaydi
                "properties": {
                    "id": {"type": "integer"},
                    "summary": {"type": "string"},
                    "is_company_related": {"type": "boolean"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "sentiment": {"type": "string", "enum": list(SENTIMENTS)},
                    "needs_manager": {"type": "boolean"},
                },
                "required": ["id", "summary", "is_company_related", "category", "sentiment", "needs_manager"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def is_junk(message: Message) -> bool:
    """Modelga yuborishga arzimaydigan xabar: matni yo'q (stiker, izohsiz rasm, nutqsiz ovoz) yoki
    unda birorta harf/raqam yo'q (faqat emoji, "+", "👍")."""
    return not any(ch.isalnum() for ch in message.full_text)


# ---------------------------------------------------------------------------
# Ovozli xabar -> matn (OpenAI)
# ---------------------------------------------------------------------------

_SPEECH_SUFFIXES = {"voice": ".ogg", "audio": ".mp3", "video": ".mp4", "video_note": ".mp4"}
_TRANSCRIBE_ATTEMPTS = 3
# Faylni yuklab olish uchun bot (`classifier_loop` o'rnatadi). Botsiz (sinov skripti, testlar)
# ovozli xabarlar matnga aylantirilmaydi.
_bot: Bot | None = None


def set_bot(bot: Bot | None) -> None:
    global _bot
    _bot = bot


def needs_transcript(message: Message) -> bool:
    return (
        settings.GROUP_STT_ENABLED and _bot is not None and bool(settings.OPENAI_API_KEY)
        and message.media_type in _SPEECH_SUFFIXES and bool(message.file_id) and message.transcript is None
    )


async def transcribe(message: Message) -> bool:
    """Ovozli xabar nutqini OpenAI orqali matnga aylantirib bazaga yozadi. True -- xabarni
    tasniflash mumkin (matn yozildi yoki nutq yo'q); False -- hozircha bo'lmadi, keyingi
    aylanishda qayta uriniladi (`_TRANSCRIBE_ATTEMPTS` martadan keyin matnsiz davom etadi)."""
    text: str | None = ""
    if (message.media_duration or 0) <= settings.GROUP_STT_MAX_DURATION_SECONDS:
        fd, path = tempfile.mkstemp(prefix="tg_voice_", suffix=_SPEECH_SUFFIXES[message.media_type])
        os.close(fd)
        try:
            await _bot.download(message.file_id, destination=path)
            text = await stt.transcribe_openai(path)
        except Exception as exc:
            # Fayl 20 MB dan katta, o'chirilgan va h.k. -- qayta urinishdan foyda yo'q
            logger.warning("Ovozli xabarni yuklab bo'lmadi (xabar=%s): %s", message.id, exc)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    if text is None:
        if message.attempts + 1 < _TRANSCRIBE_ATTEMPTS:
            await monitor_db.record_failed_attempt([message.id], settings.CLASSIFY_MAX_ATTEMPTS + _TRANSCRIBE_ATTEMPTS)
            return False
        text = ""
    await monitor_db.set_transcript(message.id, text)
    message.transcript = text
    logger.info("Ovozli xabar matnga aylantirildi: xabar=%s, %s belgi", message.id, len(text))
    return True


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _line_text(message: Message, limit: int) -> str:
    text = _cut(message.full_text, limit)
    label = _MEDIA_LABELS.get(message.media_type)
    if label:
        return f"[{label}] {text}".strip()
    return text or "(matnsiz)"


def build_transcript(group_title: str | None, conversation: list[Message], targets: dict[int, int]) -> str:
    """Modelga beriladigan suhbat matni. `targets`: xabarning bazadagi ID'si -> so'rovdagi raqami."""
    by_message_id = {message.message_id: message for message in conversation}
    lines = [f"Guruh: {group_title or 'nomsiz'}", "", "Suhbat (eskisidan yangisiga):"]
    for message in conversation:
        role = "xodim" if message.is_staff else "mijoz"
        who = f"[{role}] {message.sender_name or message.sender_username or 'nomsiz'}"
        replied = by_message_id.get(message.reply_to_message_id)
        if replied is not None and replied is not message:
            who += f" ({replied.sender_name or 'xabar'}ga javoban)"
        number = targets.get(message.id)
        if number is not None:
            lines.append(f"#{number} {who}: {_line_text(message, _TARGET_TEXT_LIMIT)}")
        else:
            lines.append(f"   {who}: {_line_text(message, _CONTEXT_TEXT_LIMIT)}")
    numbers = ", ".join(f"#{number}" for number in sorted(targets.values()))
    lines += ["", f"Tasniflanadigan xabarlar: {numbers}."]
    return "\n".join(lines)


def parse_results(data: dict | None, numbers: dict[int, int]) -> dict[int, ClassificationResult]:
    """Model javobini tekshiradi. `numbers`: so'rovdagi raqam -> xabarning bazadagi ID'si.
    Faqat to'g'ri chiqqan natijalarni qaytaradi (bazadagi ID bo'yicha); raqami takrorlangan,
    ro'yxatda yo'q yoki maydoni noto'g'ri natija tashlab yuboriladi."""
    valid: dict[int, ClassificationResult] = {}
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return valid
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        number = item.get("id")
        if not isinstance(number, int) or number not in numbers or number in seen:
            continue
        seen.add(number)
        related, category, sentiment = item.get("is_company_related"), item.get("category"), item.get("sentiment")
        summary, needs_manager = item.get("summary"), item.get("needs_manager")
        if not isinstance(related, bool) or not isinstance(needs_manager, bool):
            continue
        if category not in CATEGORIES or sentiment not in SENTIMENTS or not isinstance(summary, str):
            continue
        summary = _cut(summary, _SUMMARY_LIMIT) or None
        if not related:
            category = sentiment = None
            needs_manager = False
        elif sentiment == POSITIVE or category == POSITIVE_CATEGORY:
            # Ijobiy fikr qaysi soha haqida bo'lishidan qat'i nazar bitta toifa
            category, sentiment = POSITIVE_CATEGORY, POSITIVE
        valid[numbers[number]] = ClassificationResult(
            message_pk=numbers[number], is_company_related=related, category=category, sentiment=sentiment,
            summary=summary, needs_manager=needs_manager,
        )
    return valid


async def classify_batch(group_title: str | None, conversation: list[Message], batch: list[Message]) -> dict[int, ClassificationResult]:
    """Bitta guruhning bir paket xabarini tasniflaydi. Javobi xato chiqqan xabarlar bir marta
    qayta so'raladi. Natija: bazadagi ID -> tasnif (tasniflab bo'lmaganlari natijada yo'q)."""
    remaining = {message.id for message in batch}
    results: dict[int, ClassificationResult] = {}
    for _attempt in range(2):
        targets = {pk: number for number, pk in enumerate(sorted(remaining), start=1)}
        numbers = {number: pk for pk, number in targets.items()}
        data = await llm_client.request_json(
            SYSTEM_PROMPT, build_transcript(group_title, conversation, targets), RESULT_SCHEMA, "classification"
        )
        results.update(parse_results(data, numbers))
        remaining -= results.keys()
        if not remaining:
            break
        logger.warning("Model %s ta xabar uchun yaroqli natija bermadi -- qayta so'raladi", len(remaining))
    return results


_pass_lock = asyncio.Lock()


async def classify_pending(day: date | None = None, limit: int = 200) -> int:
    """Kutayotgan xabarlarning bir qismini tasniflaydi (`day` berilsa -- faqat shu kunnikini).
    Nechta xabar "pending"dan chiqqanini qaytaradi. Modelga yetib bo'lmasa `LLMUnavailable`.
    Bir vaqtda faqat bitta aylanish ishlaydi: fon vazifa va hisobot oldidagi tasniflash bir xil
    xabarlarni ikki marta modelga yubormaydi."""
    async with _pass_lock:
        return await _classify_pending(day, limit)


async def _classify_pending(day: date | None, limit: int) -> int:
    pending = await monitor_db.pending_messages(limit, day)
    done = 0
    for chat_id, chat_messages in groupby(pending, key=lambda message: message.chat_id):
        # Ovozli xabarlar avval matnga aylantiriladi; bo'lmagani keyingi aylanishga qoladi
        messages = [m for m in chat_messages if not needs_transcript(m) or await transcribe(m)]
        junk = [message for message in messages if is_junk(message)]
        if junk:
            done += len(
                await monitor_db.save_classifications(
                    [ClassificationResult(m.id, False, None, None, None, False) for m in junk], "rule"
                )
            )
        real = [message for message in messages if not is_junk(message)]
        group = await monitor_db.get_group(chat_id) if real else None
        for start in range(0, len(real), settings.CLASSIFY_BATCH_SIZE):
            batch = real[start:start + settings.CLASSIFY_BATCH_SIZE]
            conversation = await monitor_db.conversation(
                chat_id, batch[0].message_id, batch[-1].message_id, settings.CLASSIFY_CONTEXT_MESSAGES
            )
            results = await classify_batch(group.title if group else None, conversation, batch)
            saved = await monitor_db.save_classifications(list(results.values()), llm_client.model_name())
            done += len(saved)
            await monitor_sla.open_checks([pk for pk in saved if results[pk].needs_manager])
            await monitor_db.record_failed_attempt(
                [message.id for message in batch if message.id not in results], settings.CLASSIFY_MAX_ATTEMPTS
            )
            logger.info(
                "Tasnif: guruh=%s, %s ta xabar (shikoyat %s, ijobiy %s, neytral %s, aloqasiz %s), xato %s",
                chat_id, len(results),
                sum(r.sentiment == COMPLAINT for r in results.values()),
                sum(r.sentiment == POSITIVE for r in results.values()),
                sum(r.sentiment == NEUTRAL for r in results.values()),
                sum(not r.is_company_related for r in results.values()),
                len(batch) - len(results),
            )
    return done


# ---------------------------------------------------------------------------
# Fon vazifa
# ---------------------------------------------------------------------------

_wake: asyncio.Event | None = None


def wake() -> None:
    """Yangi xabar yozildi -- tasniflovchini uyg'otadi (yig'uvchi chaqiradi, kutmaydi)."""
    if _wake is not None:
        _wake.set()


async def classifier_loop(bot: Bot | None = None) -> None:
    """Bot jarayonida BITTA nusxada ishlaydi (`app/bot/__init__.py`). `bot` -- ovozli xabar
    fayllarini yuklab olish uchun."""
    global _wake
    set_bot(bot)
    _wake = asyncio.Event()
    while True:
        try:
            done = await classify_pending()
        except LLMUnavailable as exc:
            logger.warning("Tasniflovchi modelga yeta olmadi, 60 soniyadan keyin qayta urinadi: %s", exc)
            await asyncio.sleep(60)
            continue
        except Exception:
            logger.exception("Tasniflashda xatolik")
            await asyncio.sleep(30)
            continue
        if done:
            continue  # navbatda yana bo'lishi mumkin
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), timeout=30)
        except asyncio.TimeoutError:
            continue
        # Ketma-ket kelgan xabarlar (va darhol qilingan tahrir) bitta paketga tushishi uchun
        await asyncio.sleep(2)
