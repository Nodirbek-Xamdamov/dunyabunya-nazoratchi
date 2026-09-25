from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_GROUP_ID: int
    CEO_TELEGRAM_USER_ID: int = 0
    CEO_TELEGRAM_USERNAME: str = ""

    # Odoo
    ODOO_URL: str
    ODOO_DB: str
    # .env'da ODOO_USERNAME deb ham yozilishi mumkin (telegram-odoo-bot bilan bir xil nom)
    ODOO_LOGIN: str = Field(validation_alias=AliasChoices("ODOO_LOGIN", "ODOO_USERNAME"))
    ODOO_API_KEY: str
    ODOO_TIMEOUT_SECONDS: int = 30

    # PostgreSQL
    DATABASE_URL: str

    # Encryption key (Fernet)
    ENCRYPTION_KEY: str

    # Claude — chuqur tahlil
    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL: str = "claude-opus-5"
    ANTHROPIC_WORKSPACE_ID: str = ""

    # OpenAI — arzon/tezkor triage+xulosa (chat), Whisper (STT), TTS
    OPENAI_API_KEY: str = ""
    OPENAI_CHAT_MODEL: str = "gpt-5.4-mini"
    OPENAI_WHISPER_MODEL: str = "gpt-4o-transcribe"
    OPENAI_TTS_MODEL: str = "tts-1"
    OPENAI_TTS_VOICE: str = "alloy"

    # App
    APP_PORT: int = 8002
    LOG_LEVEL: str = "INFO"
    STORAGE_DIR: str = "./storage"
    PUBLIC_BASE_URL: str = "http://localhost:8002"

    # Message filtering thresholds
    OTHER_GROUP_MIN_DEPARTMENT_SCORE: float = 0.3
    OTHER_GROUP_MIN_TASK_CONFIDENCE: float = 0.70

    # Hisobot havolalari qancha soatdan keyin muddati o'tgan hisoblanadi (P0-2)
    REPORT_LINK_TTL_HOURS: int = 48

    # Muddatsiz vazifa nechchi kundan keyin "eskirgan" deb CEOga eslatiladi (P1-6)
    TASK_REMINDER_STALE_DAYS: int = 3


    @property
    def ceo_usernames(self) -> set[str]:
        """CEO_TELEGRAM_USERNAME vergul bilan bir nechta bo'lishi mumkin (2026-09-25:
        ikki CEO) — "@"siz, kichik harfda."""
        return {u.strip().lstrip("@").casefold()
                for u in self.CEO_TELEGRAM_USERNAME.split(",") if u.strip().lstrip("@")}


settings = Settings()
