# app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Mahsulot katalogi: sayt API'si (kalitsiz). Ro'yxat har
    # products_refresh_seconds'da xotiraga qayta yuklanadi (app/catalog.py).
    products_api_url: str = "https://demo.dunyabunya.uz/auth-api/api/products"
    products_refresh_seconds: float = 300.0
    # Mijozga beriladigan mahsulot havolasi: {site_base_url}/{uz|ru}/catalog/{id}
    site_base_url: str = "https://demo.dunyabunya.uz"

    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-4o"
    openai_timeout_seconds: float = 20.0
    max_response_tokens: int = 500

    # System promptdagi {KOMPANIYA_NOMI}/{TELEFON}/{MANZIL}/{ISH_VAQTI}/{OPERATOR_TELEGRAM} o'rnini bosadi
    company_name: str = "dunyabunya"
    company_phone: str = ""
    company_address: str = ""
    company_work_hours: str = ""
    # Mijoz "operatorga ulang" desa, Telegram orqali yuborish muvaffaqiyatsiz
    # bo'lgan holdagi zaxira sifatida botning aytadigan username (@ belgisiz).
    operator_telegram: str = ""

    # Operator-relay: mijoz so'rovi shu Telegram bot orqali operatorga
    # yuboriladi (app/telegram_operator.py). operator_telegram_chat_id —
    # operatorning Telegram chat_id'si (getUpdates orqali /start xabaridan
    # olinadi), ikkalasi ham bo'sh bo'lsa relay o'chiq bo'ladi.
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    operator_telegram_chat_id: int = 0

    # Modelga yuboriladigan oxirgi N ta xabar (butun tarix emas)
    history_max_messages: int = 10

    # Suiiste'moldan himoya — /api/chatbot/ask uchun, conversation_id bo'yicha
    rate_limit_per_minute: int = 10
    session_message_limit: int = 50

    # Frontend uchun ruxsat etilgan originlar, vergul bilan ajratilgan
    # (masalan: "https://dunyabunya.uz,https://www.dunyabunya.uz"). "*" — hamma uchun ochiq.
    cors_origins: str = "*"

    # .NET AuthApi'dagi HAQIQIY (uzoq, X-Api-Key bilan himoyalangan) chat tarixi
    # API'si — TEST uchun: biz "poll" qilib, javobsiz "user" xabarlariga javob
    # yozamiz. Bo'sh bo'lsa poller ishga tushmaydi (o'chiq holat).
    remote_chat_api_base_url: str = ""  # masalan: https://demo.dunyabunya.uz/auth-api
    remote_chat_api_key: str = ""  # X-Api-Key header qiymati

    # Vergul bilan ajratilgan conversationId ro'yxati — TEST uchun qo'lda beriladi
    # (haqiqiy "yangi suhbatni qanday topamiz" savoli hali hal qilinmagan).
    remote_chat_poll_conversation_ids: str = ""
    remote_chat_poll_interval_seconds: float = 5.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def remote_chat_poll_conversation_ids_list(self) -> list[str]:
        return [c.strip() for c in self.remote_chat_poll_conversation_ids.split(",") if c.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()