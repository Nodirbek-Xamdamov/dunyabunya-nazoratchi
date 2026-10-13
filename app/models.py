"""
SQLAlchemy v2 ORM modellari (PostgreSQL/SQLite moslashtirilgan).
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
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
