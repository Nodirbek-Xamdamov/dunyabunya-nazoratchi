"""
Mijoz guruhlaridagi xabarlarni tasniflash (2026-10-07) -- `app/bot/handlers/group_monitor.py`
shundan foydalanadi. Har bir xabar uchun quyidagilar aniqlanadi:

  - category          -- mijozning dunyabunya haqidagi SHIKOYATI ("complaint"), IJOBIY FIKRI
                         ("praise") yoki hech qaysi ("none")
  - out_of_stock      -- xabarda dunyabunya tovari yo'q / qolmagan / tugagan deyilganmi
  - promised_minutes  -- xodim "N daqiqada javob beraman" deb va'da berganmi (necha daqiqa)
  - needs_reply       -- mijoz dunyabunyadan javob kutyaptimi (savol, so'rov, buyurtma) --
                         hech kimga murojaat qilinmagan xabar uchun guruh menejeriga
                         javob kutish boshlanadi (faqat LLM rejimida aniqlanadi)
  - about_company     -- xabar umuman dunyabunyaga oidmi (2026-10-12): oid bo'lmasa xabar
                         bazaga yozilmaydi, faqat soni sanaladi
  - topic             -- kompaniyaga oid xabarning sohasi (yetkazib berish, sifat, narx ...).
                         Hisobot va ichki guruh topiklari uchun 8 ta TOIFA shundan chiqadi
                         (`CATEGORIES`): ijobiy fikr -- "Ijobiy Fikrlar", qolgani -- sohasi
                         bo'yicha. `sentiment` ham hisoblab chiqariladi: shikoyat -- negative,
                         ijobiy fikr -- positive, qolgani -- neutral.

Rasm (mijoz yuborgan surat yoki videodan kadrlar) berilsa, model uni matn bilan birga
baholaydi va qisqa tavsifini (`image_note`) qaytaradi -- buning uchun model rasmni ko'ra
olishi kerak (gemma4:12b, gpt-4o-mini).

Guruhdagi yozishmalarning ko'pi (70-80%) oddiy suhbat -- mijozlar o'zaro gaplashadi,
kompaniyaga aloqasi yo'q. Shu sabab asosiy qoida: xabar ANIQ dunyabunya haqida bo'lmasa
hech qayerga tashlanmaydi.

Tasniflashni LLM bajaradi (`GROUP_AI_PROVIDER`): "openai" -- OpenAI modeli (`OPENAI_API_KEY`
kerak), "ollama" -- shu kompyuterdagi Ollama orqali lokal model (xabarlar tashqariga
chiqmaydi, kalit kerak emas). Model nomi -- `GROUP_AI_MODEL`. Model suhbatning oxirgi xabarlarini
(kontekst) ko'radi, xabar kompaniyaga tegishlimi-yo'qmi va o'z xulosasiga necha foiz
ishonishini qaytaradi; `GROUP_AI_MIN_CONFIDENCE`dan past ishonchli xulosa tashlab yuboriladi.

LLM sozlanmagan bo'lsa yoki so'rov xato bersa -- pastdagi oddiy kalit-so'zlar
(`analyze_by_keywords`) ishlaydi. Kalit-so'zlar ma'noni tushunmaydi, shu sabab bu rejimda
shikoyat/ijobiy fikr FAQAT mijoz xodimga to'g'ridan-to'g'ri murojaat qilgan (reply yoki
@username) xabarlarda qidiriladi -- mijozlarning o'zaro suhbati hisobga olinmaydi.
"""

import asyncio
import base64
import json
import logging
import re
import weakref
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
import openai
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

MAX_PROMISED_MINUTES = 240

# Toifa kaliti -> hisobotda va ichki guruh topiklarida ko'rinadigan nom. Tartib -- hisobotdagi tartib.
CATEGORIES: dict[str, str] = {
    "delivery": "Dastavka va Logistika",
    "quality": "Mahsulot Sifati va Nuqsonlar",
    "completeness": "Komplektatsiya va Hajm",
    "staff": "Xodimlar va Xizmat Ko'rsatish",
    "price": "Narx va To'lovlar",
    "returns": "Qaytarish va Kafolat",
    "positive": "Ijobiy Fikrlar",
    "other": "Boshqa",
}
# Model tanlaydigan sohalar: "positive" bu yerda yo'q -- u `category == "praise"`dan chiqadi
TOPICS: tuple[str, ...] = ("delivery", "quality", "completeness", "staff", "price", "returns", "other")


@dataclass(frozen=True)
class MessageAnalysis:
    category: str = "none"  # "complaint" | "praise" | "none"
    out_of_stock: bool = False
    promised_minutes: int | None = None
    needs_reply: bool = False
    about_company: bool = False
    topic: str = "other"  # `TOPICS`dan biri
    image_note: str | None = None  # rasm berilgan bo'lsa -- unda nima ko'ringani (bir jumla)

    @property
    def company_related(self) -> bool:
        return self.about_company or self.category != "none"

    @property
    def category_key(self) -> str:
        """8 ta toifadan biri (`CATEGORIES` kaliti)."""
        return "positive" if self.category == "praise" else self.topic

    @property
    def category_name(self) -> str:
        return CATEGORIES[self.category_key]

    @property
    def sentiment(self) -> str:
        return {"complaint": "negative", "praise": "positive"}.get(self.category, "neutral")


# ---------------------------------------------------------------------------
# Kalit-so'zlar bo'yicha (LLM'siz) tasniflash
# ---------------------------------------------------------------------------

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "`": "'", "ʻ": "'", "ʼ": "'"})

_STOCK_NOUN = r"(?:tovar|mahsulot|товар|маҳсулот|махсулот)\w*"
_STOCK_GONE = (
    r"(?:yo'q|qolmagan|qolmadi|qolmabdi|tugagan|tugadi|tugabdi|"
    r"йўқ|йук|қолмаган|қолмади|тугаган|тугади|нет|нету|закончил\w*|отсутству\w*)"
)
# "tovar yo'q bo'lsa ..." -- shart, fakt emas
_NOT_CONDITIONAL = r"(?!\s+(?:bo'lsa|bulsa|бўлса|булса))"
_OUT_OF_STOCK_RE = re.compile(
    rf"{_STOCK_NOUN}\s+(?:[\w']+\s+){{0,3}}{_STOCK_GONE}{_NOT_CONDITIONAL}"
    rf"|(?:нет|нету)\s+(?:в\s+наличии|на\s+складе|товар\w*)"
    rf"|(?:omborda|skladda|складда|омборда|на\s+складе)\s+(?:[\w']+\s+){{0,2}}{_STOCK_GONE}{_NOT_CONDITIONAL}",
    re.IGNORECASE,
)

_COMPLAINT_RE = re.compile(
    r"shikoyat|norozi|yomon(?!\s+emas)|sifatsiz|buzilgan|buzuq|aldad|aldash|uyat|rasvo|"
    r"шикоят|норози|ёмон(?!\s+эмас)|сифатсиз|бузилган|бузуқ|алдад|уят|"
    r"жалоб|(?<!не)плох|ужас|недовол|обман|безобраз|отвратит|кошмар",
    re.IGNORECASE,
)
_PRAISE_RE = re.compile(
    r"ajoyib|a'lo|mamnun|minnatdor|barakalla|tashakkur|katta rahmat|xursandman|"
    r"ажойиб|аъло|мамнун|миннатдор|баракалла|ташаккур|катта раҳмат|хурсандман|"
    r"спасибо большое|большое спасибо|молодц|(?<!не)довол|благодар",
    re.IGNORECASE,
)

_MINUTES_RE = re.compile(r"(\d{1,3})\s*(?:daqiqa|daq\b|minut|min\b|дақиқа|дакика|минут|мин\b)", re.IGNORECASE)
_HOURS_RE = re.compile(r"(\d{1,2})\s*(?:soat|соат|час)", re.IGNORECASE)
_HALF_HOUR_RE = re.compile(r"yarim\s+soat|ярим\s+соат|полчаса", re.IGNORECASE)


def _clamp_minutes(minutes: int | None) -> int | None:
    if not minutes or minutes <= 0:
        return None
    return min(minutes, MAX_PROMISED_MINUTES)


def find_durations(text: str) -> list[int]:
    """Matndagi barcha muddatlarni ("10 minutda", "5 daqiqa", "1 soatda", "yarim soat") daqiqada
    qaytaradi: avval daqiqalar, keyin soatlar, keyin "yarim soat"."""
    minutes = [int(match.group(1)) for match in _MINUTES_RE.finditer(text)]
    minutes += [int(match.group(1)) * 60 for match in _HOURS_RE.finditer(text)]
    if _HALF_HOUR_RE.search(text):
        minutes.append(30)
    return [clamped for value in minutes if (clamped := _clamp_minutes(value))]


def parse_promised_minutes(text: str) -> int | None:
    """Matndan birinchi muddatni daqiqada ajratadi (bo'lmasa None)."""
    durations = find_durations(text)
    return durations[0] if durations else None


def analyze_by_keywords(text: str, is_staff: bool, addressed_to_staff: bool = False) -> MessageAnalysis:
    """Kalit-so'zlar bo'yicha tasnif. Shikoyat/ijobiy fikr faqat mijoz xodimga murojaat qilgan
    xabarda qidiriladi (`addressed_to_staff`) -- aks holda mijozlarning o'zaro oddiy suhbati
    ham ("bugun havo yomon") topiklarga tushib qolardi."""
    normalized = text.translate(_APOSTROPHES)
    category = "none"
    if not is_staff and addressed_to_staff:
        if _COMPLAINT_RE.search(normalized):
            category = "complaint"
        elif _PRAISE_RE.search(normalized):
            category = "praise"
    out_of_stock = bool(_OUT_OF_STOCK_RE.search(normalized))
    return MessageAnalysis(
        category=category,
        out_of_stock=out_of_stock,
        promised_minutes=parse_promised_minutes(normalized) if is_staff else None,
        # Kalit-so'zlar xabar kompaniyaga oidligini bila olmaydi -- faqat nimadir topilgani aniq
        about_company=not is_staff and (category != "none" or out_of_stock),
    )


# ---------------------------------------------------------------------------
# LLM (OpenAI yoki lokal Ollama) orqali tasniflash
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Sen "dunyabunya" kompaniyasining mijozlar bilan ishlaydigan Telegram guruhlaridagi \
xabarlarni tasniflaysan. Kompaniya qurilish mahsulotlari (sement, g'isht, armatura, \
gipsokarton, bo'yoq va hokazo) sotadi va mijozlarga yetkazib beradi. Xabarlar \
o'zbek tilida (lotin yoki kirill), rus tilida yoki aralash, ko'pincha og'zaki uslubda va \
imlo xatolari bilan yoziladi. Natijang odamga ko'rsatilmaydi -- uni dastur o'qiydi va \
shunga qarab xabarni kompaniyaning ichki guruhidagi kerakli bo'limga yo'naltiradi. Ichki \
guruhni xodimlar o'qiydi, shuning uchun u yerga keraksiz xabar tushishi ularni chalg'itadi.

Eng muhimi: bu guruhlardagi yozishmalarning 70-80 foizi oddiy suhbat. Odamlar o'zaro \
gaplashadi, hazillashadi, o'z ishlari, ob-havo, boshqa firmalar, boshqa odamlar haqida \
yozadi -- bularning dunyabunyaga aloqasi yo'q va ularni hech qayerga yo'naltirish kerak \
emas. Seni faqat dunyabunyaning o'zi haqidagi xabarlar qiziqtiradi: uning tovari, narxi, \
yetkazib berishi, xizmati yoki xodimlari. Xabar kimga va nima haqida aytilganini \
suhbatning oldingi xabarlaridan aniqla: mijoz boshqa mijozga javob yozayotgan bo'lsa va \
gap kompaniya haqida bo'lmasa -- bu oddiy suhbat.

Har bir so'rovda suhbatning oxirgi xabarlari (kontekst) va tasniflanadigan xabar \
beriladi. Faqat tasniflanadigan xabarni bahola, kontekst faqat tushunish uchun.

about_company -- xabar dunyabunya (tovari, narxi, yetkazib berishi, xizmati, xodimi) \
haqida bo'lsa true. Bu guruh dunyabunya va uning mijozlari uchun ochilgan, shuning uchun \
hech kimga qaratilmagan tovar, narx, buyurtma, to'lov yoki yetkazib berish haqidagi savol \
va so'rov dunyabunyaga berilgan hisoblanadi (true) -- agar oldingi xabarlardan uning \
boshqa mijozga yoki boshqa joy haqida aytilgani ko'rinib turmasa. Mijozlarning o'zaro \
oddiy suhbati, salom-alik, boshqa firma, bozor yoki do'kon haqidagi gap -- false.

category -- faqat MIJOZ xabari uchun ma'noga ega:
- "complaint": mijoz dunyabunyadan norozi: shikoyat qilyapti, e'tiroz bildiryapti, \
kechikish/sifat/xato/qo'pollik/javobsiz qolganidan ranjiyapti. Oddiy savol ("qachon \
keladi?") yoki buyurtma shikoyat emas; lekin "uch kundan beri kutyapman, hech kim javob \
bermayapti" -- shikoyat.
- "praise": mijoz dunyabunya xizmatidan, tovaridan yoki xodimidan mamnunligini \
bildiryapti, maqtayapti, chin dildan minnatdorlik bildiryapti. Suhbat oxiridagi odob \
yuzasidan aytilgan "rahmat", "ok rahmat", rozilik bildiruvchi "zo'r, ertaga olaman" -- \
ijobiy sharh emas.
- "none": qolgan hamma holat, jumladan about_company false bo'lgan har qanday xabar. \
Xodim yozgan xabar uchun doim "none".

out_of_stock -- xabarda dunyabunyaning biror tovari yo'qligi, qolmagani, tugagani, \
omborda yo'qligi FAKT sifatida aytilgan bo'lsa true (kim yozganidan qat'i nazar: xodim \
"bu tovar yo'q ekan" desa ham, mijoz "tovar yo'q deyishdi" desa ham). Faqat \
dunyabunyaning O'ZIDA (uning ombori, do'koni) tovar yo'qligi hisoblanadi: bozorda, boshqa \
do'konda, boshqa firmada yoki umuman shaharda yo'qligi haqidagi gap -- false. Savol \
("sement bormi?"), shart ("yo'q bo'lsa boshqasini bering"), tovardan boshqa narsaning \
yo'qligi (chegirma, vaqt, transport) ham false.

confidence -- category va out_of_stock bo'yicha xulosangga ishonching, 0 dan 100 gacha. \
Xabar aniq va bir ma'noli bo'lsa 90 va undan yuqori; ohangi yoki kimga qaratilgani \
noaniq bo'lsa, kinoya yoki hazil bo'lishi mumkin bo'lsa -- pastroq. category "none" va \
out_of_stock false bo'lsa bu hech narsa topilmaganiga ishonching.

needs_reply -- faqat MIJOZ xabari uchun: mijoz dunyabunyadan javob kutyapti -- narx yoki \
tovar haqida so'rayapti, buyurtma beryapti, biror narsani iltimos qilyapti yoki javob \
talab qiladigan shikoyat yozyapti ("menga sement kerak, qanchadan?", "ertaga yetkazib \
bera olasizmi?"). Hech kimga qaratilmagan, lekin kompaniyaga aytilgan savol ham true. \
Savol belgisi bo'lmasa ham, mijoz nimadir kerakligini yoki olmoqchi ekanini yozsa -- bu \
buyurtma, uni kompaniya tasdiqlashi kerak (true). \
Suhbatni yakunlovchi xabar ("ok", "rahmat", "tushunarli", "ertaga olaman"), maqtov, \
mijozlarning o'zaro gapi va about_company false bo'lgan har qanday xabar -- false. \
Xodim xabari uchun doim false.

reply_promise -- faqat XODIM xabari uchun: xodim mijozga hozir javob bermay, keyinroq \
JAVOB YOZISHINI (aytishini, aniqlab berishini, xabar berishini) va'da qilyapti yoki kutib \
turishni so'rayapti ("10 minutda javob beraman", "yarim soatda aytaman", "5 daqiqa \
kuting", "hozir aniqlab aytaman", "через час отвечу") -- true. Xabar javobning o'zi \
bo'lsa -- false, hatto unda vaqt aytilgan bo'lsa ham: yetkazib berish, mashina chiqishi, \
tovar kelishi yoki ish tugashi muddati ("tovar 30 minutda yetib boradi", "kechqurun olib \
boramiz", "bir haftada keladi") javob yozish va'dasi emas. Mijoz xabari uchun doim false.

promised_minutes -- reply_promise true bo'lsa, xodim aytgan muddat daqiqalarda (yarim \
soat = 30, bir soat = 60). Muddat aytilmagan bo'lsa ("hozir aniqlab aytaman", "bir \
daqiqa") yoki reply_promise false bo'lsa -- 0.

topic -- xabar dunyabunyaning qaysi sohasiga oid. Faqat about_company true bo'lgan MIJOZ \
xabari uchun ma'noga ega, qolgan hamma holatda "other". Xabar shikoyatmi, savolmi yoki \
maqtovmi -- farqi yo'q, faqat mavzusiga qara:
- "delivery": yetkazib berish va logistika -- tovar qachon keladi, kechikdi, kelmadi, \
mashina, haydovchi yo'lda, yetkazish manzili, yuk tushirish, yetkazib berish narxi va sharti.
- "quality": mahsulot sifati va nuqsonlari -- brak, singan, yorilgan, qotib qolgan, sifati \
past, muddati o'tgan, shikastlangan tovar.
- "completeness": komplektatsiya va hajm -- kam yoki ortiqcha keldi, yetishmayapti, \
buyurtma to'liq emas, boshqa tovar, o'lcham, rang yoki marka keldi, soni yoki og'irligi \
to'g'ri kelmadi.
- "staff": xodimlar va xizmat ko'rsatish -- menejer, operator, haydovchi yoki sotuvchining \
muomalasi, qo'pollik, telefonni ko'tarmaslik, javob bermaslik, va'dani bajarmaslik, xizmat \
darajasi.
- "price": narx va to'lovlar -- tovar narxi, chegirma, qimmatlik, hisob-faktura, to'lov \
usuli, to'lov o'tmagani, qarz, ortiqcha pul olingani.
- "returns": qaytarish va kafolat -- tovarni qaytarish, almashtirish, pulni qaytarish, \
kafolat sharti.
- "other": dunyabunyaga oid, lekin yuqoridagilarga to'g'ri kelmaydigan hamma narsa (tovar \
bor-yo'qligi, buyurtma berish, ish vaqti, do'kon manzili, umumiy savol).
Xabar bir nechta sohaga to'g'ri kelsa, mijozni ranjitgan yoki qiziqtirgan ASOSIY sababni \
tanla: "g'isht singan keldi, pulimni qaytaring" -- "quality" (sabab -- nuqson); "kechikib \
keldi, haydovchi ham qo'pol" -- "delivery"."""

# Faqat rasm biriktirilgan so'rovlarda qo'shiladi -- matnli xabarlar prompti o'zgarmaydi
_IMAGE_PROMPT = """

Bu so'rovga rasm(lar) biriktirilgan: guruhga yuborilgan surat yoki videodan olingan \
kadrlar. Rasmda nima ko'rinayotganini xabar matni va oldingi xabarlar bilan birga bahola. \
Singan, yorilgan, namlangan, noto'g'ri yoki kam kelgan tovar surati -- odatda shikoyat \
(topic "quality" yoki "completeness"), hatto matn qisqa bo'lsa yoki umuman bo'lmasa ham. \
Hujjat, chek, hisob-faktura, to'lov skrinshoti, obyekt yoki yuk tushirilayotgan oddiy \
surat, manzil xaritasi -- oddiy ish yozishmasi (about_company true, category "none"). \
Dunyabunyaga aloqasi ko'rinmaydigan rasm (odamlar, ovqat, hazil rasm, boshqa firma \
reklamasi) -- about_company false. Rasm matnsiz kelgan va nimaligi noaniq bo'lsa -- \
confidence past bo'lsin.

image_description -- rasmda nima ko'rinayotganini o'zbek tilida bitta qisqa jumla bilan \
yoz (masalan: "Singan g'ishtlar taxlami", "To'lov cheki skrinshoti")."""

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "about_company": {"type": "boolean"},
        "category": {"type": "string", "enum": ["complaint", "praise", "none"]},
        "out_of_stock": {"type": "boolean"},
        "confidence": {"type": "integer"},
        "needs_reply": {"type": "boolean"},
        "reply_promise": {"type": "boolean"},
        "promised_minutes": {"type": "integer"},
        # Oxirida turadi: model maydonlarni shu tartibda yozadi, yangi maydon oldingilariga ta'sir qilmaydi
        "topic": {"type": "string", "enum": list(TOPICS)},
    },
    "required": [
        "about_company", "category", "out_of_stock", "confidence", "needs_reply", "reply_promise",
        "promised_minutes", "topic",
    ],
    "additionalProperties": False,
}
# Rasmli so'rov: model avval rasmni tasvirlaydi (birinchi maydon), keyin tasniflaydi
_IMAGE_OUTPUT_SCHEMA = {
    **_OUTPUT_SCHEMA,
    "properties": {"image_description": {"type": "string"}, **_OUTPUT_SCHEMA["properties"]},
    "required": ["image_description", *_OUTPUT_SCHEMA["required"]],
}
_IMAGE_NOTE_LIMIT = 200

_client: AsyncOpenAI | None = None
_ollama_client: httpx.AsyncClient | None = None
# Har bir event loop uchun alohida: testlarda har test o'z loop'ida ishlaydi
_llm_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _llm_slot() -> asyncio.Semaphore:
    """Modelga bir vaqtda ketadigan so'rovlar sonini cheklaydi (`GROUP_AI_MAX_CONCURRENCY`)."""
    loop = asyncio.get_running_loop()
    slot = _llm_slots.get(loop)
    if slot is None:
        slot = _llm_slots[loop] = asyncio.Semaphore(max(1, settings.GROUP_AI_MAX_CONCURRENCY))
    return slot


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=30.0, max_retries=1)
    return _client


def _get_ollama_client() -> httpx.AsyncClient:
    global _ollama_client
    if _ollama_client is None:
        # Model xotiradan tushib qolgan bo'lsa birinchi so'rov uni qayta yuklaydi (10-20 soniya)
        _ollama_client = httpx.AsyncClient(base_url=settings.OLLAMA_URL, timeout=90.0)
    return _ollama_client


def _provider_ready(provider: str) -> bool:
    if provider == "ollama":
        return True
    return bool(settings.OPENAI_API_KEY)


def _build_user_content(text: str, is_staff: bool, context: str, has_images: bool = False) -> str:
    sender = "xodim" if is_staff else "mijoz"
    parts = []
    if context:
        parts.append(f"Suhbatning oldingi xabarlari:\n{context}")
    if has_images and not text.strip():
        text = "(matnsiz, faqat rasm)"
    parts.append(f"Tasniflanadigan xabar ({sender} yozgan):\n{text}")
    return "\n\n".join(parts)


def _is_reasoning_model(model: str) -> bool:
    """`reasoning_effort` faqat "o'ylaydigan" modellarda bor (gpt-5*, o1/o3/o4); gpt-4o va
    gpt-4.1 oilasi bu parametrni rad etadi."""
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


async def _request_openai(
    system_prompt: str, user_text: str, images: Sequence[bytes], model: str, schema: dict
) -> dict | None:
    content: str | list[dict] = user_text
    if images:
        content = [{"type": "text", "text": user_text}] + [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode(), "detail": "low"},
            }
            for image in images
        ]
    # Tasniflash oddiy vazifa -- chuqur o'ylash kerak emas
    extra = {"reasoning_effort": "low"} if _is_reasoning_model(model) else {"temperature": 0}
    try:
        response = await _get_client().chat.completions.create(
            model=model,
            max_completion_tokens=2000,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "message_analysis", "strict": True, "schema": schema},
            },
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": content}],
            **extra,
        )
    except openai.AuthenticationError:
        logger.error("OPENAI_API_KEY noto'g'ri -- guruh xabarlari kalit-so'zlar bo'yicha tasniflanadi")
        return None
    except openai.RateLimitError:
        logger.warning("OpenAI rate limit/kvota -- bu xabar kalit-so'zlar bo'yicha tasniflanadi")
        return None
    except openai.APIStatusError as exc:
        logger.warning("OpenAI xatosi (%s): %s", exc.status_code, exc.message)
        return None
    except openai.APIConnectionError as exc:
        logger.warning("OpenAI'ga ulanib bo'lmadi: %s", exc)
        return None

    choice = response.choices[0]
    if choice.finish_reason != "stop" or not choice.message.content:
        logger.warning("OpenAI tasnifni tugatmadi (finish_reason=%s)", choice.finish_reason)
        return None
    try:
        return json.loads(choice.message.content)
    except ValueError:
        logger.warning("OpenAI javobini o'qib bo'lmadi")
        return None


async def _request_ollama(
    system_prompt: str, user_text: str, images: Sequence[bytes], model: str, schema: dict
) -> dict | None:
    """Shu kompyuterdagi Ollama (`OLLAMA_URL`) orqali lokal model. `format` -- JSON sxema:
    Ollama javobni shu sxemaga majburlaydi, shu sabab javob har doim to'g'ri JSON bo'ladi."""
    user_message: dict = {"role": "user", "content": user_text}
    if images:
        user_message["images"] = [base64.b64encode(image).decode() for image in images]
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}, user_message],
        "stream": False,
        "format": schema,
        # "O'ylash" rejimi javobni bir necha barobar sekinlashtiradi, tasniflashga kerak emas
        "think": False,
        "keep_alive": settings.GROUP_AI_KEEP_ALIVE,
        "options": {"temperature": 0, "num_ctx": 8192},
    }
    try:
        response = await _get_ollama_client().post("/api/chat", json=payload)
        response.raise_for_status()
        return json.loads(response.json()["message"]["content"])
    except httpx.HTTPStatusError as exc:
        logger.warning("Ollama xatosi (%s): %s", exc.response.status_code, exc.response.text[:200])
    except httpx.HTTPError as exc:
        logger.warning("Ollama'ga ulanib bo'lmadi (%s): %r", settings.OLLAMA_URL, exc)
    except (ValueError, KeyError, TypeError):
        logger.warning("Ollama javobini o'qib bo'lmadi")
    return None


async def _request_llm(
    text: str,
    is_staff: bool,
    context: str = "",
    *,
    images: Sequence[bytes] = (),
    provider: str | None = None,
    model: str | None = None,
) -> dict | None:
    """Modeldan xom tasnifni (sxemadagi maydonlar) oladi. Har qanday xatoda None qaytaradi.
    `provider`/`model` berilmasa -- `GROUP_AI_PROVIDER`/`GROUP_AI_MODEL`."""
    provider = provider or settings.GROUP_AI_PROVIDER
    model = model or settings.GROUP_AI_MODEL
    system_prompt = _SYSTEM_PROMPT + _IMAGE_PROMPT if images else _SYSTEM_PROMPT
    schema = _IMAGE_OUTPUT_SCHEMA if images else _OUTPUT_SCHEMA
    user_text = _build_user_content(text, is_staff, context, has_images=bool(images))
    request = _request_ollama if provider == "ollama" else _request_openai
    async with _llm_slot():
        return await request(system_prompt, user_text, images, model, schema)


def _promised_minutes(text: str, minutes: int) -> int:
    """Xodim keyinroq javob berishni va'da qildi -- necha daqiqaga. Model raqamni ba'zan
    noto'g'ri ko'chiradi ("15 минут" -> 5), shu sabab matnda raqam bilan yozilgan muddat bo'lsa
    va model aytgan son ular orasida bo'lmasa, matndagisi olinadi. Muddat aytilmagan bo'lsa
    ("hozir aniqlab aytaman") -- odatdagi javob kutish vaqti."""
    durations = find_durations(text.translate(_APOSTROPHES))
    if durations and minutes not in durations:
        minutes = durations[0]
    # "Bir daqiqa", "minutku" kabi va'da kutishni odatdagi muddatdan qisqartirmaydi
    return max(_clamp_minutes(minutes) or 0, settings.STAFF_REPLY_TIMEOUT_MINUTES)


def _interpret(data: dict, is_staff: bool, text: str = "") -> MessageAnalysis | None:
    """Modelning xom javobiga "kompaniya haqidami" va ishonch chegarasini qo'llaydi."""
    try:
        about_company = bool(data["about_company"])
        category = data["category"]
        out_of_stock = bool(data["out_of_stock"])
        confidence = int(data["confidence"])
        needs_reply = bool(data["needs_reply"])
        reply_promise = bool(data["reply_promise"])
        promised_minutes = int(data["promised_minutes"])
    except (ValueError, KeyError, TypeError):
        logger.warning("Model javobidagi maydonlarni o'qib bo'lmadi")
        return None
    if category not in ("complaint", "praise", "none"):
        category = "none"
    topic = data.get("topic")
    if topic not in TOPICS or not about_company or is_staff:
        topic = "other"
    image_note = " ".join(str(data.get("image_description") or "").split())[:_IMAGE_NOTE_LIMIT] or None
    promised = _promised_minutes(text, promised_minutes) if is_staff and reply_promise else None

    # Chegarani sozlash uchun har bir xulosa logga yoziladi (xabar matni yozilmaydi)
    logger.info(
        "Tasnif: about_company=%s category=%s topic=%s out_of_stock=%s needs_reply=%s confidence=%s promised=%s",
        about_company, category, topic, out_of_stock, needs_reply, confidence, promised,
    )
    # Kompaniyaga aloqasiz yoki ishonchi past xulosa -- hech qayerga tashlanmaydi
    trusted = about_company and confidence >= settings.GROUP_AI_MIN_CONFIDENCE
    return MessageAnalysis(
        category=category if trusted and not is_staff else "none",
        out_of_stock=out_of_stock and trusted,
        promised_minutes=promised,
        needs_reply=needs_reply and trusted and not is_staff,
        about_company=about_company,
        topic=topic,
        image_note=image_note,
    )


async def _analyze_with_llm(
    text: str,
    is_staff: bool,
    context: str = "",
    *,
    images: Sequence[bytes] = (),
    provider: str | None = None,
    model: str | None = None,
) -> MessageAnalysis | None:
    """Modeldan tasnif oladi. Har qanday xatoda None qaytaradi (chaqiruvchi kalit-so'zlarga o'tadi)."""
    data = await _request_llm(text, is_staff, context, images=images, provider=provider, model=model)
    return _interpret(data, is_staff, text) if data is not None else None


def _has_words(text: str) -> bool:
    return sum(ch.isalpha() for ch in text) >= 2


async def analyze_message(
    text: str,
    is_staff: bool,
    *,
    context: str = "",
    addressed_to_staff: bool = False,
    images: Sequence[bytes] = (),
    provider: str | None = None,
    model: str | None = None,
) -> MessageAnalysis:
    """Xabarni (matni va, bo'lsa, rasmlarini) tasniflaydi. Hech qachon xato tashlamaydi.

    `context` -- shu guruhdagi oldingi bir necha xabar (model xabar kimga va nima haqida
    ekanini tushunishi uchun). `addressed_to_staff` -- mijoz xodimga reply/@username orqali
    murojaat qilganmi (kalit-so'z rejimida shikoyat/ijobiy fikr faqat shunda qidiriladi).
    `images` -- JPEG baytlari (surat yoki video kadrlari). `provider`/`model` -- shu xabar
    uchun boshqa model (masalan ovozli xabar va video uchun OpenAI); u ishlamasa asosiy model
    (`GROUP_AI_PROVIDER`), u ham ishlamasa kalit-so'zlar."""
    images = tuple(images)
    if not images and not _has_words(text):
        return MessageAnalysis()

    default = (settings.GROUP_AI_PROVIDER, settings.GROUP_AI_MODEL)
    attempts = [(provider, model or settings.GROUP_AI_MODEL)] if provider else []
    if default not in attempts:
        attempts.append(default)
    for attempt_provider, attempt_model in attempts:
        if not _provider_ready(attempt_provider):
            continue
        result = await _analyze_with_llm(
            text, is_staff, context, images=images, provider=attempt_provider, model=attempt_model
        )
        if result is None and images and _has_words(text):
            # Model rasmni qabul qilmagan bo'lishi mumkin (rasm ko'ra olmaydigan model) -- matnning o'zi
            result = await _analyze_with_llm(
                text, is_staff, context, provider=attempt_provider, model=attempt_model
            )
        if result is not None:
            return result
    return analyze_by_keywords(text, is_staff, addressed_to_staff)
