"""
FastAPI Pydantic validatsiya sxemalari.
"""

from datetime import datetime
from typing import Any, List, Optional, Union
from pydantic import BaseModel, Field


class WebhookUserPayload(BaseModel):
    username: Optional[Union[str, List[str]]] = None
    chat_id: Optional[int] = None


class WebhookManagerPayload(BaseModel):
    name: Optional[str] = None


class OdooWebhookPayload(BaseModel):
    doc_id: Optional[str] = None
    pdf_base64: str
    message: Optional[str] = None
    subject: Optional[str] = None
    user: Optional[WebhookUserPayload] = None
    manager: Optional[WebhookManagerPayload] = None
    order: Optional[dict[str, Any]] = Field(default_factory=dict)
    # Agar to'ldirilsa, buyurtma foydalanuvchi(lar)ga emas, shu Telegram guruhiga
    # (PDF + tasdiqlash so'rovnomasi) yuboriladi. Individual yo'nalishdan mustaqil.
    group_id: Optional[int] = None


class ChatIdResponse(BaseModel):
    doc_id: str
    username: str
    chat_id: Optional[int] = None
    status: str
    confirmation_status: str
    message: Optional[str] = None
    feedback_files: Optional[Union[str, List[str]]] = None
    synced: bool


class SyncUpdateRequest(BaseModel):
    doc_ids: Optional[List[str]] = None
    ids: Optional[List[int]] = None


class GroupMembershipResponse(BaseModel):
    id: int
    chat_id: int
    username: Optional[str] = None
    group_id: int
    group_title: Optional[str] = None


class GroupMembershipSyncRequest(BaseModel):
    ids: List[int]


class GroupOrderResponse(BaseModel):
    order_id: int
    doc_id: str
    group_id: int
    chat_id: Optional[int] = None
    confirmation_status: str
    feedback_text: Optional[str] = None


class BotMenuRequest(BaseModel):
    """Bot menyusidagi (Balance/Orders/Quotation/Support/Info) tugma bosilganda yuboriladi."""
    chat_id: int
    username: Optional[str] = None
    lang: str = "uz"
    # Balance bo'limi uchun: scope="general"/"object", period="current"/"periodic"
    scope: Optional[str] = None
    period: Optional[str] = None
    # Faqat scope="object" bo'lsa to'ldiriladi
    object_name: Optional[str] = None
    # Faqat period="periodic" bo'lsa to'ldiriladi ("DD.MM.YYYY")
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class BotMenuResponse(BaseModel):
    success: bool = True
    message: str


class MonthlyStatEntry(BaseModel):
    """Bir oylik statistika qatori (joriy yil, Yanvar->Dekabr). `paid` -- Odoo'ning
    `x_studio_paid_amount` maydonidan olingan to'lov holati (2026-08-04, mijoz aniq
    so'ragan). `unpaid` ustuni keyinroq olib tashlandi -- to'lanmagan summa allaqachon
    "Balans" ustunida (`spent - paid` farqi orqali) qarz sifatida hisobga olingan."""
    month: str  # "YYYY-MM"
    spent: float
    returned: float
    paid: float


class ObjectStatEntry(BaseModel):
    """Bitta obyekt (yoki "Umumiy", `object_name` shu yerda allaqachon FastAPI
    tomonidan hal qilingan -- odoo_client.py o'ziga xos i18n bilmaydi) bo'yicha
    statistika qatori. `paid` (2026-08-04, real "qarshi talmarjon" obyektida to'lov
    PDF'da ko'rinmayapti degan xabar tekshirilib qo'shildi) -- shu obyektga tegishli
    invoyslarni yopgan to'lovlar yig'indisi, `odoo_client.get_monthly_statistics`'da
    hisoblangan (izohiga qarang)."""
    object_id: int
    object_name: str
    spent: float
    returned: float
    paid: float


class BotStatisticsResponse(BaseModel):
    """Bot uchun: Statistika tugmasi bosilganda. `found=False` -- mijoz Odoo'da
    topilmadi; `months=None` -- Odoo so'rovi muvaffaqiyatsiz bo'ldi (0 xarajatdan farqli).
    `current_balance` -- mijozning HOZIRGI (Odoo'dan, `app.balance.invert_balance_sign`
    orqali) balansi, xuddi Malumotlarim'da ko'rsatilgani bilan bir xil -- PDF'dagi oylik
    "Balans" ustuni shu qiymatdan orqaga hisoblab quriladi (2026-08-04, mijoz aniq
    so'ragan: ikkalasi bir xil balansni ko'rsatishi kerak)."""
    success: bool = True
    found: bool = True
    partner_name: Optional[str] = None
    months: Optional[List[MonthlyStatEntry]] = None
    by_object: Optional[List[ObjectStatEntry]] = None
    current_balance: Optional[float] = None


class BalanceObjectResponse(BaseModel):
    """Bot uchun: mijozning obyektlari ro'yxati (Balans -> Obyekt tanlanganda), keshdagi (DB) ma'lumotdan olinadi.
    `latitude`/`longitude` -- faqat "Buyurtma berish" oqimida ishlatiladi (Odoo'ning o'z
    partner_latitude/partner_longitude maydonlaridan, 2026-08-02): agar to'ldirilgan
    bo'lsa, shu obyekt tanlanganda lokatsiya qayta so'ralmaydi."""
    id: int
    name: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class OrderCustomerLookupResponse(BaseModel):
    """Bot uchun: "Buyurtma berish" bosilganda mijozni Odoo'dan (chat_id bo'yicha) qidiradi --
    obyekt tanlash / lokatsiya so'rash / yangi-mijoz oqimlaridan qaysi birini ko'rsatishni
    aniqlash uchun ishlatiladi. `partner_id` -- mijoz Odoo'da topilsa, buyurtma yozilganda
    (OrderSubmitRequest) crm.lead.partner_id sifatida qayta yuboriladi."""
    found: bool
    partner_id: Optional[int] = None
    partner_name: Optional[str] = None
    partner_phone: Optional[str] = None
    objects: List[BalanceObjectResponse] = Field(default_factory=list)


class OrderSubmitRequest(BaseModel):
    """Bot -> FastAPI: "Buyurtma berish" so'rovini yuboradi -- Odoo'da MAVJUD (partner_id
    berilgan) va butunlay yangi (partner_id=None, partner_phone bilan) mijozlar uchun bir xil.
    `object_name` yoki `latitude`/`longitude` juftligidan faqat biri to'ldiriladi (obyekt
    tanlangan bo'lsa obyekt, aks holda lokatsiya). `photo_path`/`video_path`/`audio_path`/
    `document_path` (2026-08-03) -- bot ORDER_MEDIA_DIR'ga allaqachon yuklab olgan faylning
    nomi (relative, to'liq yo'l emas) -- FastAPI shu faylni o'qib Odoo'ga ir.attachment
    sifatida (crm.lead'ga biriktirilgan holda) yuklaydi."""
    partner_id: Optional[int] = None
    partner_name: str
    partner_phone: Optional[str] = None
    chat_id: int
    order_details: str
    object_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    photo_path: Optional[str] = None
    video_path: Optional[str] = None
    audio_path: Optional[str] = None
    document_path: Optional[str] = None


class OrderSubmitResponse(BaseModel):
    success: bool
    lead_id: Optional[int] = None


class OdooBalanceObjectSync(BaseModel):
    """Odoo'dan sync qilinadigan bitta obyekt (qurilish joyi)."""
    id: int
    name: str
    balance_uzs: float = 0.0
    balance_usd: float = 0.0


class OdooBalanceSyncRequest(BaseModel):
    """Odoo -> FastAPI: mijozning balans va obyektlari sync qilinadi (bosingdan
    ushbu chat_id uchun mavjud yozuv bo'lsa yangilanadi, bo'lmasa yaratiladi)."""
    chat_id: int
    partner_name: str
    total_balance_uzs: float = 0.0
    total_balance_usd: float = 0.0
    objects: List[OdooBalanceObjectSync] = Field(default_factory=list)


class DeliveryConfirmationPayload(BaseModel):
    """taklif-bot -> FastAPI: haydovchi yuborgan yetkazib berish rasmi + tijorat taklifi
    raqami + menejer ismi (taklif-botning o'z QR/VLM pipeline'i orqali aniqlangan)."""
    doc_id: str
    manager_name: Optional[str] = None
    photo_base64: str
    uploaded_by_name: Optional[str] = None


class DeliveryConfirmationResponse(BaseModel):
    """Odoo uchun: GET /api/v1/delivery-confirmations orqali qaytadigan yozuv. Faqat
    Odoo'ga kerakli maydonlar (2026-08-02, foydalanuvchi so'rovi bo'yicha) -- mijoz
    chat_id/xabar-yuborilganmi/kim yuklagani/vaqt kabi Telegram-ichki ma'lumotlar
    endi bu javobda yo'q (baza va bot logikasida hamon mavjud, shunchaki Odoo'ga
    ko'rsatilmaydi)."""
    id: int
    doc_id: str
    manager_name: Optional[str] = None
    photo_url: str
    synced: bool


class ObjectLocationSubmitRequest(BaseModel):
    """Bot -> FastAPI: "Buyurtma berish" oqimida mijoz obyekt (mavjud yoki yangi yaratilgan)
    uchun GPS lokatsiyasini yuborgach. `object_id` -- mavjud obyekt tanlangan bo'lsa Odoo'dagi
    child partner ID'si, yangi obyekt bo'lsa None."""
    partner_id: int
    object_id: Optional[int] = None
    object_name: str
    is_new_object: bool = False
    chat_id: int
    latitude: float
    longitude: float
    username: Optional[str] = None
    partner_name: Optional[str] = None
    partner_phone: Optional[str] = None


class GroupInquirySubmitRequest(BaseModel):
    """Bot -> FastAPI: guruhda kimdir botni mention qilib yozganda (2026-08-03).
    FastAPI shu chat_id'ni Odoo'dan (x_telegram_id) qidiradi -- topilsa/topilmasa
    ikkala holatda ham HAQIQIY crm.lead yoziladi (2026-08-03, ikkinchi o'zgarish --
    avval faqat topilgan holatda GroupInquiry sifatida saqlanardi, endi u ustiga
    ham qo'shildi): topilsa mavjud partner_id bilan, topilmasa contact_name sifatida
    `full_name`/`username`dan foydalanib (telefon yo'q). Topilgan holatda bot hech
    narsa demaydi, topilmagan holatda esa lead yaratilgan-yaratilmaganiga qarab
    mos javob beradi (pastdagi GroupInquirySubmitResponse.lead_created'ga qarang)."""
    chat_id: int
    username: Optional[str] = None
    full_name: Optional[str] = None
    group_id: int
    group_title: Optional[str] = None
    message_text: str


class GroupInquirySubmitResponse(BaseModel):
    found: bool
    partner_id: Optional[int] = None
    # crm.lead muvaffaqiyatli yaratilganini bildiradi (found=True/False'dan qat'i nazar,
    # 2026-08-03) -- bot buni topilmagan holatda mijozga qanday javob berishni
    # aniqlash uchun ishlatadi (muvaffaqiyatli bo'lsa "so'rovingiz qabul qilindi",
    # bo'lmasa umumiy xatolik xabari).
    lead_created: bool = False


class GroupInquiryResponse(BaseModel):
    """Odoo uchun: GET /api/v1/group-inquiries orqali qaytadigan yozuv."""
    id: int
    chat_id: int
    username: Optional[str] = None
    full_name: Optional[str] = None
    group_id: int
    group_title: Optional[str] = None
    partner_id: int
    partner_name: Optional[str] = None
    message_text: str
    created_at: datetime
    synced: bool


class GroupInquirySyncRequest(BaseModel):
    ids: List[int]


class ObjectContactUpdateRequest(BaseModel):
    """Bot -> FastAPI: "Ma'lumotlarni tahrirlash" oqimidan obyektning telefon raqami
    va/yoki GPS lokatsiyasini yangilash so'rovi (2026-08-05). `phone` yolg'iz, yoki
    `latitude`+`longitude` juftligi yolg'iz, yoki ikkalasi birga berilishi mumkin.
    `object_name`/`chat_id`/`username`/`partner_id` -- Odoo'ga yozilmaydi (faqat
    `phone`/`latitude`/`longitude` yoziladi), FAQAT muvaffaqiyatli bo'lsa ObjectEditLog
    yozuviga LOG sifatida saqlanadi (Odoo xodimlari "qaysi obyekt o'zgardi" so'rasa)."""
    object_id: int
    object_name: str
    chat_id: int
    username: Optional[str] = None
    partner_id: Optional[int] = None
    phone: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class ObjectContactUpdateResponse(BaseModel):
    success: bool


class ObjectEditLogResponse(BaseModel):
    """Odoo uchun: GET /api/v1/object-edits orqali qaytadigan yozuv -- mijozlar
    "Ma'lumotlarni tahrirlash" orqali o'zgartirgan obyektlar logi (2026-08-05)."""
    id: int
    object_id: int
    object_name: str
    partner_id: Optional[int] = None
    field: str
    new_phone: Optional[str] = None
    new_latitude: Optional[float] = None
    new_longitude: Optional[float] = None
    chat_id: int
    changed_by_username: Optional[str] = None
    changed_at: datetime
    synced: bool


class ObjectEditLogSyncRequest(BaseModel):
    ids: List[int]


class ObjectLocationResponse(BaseModel):
    """Odoo uchun: GET /api/v1/object-locations orqali qaytadigan yozuv."""
    id: int
    partner_id: int
    object_id: Optional[int] = None
    object_name: str
    is_new_object: bool
    latitude: float
    longitude: float
    chat_id: int
    created_by_username: Optional[str] = None
    partner_name: Optional[str] = None
    partner_phone: Optional[str] = None
    created_at: datetime
    synced: bool
