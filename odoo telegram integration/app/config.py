"""
Ilova konfiguratsiyasi.
Barcha sozlamalar .env faylidan yoki environment variable'lardan o'qiladi.
"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/app.db"

    # FastAPI xavfsizligi
    API_KEY: str

    # Aiogram Bot
    BOT_TOKEN: str
    BOT_USERNAME: str  # Masalan: "@SizningBotUsername"

    # Mijoz xabarlari uzatiladigan support akkaunt (username, "@"siz)
    SUPPORT_USERNAME: str = "dunyabunya_support"

    # Telethon Userbot
    TELEGRAM_API_ID: int
    TELEGRAM_API_HASH: str
    TELETHON_SESSION_NAME: str = "userbot_session"

    # Fayl saqlash joylari
    PDF_STORAGE_DIR: str = "./data/pdfs"
    WEBHOOK_LOG_DIR: str = "./data/webhook_logs"

    # taklif-bot (haydovchi yetkazib berish rasmlarini yuboradigan alohida loyiha)dan
    # POST /api/v1/delivery-confirmations orqali kelgan rasmlar shu yerga saqlanadi
    DELIVERY_PHOTOS_DIR: str = "./data/delivery_photos"

    # "Buyurtma berish" oqimida mijoz yuborgan rasm/video/ovozli xabar shu yerga
    # saqlanadi (2026-08-03) -- bot va fastapi konteynerlari orasida ./data ulushilgan
    # volume orqali bir xil: bot yuklab oladi, fastapi shu yerdan o'qib Odoo'ga
    # ir.attachment sifatida yuklaydi (HTTP orqali ochiq ko'rsatilmaydi -- URL yondashuvi
    # 2026-08-03'da sinab ko'rilgach haqiqiy Odoo biriktirmasi foydasiga almashtirildi).
    ORDER_MEDIA_DIR: str = "./data/order_media"

    # Bot menyu tugmalari (balance/orders/quotation/support/info) shu manzildagi
    # FastAPI'ga ichki so'rov (localhost) yuboradi
    INTERNAL_API_URL: str = "http://127.0.0.1:8000"

    # Odoo bilan to'g'ridan-to'g'ri (XML-RPC) integratsiya -- Balans va Tijorat
    # takliflarim bo'limlari uchun. Sozlanmagan bo'lsa bu bo'limlar placeholder
    # javob bilan ishlashda davom etadi (app/odoo_client.py).
    ODOO_URL: str | None = None
    ODOO_DB: str | None = None
    ODOO_USERNAME: str | None = None  # Odoo login (email)
    ODOO_API_KEY: str | None = None

    # Biznes mantiq sozlamalari
    ORDER_EXPIRATION_HOURS: int = 48
    USERBOT_POLL_INTERVAL_SECONDS: int = 15
    EXPIRATION_CHECK_INTERVAL_SECONDS: int = 3600
    GROUP_SYNC_INTERVAL_SECONDS: int = 900

    # Odoo API kaliti odatda ~90 kunda muddati tugaydi -- shuni "unutib qo'yish" holiga
    # qarshi (2026-08-06) davriy tekshiruv: agar Odoo'ga autentifikatsiya ishlamay qolsa,
    # shu chat_id'ga Telegram orqali ogohlantirish yuboriladi (`app/utils.py`,
    # `odoo_health_check_loop`). Standart qiymat -- NodirXamdamov (chat_id
    # tasdiqlangan: `telegram_users` jadvalida username="NodirXamdamov").
    ODOO_HEALTH_CHECK_INTERVAL_SECONDS: int = 21600
    ODOO_ALERT_CHAT_ID: int = 5817708955

    # Mijoz guruhlarini kuzatish (2026-10-07, `app/bot/handlers/group_monitor.py`): bot
    # mijozlar bor guruhlarda FAQAT o'qiydi (hech narsa yozmaydi), shikoyat/ijobiy sharh/
    # "tovar yo'q" xabarlarini shu ichki (xodimlar) forum-guruhining mos topigiga tashlaydi.
    # Shu guruh a'zolari "dunyabunya xodimi" deb hisoblanadi. Sozlanmagan bo'lsa kuzatish
    # butunlay o'chiq. `.env`da CENTRAL_FORUM_CHAT_ID deb ham yozish mumkin.
    FEEDBACK_HUB_GROUP_ID: int | None = Field(
        default=None, validation_alias=AliasChoices("FEEDBACK_HUB_GROUP_ID", "CENTRAL_FORUM_CHAT_ID")
    )
    # Topik ID'lari odatda topik nomidan avtomatik aniqlanadi (`hub_topics` jadvali);
    # nom mos kelmasa shu yerda qo'lda belgilash mumkin.
    HUB_TOPIC_COMPLAINTS_ID: int | None = None  # toifa topigi topilmagan shikoyatlar uchun umumiy topik
    HUB_TOPIC_PRAISE_ID: int | None = None
    HUB_TOPIC_STOCK_ID: int | None = None
    # Toifa -> topik ID (2026-10-12), masalan "delivery=12,quality=13,price=14". Kalitlar:
    # delivery, quality, completeness, staff, price, returns, positive, other, stock, unanswered
    HUB_TOPIC_IDS: str = ""
    # Toifaga mos topik topilmasa bot uni o'zi yaratadi (ichki guruhda "Mavzularni boshqarish"
    # huquqi kerak). O'chiq bo'lsa yoki huquq yo'q bo'lsa -- umumiy shikoyat topigi / General.
    HUB_AUTO_CREATE_TOPICS: bool = True
    # Kuzatilmaydigan guruhlar (vergul bilan ajratilgan chat_id'lar, masalan call-center guruhi)
    GROUP_MONITOR_EXCLUDED_GROUP_IDS: str = ""
    # Mijoz xodimga murojaat qilgach, xodim shuncha daqiqa ichida javob yozmasa eslatma boradi
    STAFF_REPLY_TIMEOUT_MINUTES: int = 5
    STAFF_REPLY_CHECK_INTERVAL_SECONDS: int = 15
    # Mijoz guruhida admin unvonida (custom title) shu bo'laklardan biri bo'lgan xodim shu
    # guruhning mas'ul MENEJERI hisoblanadi: mijoz hech kimga murojaat qilmay savol yozsa,
    # javob shu menejerdan kutiladi.
    GROUP_MANAGER_TITLE_KEYWORDS: str = "menej,meneg,menedj,manag,менедж,менеж"
    # Javobsiz qolgan savol bo'yicha ogohlantirish qayerga boradi (2026-10-12):
    #   "dm"    -- xodimning/menejerning shaxsiy chatiga; bot mijoz guruhiga HECH NARSA yozmaydi
    #   "group" -- mijoz guruhining o'ziga alohida (reply'siz) xabar: shu guruhning barcha
    #              ro'yxatdagi menejerlari @username bilan belgilanadi. Bu rejimda guruhdagi
    #              ISTALGAN xodim xabari kutishni bekor qiladi (`app/group_timers.py`).
    GROUP_ALERT_MODE: str = "dm"
    # "group" rejimida guruhga yoziladigan matn (menejerlar belgilangan qatordan keyin)
    GROUP_ALERT_TEXT: str = "Mijozning savoli javobsiz qoldi. Iltimos, javob bering."
    # Ogohlantirish yuborilgach, xodim javob yozmaguncha shu guruhda keyingisi shuncha daqiqadan
    # oldin yuborilmaydi (mijozlar ko'z o'ngida ketma-ket belgilab tashlamaslik uchun)
    GROUP_ALERT_REPEAT_MINUTES: int = 60

    # Guruh xabarlarini tasniflash (shikoyat / ijobiy sharh / tovar yo'q / "N daqiqada javob
    # beraman") uchun OpenAI. Kalit berilmasa oddiy kalit-so'zlar bo'yicha ishlaydi
    # (`app/group_ai.py`).
    # GROUP_AI_PROVIDER: "openai" yoki "ollama" (shu kompyuterdagi lokal model, kalit kerak emas).
    GROUP_AI_PROVIDER: str = "openai"
    OPENAI_API_KEY: str | None = None
    GROUP_AI_MODEL: str = "gpt-5.4-mini"
    # Lokal Gemma (Ollama) manzili; `.env`da GEMMA_API_URL deb ham yozish mumkin
    OLLAMA_URL: str = Field(
        default="http://127.0.0.1:11434", validation_alias=AliasChoices("OLLAMA_URL", "GEMMA_API_URL")
    )
    # Lokal model oxirgi so'rovdan keyin qancha vaqt videokarta xotirasida turadi ("-1" = doim)
    GROUP_AI_KEEP_ALIVE: str = "30m"
    # Modelga bir vaqtda nechta so'rov ketadi. Qolganlari shu jarayonda navbat kutadi -- 200+
    # guruhdan xabar to'lqini kelganda so'rovlar kutish vaqti (timeout) tufayli yo'qolmaydi.
    GROUP_AI_MAX_CONCURRENCY: int = 2

    # Rasm tahlili (2026-10-12, `app/ai_processor.py`): mijoz yuborgan surat (va video kadrlari)
    # matn bilan birga tasniflovchi modelga beriladi. Model rasmni ko'ra olishi kerak
    # (gemma4:12b, gpt-4o-mini ko'radi).
    GROUP_IMAGE_ANALYSIS_ENABLED: bool = False
    GROUP_IMAGE_MAX_SIDE: int = 1024
    # Videodan nechta kadr olinadi (0 = faqat ovozi tahlil qilinadi)
    GROUP_VIDEO_FRAMES: int = 3

    # Ovozli xabar / audio / video TAHLILI uchun alohida model: bo'sh = matn bilan bir xil
    # (GROUP_AI_PROVIDER / GROUP_AI_MODEL). "openai" -- GROUP_MEDIA_AI_MODEL (masalan
    # gpt-4o-mini) orqali; kalit bo'lmasa yoki so'rov xato bersa asosiy modelga qaytadi.
    GROUP_MEDIA_AI_PROVIDER: str = ""
    GROUP_MEDIA_AI_MODEL: str = "gpt-4o-mini"

    # Ovozli xabar / audio / video (faqat ovozi) / dumaloq video -> matn (`app/stt.py`), so'ng
    # oddiy matnli xabar kabi tasniflanadi.
    GROUP_STT_ENABLED: bool = False
    # "local"  -- shu kompyuterda (faster-whisper), audio hech qayerga yuborilmaydi
    # "openai" -- OpenAI transkripsiya API'si (OPENAI_STT_MODEL); xato bersa lokal modelga qaytadi
    GROUP_STT_PROVIDER: str = "local"
    # O'lchov (2026-10-12, 20 ta ochiq o'zbekcha yozuv, so'z xatosi): lokal model 18%,
    # gpt-4o-transcribe 35%, whisper-1 98% (o'zbekchani qozoq/arman harflarida yozadi) --
    # o'zbekcha nutq uchun OpenAI tanlansa `gpt-4o-transcribe` qo'ying, `whisper-1` yaramaydi.
    OPENAI_STT_MODEL: str = "whisper-1"
    # Bo'sh = tilni model o'zi aniqlaydi. "uz" ni API qabul qilmaydi (400 xatosi, 2026-10-12).
    OPENAI_STT_LANGUAGE: str = ""
    # Bo'sh = ~/.cache/faster-whisper-ct2/islomov--rubaistt_v2_medium (CTranslate2 papkasi)
    GROUP_STT_MODEL: str = ""
    GROUP_STT_DEVICE: str = "auto"  # auto (videokarta, bo'lmasa protsessor) | cuda | cpu
    # Videokartada ishlash uchun kerakli cuBLAS/cuDNN DLL'lari turgan papka (Windows)
    GROUP_STT_CUDA_DLL_DIR: str = "./cuda_runtime"
    GROUP_STT_LANGUAGE: str = "uz"
    # Ishonchli ravishda ruscha deb aniqlangan nutq uchun ko'p tilli model (bo'sh = hamma nutq
    # o'zbekcha model orqali; u ruschani lotin harflarida transliteratsiya qilib yozadi)
    GROUP_STT_RU_MODEL: str = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    # Bundan uzun yozuvlar matnga o'girilmaydi (uzun video butun navbatni band qilmasin)
    GROUP_STT_MAX_DURATION_SECONDS: int = 600
    # Model o'z xulosasiga (shikoyat / ijobiy fikr / tovar yo'q) shu foizdan kam ishonsa,
    # xabar hech qayerga tashlanmaydi. Balandroq = kamroq, lekin aniqroq xabar tushadi.
    GROUP_AI_MIN_CONFIDENCE: int = 80

    # Kunlik statistika hisoboti (2026-10-12, `app/daily_report.py`): har kuni yarim tunda
    # (REPORT_TIMEZONE bo'yicha 00:00) o'tgan kun uchun HTML fayl yoziladi:
    # DAILY_REPORT_DIR/stats_YYYY-MM-DD.html. DAILY_REPORT_CHAT_ID berilsa shu chatga
    # (ichki forum-guruh bo'lsa DAILY_REPORT_TOPIC_ID topigiga) fayl qilib ham yuboriladi.
    DAILY_REPORT_ENABLED: bool = True
    DAILY_REPORT_DIR: str = "./reports"
    DAILY_REPORT_CHAT_ID: int | None = None
    DAILY_REPORT_TOPIC_ID: int | None = None
    REPORT_TIMEZONE: str = "Asia/Tashkent"
    # `message_logs` yozuvlari shuncha kundan keyin o'chiriladi (0 = o'chirilmaydi)
    MESSAGE_LOG_RETENTION_DAYS: int = 180

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
