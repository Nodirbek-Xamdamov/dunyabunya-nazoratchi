"""
SQLAlchemy v2 ORM modellari (PostgreSQL/SQLite moslashtirilgan).
"""

import enum
from datetime import date as date_type
from datetime import datetime

from sqlalchemy import (
    JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OrderStatus(str, enum.Enum):
    PENDING = "pending"   # Botga kirishi kutilmoqda
    SENT = "sent"         # Fayl va ma'lumot bot orqali berildi
    EXPIRED = "expired"   # Vaqti o'tdi


class ConfirmationStatus(str, enum.Enum):
    PENDING = "pending"                     # Mijoz javob berishi kutilmoqda
    CONFIRMED = "confirmed"                 # Mijoz buyurtmani tasdiqladi [Tasdiqlash]
    REJECTED = "rejected"                   # Mijoz buyurtmani rad etdi [Rad etish]
    CHANGES_REQUESTED = "changes_requested" # Mijoz o'zgartirish kiritishni so'radi [O'zgartirish]


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Odoo'dan kelgan hujjat/buyurtma raqami (masalan: SO001 yoki h.k.)
    doc_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)

    # Odoo'dan kelgan asl username
    raw_username: Mapped[str] = mapped_column(String(128), nullable=False)

    # normalize_username() orqali tozalangan username
    normalized_username: Mapped[str] = mapped_column(String(128), index=True, nullable=False)

    # Odoo'dan kelgan menejer ismi
    manager_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Odoo'dan kelgan xususiy matn xabari (to'g'ridan-to'g'ri ko'rsatiladi)
    custom_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Buyurtma tafsilotlari JSON string ko'rinishida saqlanadi
    order_details: Mapped[str] = mapped_column(Text, nullable=False)

    # PDF fayl diskdagi manzili
    pdf_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Foydalanuvchi /start bosgandan keyin to'ldiriladi
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # PostgreSQL bilan ziddiyat bo'lmasligi uchun String(32) saqlanadi
    status: Mapped[str] = mapped_column(
        String(32),
        default=OrderStatus.PENDING.value,
        nullable=False,
    )

    # Mijozning buyurtmani tasdiqlash holati (pending, confirmed, rejected, changes_requested)
    confirmation_status: Mapped[str] = mapped_column(
        String(32),
        default=ConfirmationStatus.PENDING.value,
        nullable=False,
    )

    # Mijoz tomonidan qoldirilgan izoh (matn, audio transkripsiya va h.k.)
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Mijoz yuborgan fayllar yo'li JSON formatda (masalan: ["data/feedback_files/photo_1.jpg", ...])
    feedback_files: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Foydalanuvchiga ushbu xabar yetib borganini/yuborilganini bildiradi
    notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Odoo /api/v1/chat-ids orqali ushbu yozuvni o'ziga sinxronlab olganini bildiradi
    synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Guruhga yo'naltirilgan buyurtmalar uchun (individual oqimga ta'sir qilmaydi) ---

    # Agar buyurtma bitta foydalanuvchi emas, balki Telegram guruhiga yuborilgan bo'lsa.
    # Bunday holatda `chat_id` -- shu guruh ichida ushbu buyurtmani TASDIQLASHGA vakolatli
    # yagona shaxsning chat_id'si (faqat shu odam Tasdiqlash/Rad etish/O'zgartirish
    # tugmalarini bosa oladi -- app/bot/handlers/orders.py shuni tekshiradi).
    group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    def __repr__(self) -> str:
        return f"<Order id={self.id} doc_id={self.doc_id} username={self.normalized_username} status={self.status} confirmation={self.confirmation_status}>"


class TelegramUser(Base):
    """Botga /start bosgan har bir foydalanuvchi haqida buyurtmadan mustaqil identifikatsiya.

    Guruh a'zoligini (GroupMembership) shu jadval bilan bog'lash uchun kerak — chunki
    bitta foydalanuvchi bir nechta buyurtmaga ega bo'lishi yoki umuman buyurtmasiz ham
    guruh a'zosi bo'lishi mumkin.
    """

    __tablename__ = "telegram_users"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    normalized_username: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)

    # Foydalanuvchi botda tanlagan til: "uz" | "ru"
    language: Mapped[str] = mapped_column(String(8), default="uz", nullable=False)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<TelegramUser chat_id={self.chat_id} username={self.normalized_username}>"


class GroupMembership(Base):
    """Telethon userbot skanerlagan (foydalanuvchi, guruh) a'zolik juftliklari."""

    __tablename__ = "group_memberships"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("telegram_users.chat_id"), index=True, nullable=False)
    group_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    group_title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Odoo /api/v1/group-memberships orqali ushbu yozuvni o'ziga sinxronlab olganini bildiradi
    synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:
        return f"<GroupMembership chat_id={self.chat_id} group_id={self.group_id}>"


class BotGroup(Base):
    """Rasmiy bot (aiogram) a'zo/admin bo'lgan guruhlar -- `my_chat_member` eventi orqali
    real-vaqtda yoziladi (app.bot.handlers.chat_events). Telethon userbotning har 15
    daqiqalik to'liq skanerlashidan mustaqil, alohida ro'yxat -- faqat "bot qaysi
    guruhlarda bor" degan ma'lumotni tez aniqlash uchun.
    """

    __tablename__ = "bot_groups"

    group_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<BotGroup group_id={self.group_id} title={self.title} is_admin={self.is_admin}>"


class OdooBalanceSnapshot(Base):
    """Odoo tomonidan push qilingan (XML-RPC endi ishlamagani uchun) mijoz balans
    ma'lumotining eng so'nggi nusxasi -- Balance menyusi shu yerdan o'qiydi, Odoo'ga
    to'g'ridan-to'g'ri so'rov yubormaydi. Har safar Odoo /api/v1/odoo/balance/sync'ga
    yangi ma'lumot yuborganda shu yozuv (chat_id bo'yicha) yangilanadi.
    """

    __tablename__ = "odoo_balance_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    partner_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    total_balance_uzs: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_balance_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Har bir obyekt: {"id": int, "name": str, "balance_uzs": float, "balance_usd": float}
    objects: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<OdooBalanceSnapshot chat_id={self.chat_id} partner_name={self.partner_name}>"


class SupportForwardMessage(Base):
    """
    Mijoz botga yoki premium akkauntga yozgan xabarlarni @support_dunyabunya'ga
    uzatish uchun navbat. Bot (aiogram) alohida jarayon bo'lgani uchun Telethon
    userbotga to'g'ridan-to'g'ri murojaat qila olmaydi -- shu sabab shu jadval
    orqali (userbot.poll_loop davriy o'qib yuboradi) uzatiladi.
    """

    __tablename__ = "support_forward_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # "bot" | "userbot"
    sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<SupportForwardMessage id={self.id} username={self.username} sent={self.sent}>"


class DeliveryConfirmation(Base):
    """
    Haydovchi alohida "taklif-bot" loyihasida (~/demo/taklif-bot) tijorat taklifi
    hujjatining yetkazib berilgan rasmini yuborgach, o'sha bot shu yerga (POST
    /api/v1/delivery-confirmations) doc_id + menejer ismi + rasmni jo'natadi. Bu yerda:
    (1) rasm diskka saqlanadi, (2) shu doc_id bo'yicha mijozning chat_id'si (mavjud
    Order yozuvlaridan) topilib, "buyurtmangiz yetib bordi" xabari yuboriladi, (3) Odoo
    tomoni GET /api/v1/delivery-confirmations orqali shu yozuvni o'zi SO'RAB (poll qilib)
    oladi va /mark-synced bilan belgilaydi -- xuddi chat-ids/group-memberships bilan bir
    xil naqsh (Odoo'ga to'g'ridan-to'g'ri yozilmaydi, Odoo'ning o'zi GET qilib oladi).
    """

    __tablename__ = "delivery_confirmations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # taklif-botdan kelgan tijorat taklifi/buyurtma raqami (masalan "S56617")
    doc_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)

    # taklif-bot QR/VLM orqali aniqlagan menejer ismi (topilmasa NULL)
    manager_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Yetkazib berish rasmi diskdagi manzili
    photo_path: Mapped[str] = mapped_column(String(512), nullable=False)

    # Shu doc_id bo'yicha topilgan mijozning chat_id'si (topilmasa NULL)
    customer_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Mijozga "buyurtmangiz yetib bordi" xabari muvaffaqiyatli yuborilganini bildiradi
    customer_notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # taklif-bot tomonidan yuklagan xodimning F.I.Sh (audit uchun, ixtiyoriy)
    uploaded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Odoo /api/v1/delivery-confirmations orqali ushbu yozuvni o'ziga sinxronlab olganini bildiradi
    synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:
        return f"<DeliveryConfirmation id={self.id} doc_id={self.doc_id} synced={self.synced}>"


class ObjectLocation(Base):
    """
    "Buyurtma berish" oqimida mijoz (Odoo'da MAVJUD, partner_id orqali tanilgan) uchun
    obyekt (qurilish joyi) tanlanganda YOKI yangi obyekt yaratilganda, shu obyektning GPS
    lokatsiyasi shu yerga yoziladi (2026-08-02). Odoo'ga to'g'ridan-to'g'ri yozilmaydi --
    Odoo GET /api/v1/object-locations orqali o'zi so'rab oladi va /mark-synced bilan
    belgilaydi, xuddi chat-ids/delivery-confirmations bilan bir xil poll+mark-synced naqsh.
    `object_id` -- Odoo'dagi mavjud obyekt (child res.partner, type="delivery") ID'si;
    `is_new_object=True` bo'lsa bu obyekt Odoo'da hali yo'q (mijoz nomini qo'lda kiritgan),
    `object_id` NULL bo'ladi -- Odoo tarafi bu holda yangi obyekt yozuvini o'zi yaratadi.
    """

    __tablename__ = "object_locations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Odoo'dagi mijozning asosiy partner ID'si (res.partner.id)
    partner_id: Mapped[int] = mapped_column(nullable=False, index=True)

    # Odoo'dagi mavjud obyekt (child partner) ID'si -- yangi obyekt bo'lsa NULL
    object_id: Mapped[int | None] = mapped_column(nullable=True)

    object_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # True -- bu obyekt Odoo'da hali mavjud emas (mijoz nomini qo'lda kiritgan)
    is_new_object: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_username: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Mijozning ismi/telefoni (2026-08-02) -- Odoo'da partner_id orqali baribir bor,
    # lekin yangi obyekt (is_new_object=True) uchun Odoo xodimi obyektni qo'lda
    # yaratayotganda partner_id'ni qayta qidirmasdan kimga tegishli ekanini ko'rishi uchun.
    partner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    partner_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # MAVJUD obyekt (object_id bor) uchun: shu lokatsiya Odoo'ning o'z partner_latitude/
    # partner_longitude maydoniga to'g'ridan-to'g'ri muvaffaqiyatli yozilganini bildiradi
    # (2026-08-02). Yangi obyekt uchun doim False qoladi (yozadigan joy yo'q). GET
    # /api/v1/object-locations shu maydonni tekshiradi: True bo'lsa Odoo'da allaqachon
    # bor, qayta ko'rsatilmaydi; False bo'lsa (yangi obyekt YOKI to'g'ridan-to'g'ri yozish
    # muvaffaqiyatsiz bo'lgan holat) ma'lumot yo'qolmasligi uchun GET orqali ko'rsatiladi.
    odoo_synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Odoo /api/v1/object-locations orqali ushbu yozuvni o'ziga sinxronlab (ko'rib
    # chiqib) olganini bildiradi -- odoo_synced'dan farqli, bu "staff ko'rdi" degani.
    synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:
        return f"<ObjectLocation id={self.id} object_name={self.object_name} is_new_object={self.is_new_object}>"


class ObjectEditLog(Base):
    """"Ma'lumotlarni tahrirlash" oqimida (app.bot.handlers.edit_info, 2026-08-05) mijoz
    o'z obyektining telefon raqami yoki lokatsiyasini o'zgartirganda, o'zgarish Odoo'ga
    TO'G'RIDAN-TO'G'RI yoziladi (res.partner.write, odoo_client.update_object_contact) --
    Odoo o'zgarishni darhol o'z bazasida ko'radi, hech qanday alohida sinxronlash SHART
    EMAS. Bu jadval FAQAT Odoo XODIMLARI "qaysi obyekt qachon o'zgardi" degan savolga
    javob topishi uchun LOG sifatida qo'shimcha saqlanadi -- GET /api/v1/object-edits
    orqali so'rab olinadi, xuddi chat-ids/object-locations bilan bir xil poll+mark-
    synced naqsh (`synced` shu yerda ham "Odoo xodimi ko'rib chiqdi" degani, Odoo'ning
    o'z ma'lumotining o'zi allaqachon yangilangan -- bu maydon FAQAT log ko'rilganini
    bildiradi)."""

    __tablename__ = "object_edit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    object_id: Mapped[int] = mapped_column(nullable=False, index=True)
    object_name: Mapped[str] = mapped_column(String(255), nullable=False)
    partner_id: Mapped[int | None] = mapped_column(nullable=True, index=True)

    # "phone" yoki "location" -- qaysi maydon o'zgartirilgani
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    new_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    changed_by_username: Mapped[str | None] = mapped_column(String(128), nullable=True)

    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Odoo /api/v1/object-edits orqali shu logni ko'rib chiqqanini bildiradi.
    synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:
        return f"<ObjectEditLog id={self.id} object_id={self.object_id} field={self.field}>"


class GroupInquiry(Base):
    """Guruhda kimdir botni (@BOT_USERNAME) mention qilib yozgan xabar (2026-08-03) --
    faqat yuboruvchi Odoo'da (res.partner.x_telegram_id bo'yicha) TOPILGAN holatda
    yoziladi (`app.bot.handlers.group_mentions`, POST /api/v1/bot/group-inquiry orqali
    -- topilmasa hech narsa saqlanmaydi, guruhga darhol "ro'yxatdan o'ting" javobi
    beriladi). Odoo GET /api/v1/group-inquiries orqali o'zi so'rab oladi va
    /mark-synced bilan belgilaydi -- xuddi chat-ids/group-memberships bilan bir xil
    poll+mark-synced naqsh (to'g'ridan-to'g'ri crm.lead yozilmaydi)."""

    __tablename__ = "group_inquiries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    chat_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    group_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    group_title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Odoo'dagi topilgan mijozning res.partner.id/name'i
    partner_id: Mapped[int] = mapped_column(nullable=False, index=True)
    partner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    message_text: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Odoo /api/v1/group-inquiries orqali ushbu yozuvni o'ziga sinxronlab olganini bildiradi
    synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:
        return f"<GroupInquiry id={self.id} chat_id={self.chat_id} partner_id={self.partner_id}>"


class OrderMediaAttachment(Base):
    """"Buyurtma berish" oqimida mijoz buyurtma tafsiloti sifatida yuborgan rasm/video/
    ovozli xabar/fayl(hujjat) (2026-08-03) -- bot ularni ORDER_MEDIA_DIR'ga yuklab oladi,
    fastapi shu fayllarni o'qib Odoo'ga HAQIQIY ir.attachment sifatida crm.lead'ga
    biriktiradi (call-center guruhiga forward qilish o'rniga -- bu oqimda guruh butunlay
    ishlatilmaydi endi; ochiq URL/description-havola yondashuvi ham shu kunning o'zida
    sinab ko'rilib, ir.attachment foydasiga almashtirilgan). Bu jadval -- Odoo'ga
    yuborilgan-yuborilmaganidan qat'i nazar -- mahalliy zaxira nusxa. Bitta Telegram
    xabarida bir vaqtning o'zida faqat bitta media turi bo'lishi mumkin, shu sabab
    amalda har doim shu to'rt ustundan faqat bittasi to'ldiriladi."""

    __tablename__ = "order_media_attachments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    chat_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)

    # Muvaffaqiyatli yozilgan crm.lead ID'si -- Odoo'ga yozish muvaffaqiyatsiz bo'lsa NULL
    # (baribir saqlanadi, mediani yo'qotmaslik uchun -- app/bot/handlers/menu.py'dagi
    # generic_error javobi bilan birga qo'lda tekshirish uchun qoladi).
    lead_id: Mapped[int | None] = mapped_column(nullable=True, index=True)

    photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    video_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Telegram "fayl" (document) sifatida yuborilgan ixtiyoriy formatdagi hujjat (masalan
    # PDF/DOCX/XLSX) -- asl kengaytmasi saqlanadi (2026-08-03), asl fayl nomi emas.
    document_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<OrderMediaAttachment id={self.id} chat_id={self.chat_id} lead_id={self.lead_id}>"


class StatusUpdateLog(Base):
    """"Holat yangilanishi" (STATUS_UPDATE_SUBJECT) push'lari uchun takror-yuborishni
    oldini olish (2026-08-03) -- live tekshirilgan: Odoo bitta haqiqiy o'zgarish uchun
    AYNAN BIR XIL xabarni bir necha marta (masalan S56653 uchun "bekor qilindi" matni
    3 marta ketma-ket, ~1 daqiqa ichida) yuborishi mumkin -- Odoo tarafidagi biror
    avtomatizatsiya/retry sababli, bizning nazoratimizdan tashqarida. Har bir (doc_id,
    message, target) uchtaligi FAQAT bir marta yozib qo'yiladi -- xuddi shu uchtalik
    qayta kelsa (`handle_status_update_notification`dagi tekshiruv), mijozga/guruhga
    qayta Telegram xabari YUBORILMAYDI (lekin webhook baribir muvaffaqiyatli javob
    qaytaradi -- Odoo qayta urinib ko'rmasin). `target_chat_id`/`target_group_id`dan
    faqat bittasi to'ldiriladi (individual/guruh yo'nalishiga qarab) -- ikkalasi ham
    NULL bo'lgan holat yo'q (chat_id topilmagan holatlar umuman yozilmaydi, chunki
    yuborilmagan)."""

    __tablename__ = "status_update_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    target_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "doc_id", "message", "target_chat_id", "target_group_id", name="uq_status_update_target"
        ),
    )

    def __repr__(self) -> str:
        return f"<StatusUpdateLog id={self.id} doc_id={self.doc_id}>"


class HubTopic(Base):
    """Ichki (xodimlar) forum-guruhidagi topiklar (2026-10-07) -- Bot API topiklar
    ro'yxatini bermaydi, shu sabab bot ularni shu guruhdagi xabarlardan o'zi "o'rganib"
    shu yerga yozadi (`app.bot.handlers.group_monitor.learn_hub_topic`). Shikoyat/ijobiy
    sharh/tovar yo'q xabarlari qaysi topikka tashlanishi topik NOMI bo'yicha aniqlanadi."""

    __tablename__ = "hub_topics"

    thread_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<HubTopic thread_id={self.thread_id} name={self.name}>"


class StaffMember(Base):
    """Ichki (xodimlar) guruhi a'zosi ekani tasdiqlangan dunyabunya xodimlari (2026-10-07).
    Mijoz guruhda xodimni @username orqali mention qilganda username -> chat_id topish
    uchun kerak (Bot API username bo'yicha foydalanuvchini qidirib bermaydi)."""

    __tablename__ = "staff_members"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    normalized_username: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<StaffMember chat_id={self.chat_id} username={self.normalized_username}>"


class StaffReplyWait(Base):
    """Mijoz guruhda xodimga murojaat qilgan, xodim hali javob yozmagan holat (2026-10-07).
    Har bir (guruh, xodim) juftligi uchun ko'pi bilan BITTA yozuv. `due_at` o'tib ketsa
    xodimning shaxsiy chatiga eslatma yuboriladi (`notified_at` belgilanadi); xodim shu
    guruhga javob yozsa yozuv o'chiriladi. Xodim "N daqiqada javob beraman" desa
    `due_at` shu N daqiqaga suriladi va `promised=True` bo'ladi."""

    __tablename__ = "staff_reply_waits"
    __table_args__ = (UniqueConstraint("group_id", "staff_chat_id", name="uq_staff_reply_wait_group_staff"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    group_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    group_title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    staff_chat_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    staff_username: Mapped[str | None] = mapped_column(String(128), nullable=True)

    customer_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Mijozning xodimga murojaat qilgan xabari
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_link: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # True -- mijoz aynan shu xodimga murojaat qilgan (reply / @username); False -- xabar hech
    # kimga qaratilmagan, xodim guruh MENEJERI (admin unvoni bo'yicha) sifatida mas'ul.
    addressed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    wait_minutes: Mapped[int] = mapped_column(nullable=False)
    promised: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<StaffReplyWait id={self.id} group_id={self.group_id} staff_chat_id={self.staff_chat_id}>"


class Group(Base):
    """Bot kuzatadigan mijoz guruhlari (2026-10-12). Guruhdan birinchi xabar kelganda yozuv
    o'zi paydo bo'ladi (`app.group_registry.ensure_group`). `is_active=False` qilingan guruh
    kuzatilmaydi: xabarlari tahlil qilinmaydi, hech qayerga tashlanmaydi (ichki guruhda
    `/guruh_ochir` / `/guruh_yoq`)."""

    __tablename__ = "monitored_groups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<Group group_id={self.group_id} title={self.title} is_active={self.is_active}>"


class Manager(Base):
    """Guruhga biriktirilgan menejerlar (2026-10-12). Ikki manbadan to'ladi:
    `source="title"` -- guruh adminining unvonida "Menejer" so'zi bor va u ichki guruh a'zosi
    (bot o'zi aniqlab, shu jadval bilan tenglashtirib turadi); `source="manual"` -- ichki
    guruhda `/menejer_qosh` buyrug'i bilan qo'lda yozilgan. Javobsiz qolgan savol bo'yicha
    guruhga yoziladigan ogohlantirishda shu yerdagi hamma menejer belgilanadi; ularning
    guruhdagi xabari xodim javobi hisoblanadi."""

    __tablename__ = "group_managers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    # Qo'lda yozilgan menejerning Telegram ID'si u guruhda birinchi marta yozmaguncha noma'lum
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # "@"siz, kichik harflarda (`normalize_username`)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<Manager group_id={self.group_id} username={self.username} source={self.source}>"


class MessageLog(Base):
    """Mijozning KOMPANIYAGA OID deb topilgan har bir guruh xabari (2026-10-12) -- kunlik
    hisobot shu jadvaldan yig'iladi (`app/daily_report.py`). Kompaniyaga aloqasiz oddiy
    suhbat bu yerga YOZILMAYDI: uning faqat soni `daily_stats`da sanaladi. Xodim xabarlari
    ham yozilmaydi."""

    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Yozilgan matn; ovozli xabar/video bo'lsa -- nutqning matni, rasm bo'lsa -- model tavsifi
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # text | photo | voice | audio | video | video_note
    media_type: Mapped[str] = mapped_column(String(16), default="text", server_default="text", nullable=False)

    # `app.group_ai.CATEGORIES`dagi nomlardan biri ("Dastavka va Logistika", ..., "Boshqa")
    category: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # positive | negative | neutral  (negative = shikoyat, positive = ijobiy fikr)
    sentiment: Mapped[str] = mapped_column(String(16), default="neutral", server_default="neutral", nullable=False)
    is_company_related: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    # Xabar ichki guruh topigiga tashlanganmi
    forwarded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)

    def __repr__(self) -> str:
        return f"<MessageLog id={self.id} group_id={self.group_id} category={self.category}>"


class DailyStats(Base):
    """Kun bo'yicha sanoqlar (2026-10-12): har bir (sana, nom) juftligi uchun bitta yozuv.
    Toifalar soni yarim tundagi hisobot paytida `message_logs`dan hisoblab yoziladi; matni
    saqlanmaydigan narsalar (kompaniyaga aloqasiz xabarlar, xodim xabarlari, "tovar yo'q"
    signallari, javobsiz qolgan savollar) esa xabar kelgan zahoti shu yerda sanab boriladi
    (`app/group_stats.py`)."""

    __tablename__ = "daily_stats"
    __table_args__ = (UniqueConstraint("date", "category_name", name="uq_daily_stats_date_category"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # `date_type`: sinf ichida `date` nomi shu ustunning o'zi bilan band
    date: Mapped[date_type] = mapped_column(Date, index=True, nullable=False)
    category_name: Mapped[str] = mapped_column(String(64), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    def __repr__(self) -> str:
        return f"<DailyStats date={self.date} category_name={self.category_name} count={self.count}>"


class InquiryTimer(Base):
    """`GROUP_ALERT_MODE=group` rejimidagi javob kutish (2026-10-12): har bir guruh uchun
    ko'pi bilan BITTA yozuv. Mijoz savol yozganda ochiladi; shu guruhga istalgan xodim yozsa
    o'chiriladi ("N daqiqada javob beraman" desa `due_at` suriladi). `due_at` o'tib ketsa
    guruhning o'ziga menejerlarni belgilab xabar yoziladi va `notified_at` qo'yiladi
    (`app/group_timers.py`). Yozuv bazada turgani uchun bot qayta ishga tushsa ham kutish
    yo'qolmaydi."""

    __tablename__ = "group_inquiry_timers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    group_title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    customer_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Kutish boshlangan (birinchi javobsiz) mijoz xabari
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Mijoz guruhi forum bo'lsa -- xabar yozilgan topik (ogohlantirish ham o'sha yerga yoziladi)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_link: Mapped[str | None] = mapped_column(String(255), nullable=True)

    wait_minutes: Mapped[int] = mapped_column(nullable=False)
    promised: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<InquiryTimer group_id={self.group_id} due_at={self.due_at}>"
