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

    # ------------------------------------------------------------------------------------
    # Mijoz guruhlari monitoringi (2026-10-13). Bot mijoz guruhlarida FAQAT o'qiydi: hamma
    # xabar SQLite bazaga yoziladi (`app/monitor_db.py`), fon vazifa ularni tasniflaydi
    # (`app/monitor_classifier.py`), "5 daqiqa qoidasi" tekshiriladi (`app/monitor_sla.py`),
    # har kuni 00:00 da Excel hisobot yuboriladi (`app/monitor_report.py`).
    # ------------------------------------------------------------------------------------
    MONITOR_ENABLED: bool = True
    # Monitoring bazasi (SQLite fayli)
    DB_PATH: str = "./data/monitor.db"
    # Kuzatilmaydigan guruhlar (vergul bilan ajratilgan chat_id'lar, masalan call-center guruhi).
    # Hisobot guruhining o'zi (REPORT_CHAT_ID) hech qachon kuzatilmaydi.
    GROUP_MONITOR_EXCLUDED_GROUP_IDS: str = ""
    # "Kun" chegarasi va 00:00 dagi hisobot shu vaqt mintaqasi bo'yicha
    TIMEZONE: str = Field(default="Asia/Tashkent", validation_alias=AliasChoices("TIMEZONE", "REPORT_TIMEZONE"))

    # --- Tasniflash va xulosa modeli (`app/llm_client.py`) ---
    # GROUP_AI_PROVIDER: "ollama" (shu kompyuterdagi lokal model, kalit kerak emas) yoki "openai".
    GROUP_AI_PROVIDER: str = "openai"
    OPENAI_API_KEY: str | None = None
    GROUP_AI_MODEL: str = "gpt-5.4-mini"
    # Lokal Gemma (Ollama) manzili; `.env`da GEMMA_API_URL deb ham yozish mumkin
    OLLAMA_URL: str = Field(
        default="http://127.0.0.1:11434", validation_alias=AliasChoices("OLLAMA_URL", "GEMMA_API_URL")
    )
    # Lokal model oxirgi so'rovdan keyin qancha vaqt videokarta xotirasida turadi ("-1" = doim)
    GROUP_AI_KEEP_ALIVE: str = "30m"
    # Modelga bir vaqtda nechta so'rov ketadi (qolganlari navbat kutadi)
    GROUP_AI_MAX_CONCURRENCY: int = 2
    # Bitta so'rovda bir guruhning nechta xabari tasniflanadi va ularga nechta oldingi xabar kontekst bo'ladi
    CLASSIFY_BATCH_SIZE: int = 10
    CLASSIFY_CONTEXT_MESSAGES: int = 8
    # Model shuncha marta yaroqli javob bermagan xabar "failed" bo'ladi va boshqa urinilmaydi
    CLASSIFY_MAX_ATTEMPTS: int = 5

    # --- 5 daqiqa qoidasi ---
    # Guruh admini unvonida (custom title) shu so'zlardan biri bo'lsa u shu guruhning menejeri
    # (katta-kichik harf farqsiz, so'zning bir qismi ham yetadi)
    MANAGER_TITLES: str = Field(
        default="meneger,menejer,manager,menedj,менедж,менеж",
        validation_alias=AliasChoices("MANAGER_TITLES", "GROUP_MANAGER_TITLE_KEYWORDS"),
    )
    # Menejer shuncha daqiqa ichida javob yozmasa so'rov "javobsiz" hisoblanadi
    SLA_MINUTES: int = Field(default=5, validation_alias=AliasChoices("SLA_MINUTES", "STAFF_REPLY_TIMEOUT_MINUTES"))
    # Ish vaqti, masalan "09:00-19:00": undan tashqaridagi so'rovlar tekshirilmaydi. Bo'sh = 24/7.
    SLA_WORK_HOURS: str = ""

    # --- Kunlik Excel hisobot ---
    # Hisobot yuboriladigan guruh. Forum-guruh bo'lsa fayl "General" topigiga tushadi;
    # boshqa topik kerak bo'lsa REPORT_THREAD_ID ga o'sha topik ID'sini yozing.
    REPORT_CHAT_ID: int | None = Field(
        default=None, validation_alias=AliasChoices("REPORT_CHAT_ID", "FEEDBACK_HUB_GROUP_ID", "CENTRAL_FORUM_CHAT_ID")
    )
    REPORT_THREAD_ID: int | None = None
    # Hisobot har kuni shu vaqtda (TIMEZONE bo'yicha) yuboriladi va undan oldingi 24 soatni qamraydi.
    # "00:00" -- o'tgan kalendar kuni. Sinov davrida "11:00": kechagi 11:00 dan bugungi 11:00 gacha.
    REPORT_TIME: str = "00:00"
    REPORTS_DIR: str = Field(default="./reports", validation_alias=AliasChoices("REPORTS_DIR", "DAILY_REPORT_DIR"))
    # Hisobotni yuborib bo'lmasa xabar beriladigan chat (bo'sh = ODOO_ALERT_CHAT_ID)
    ADMIN_CHAT_ID: int | None = None
    # /test_report buyrug'ini ishlata oladiganlar (vergul bilan Telegram ID'lar). Bundan tashqari
    # ADMIN_CHAT_ID egasi va hisobot guruhining adminlari ham ishlata oladi.
    ADMIN_USER_IDS: str = ""
    # Monitoring bazasining kunlik zaxira nusxalari (monitor_YYYY-MM-DD.db), oxirgi BACKUP_KEEP tasi saqlanadi
    BACKUP_DIR: str = "./backups"
    BACKUP_KEEP: int = 14

    # --- Ovozli xabar / audio / video (ovozi) -> matn ---
    # Yoqilgan bo'lsa tasniflovchi matni yo'q ovozli xabarni OpenAI transkripsiya API'si orqali
    # matnga aylantiradi (`OPENAI_API_KEY` kerak; foydalanuvchi qarori, 2026-10-13) va shu matn
    # bo'yicha tasniflaydi. O'chiq bo'lsa ovozli xabarlar faqat media turi bilan saqlanadi.
    GROUP_STT_ENABLED: bool = False
    # Quyidagi ikkitasi faqat `app/stt.py`dagi lokal model uchun (monitoring uni ishlatmaydi)
    GROUP_STT_PROVIDER: str = "local"
    # O'zbekcha uchun `gpt-4o-transcribe`: `whisper-1` o'zbekchani tanimaydi (2026-10-12 o'lchovi:
    # so'z xatosi whisper-1 98%, gpt-4o-transcribe 35%, lokal model 18%).
    OPENAI_STT_MODEL: str = "gpt-4o-transcribe"
    # Bo'sh = tilni model o'zi aniqlaydi. "uz" ni API qabul qilmaydi (400 xatosi, 2026-10-12).
    OPENAI_STT_LANGUAGE: str = ""
    # Bo'sh = ~/.cache/faster-whisper-ct2/islomov--rubaistt_v2_medium (CTranslate2 papkasi)
    GROUP_STT_MODEL: str = ""
    GROUP_STT_DEVICE: str = "auto"  # auto (videokarta, bo'lmasa protsessor) | cuda | cpu
    # Videokartada ishlash uchun kerakli cuBLAS/cuDNN DLL'lari turgan papka (Windows)
    GROUP_STT_CUDA_DLL_DIR: str = "./cuda_runtime"
    GROUP_STT_LANGUAGE: str = "uz"
    GROUP_STT_RU_MODEL: str = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    GROUP_STT_MAX_DURATION_SECONDS: int = 600

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
