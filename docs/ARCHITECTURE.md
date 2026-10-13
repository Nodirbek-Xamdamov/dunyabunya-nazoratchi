# Arxitektura

## Doira (2026-09-25 da aniqlashtirilgan)

Telegram'ga tashlangan fayl (rasm yoki PDF) Odoo'dagi **mavjud** hujjatning chatter'iga yuklanadi, "bo'ldi, boshqa narsa emas".
Odoo'ga yoziladigan yagona narsa bu `ir.attachment` va unga bog'langan `message_post` (Note, matni "Telegram: yuboruvchi").
Yozuvlar yaratilmaydi, maydonlar o'zgarmaydi. Bo'limlar: sotuv (`sale.order`), xarid (`purchase.order`), ombor (`stock.picking`).
Yangilari `config/documents.yaml` orqali kod o'zgarmasdan qo'shiladi.

Bot chatdagi har bir rasm/PDF'ni kim tashlaganidan qat'i nazar tekshiradi. Bizning hujjat bo'lsa, biriktiradi, qayta tashlansa ham.
Hujjat bo'lmasa, jimgina tashlab yuboradi. Bizning hujjat bo'lib, aniqlanmasa, "tiniqroq tashlang" deb reply qiladi.
Oldingi rejalar bekor qilingan: "yangi yozuv yaratish" (`odoo_mapping.yaml`), bitta rasmni bir marta biriktirish (`duplicate`),
xodimlarni telefon orqali ro'yxatdan o'tkazish (0003 da qo'shilib, 0004 da `employees` jadvali bilan olib tashlangan).

## Umumiy ko'rinish

Bitta Python jarayoni (asyncio):

```
Telegram ──long polling──► handlers ──► SQLite: jobs (navbat) ◄──── review (odam javoblari: matn, tugmalar)
                                          │            ▲
                  ┌───────────────────────┘            │ notify_pending (outbox)
                  ▼                                    │
            Worker pool: yuklab olish → kodlar/PDF matni → Odoo'da topish → (AI + tekshiruv) → chatter'ga biriktirish
                                                       │
                                                  Notifier: reaksiya / savol / xato xabari
            + heartbeat (watchdog uchun), lokal fayllarni tozalash
```

- **Handlerlar** tez: fayl turi va hajmini tekshiradi, `jobs` ga yozadi, 👀 qo'yadi. Yuklab olishni worker qiladi
  (xato bo'lsa, oddiy retry).
- **Worker bosqichlari** (`pipeline/processor.py`): har bir chaqiruv jobni bir qadam suradi va har qadam DB'ga yoziladi.
  Tarmoq muammosi `TransientError` bilan backoff'ga o'tadi, Odoo'ning rad etishi (`OdooError`) `failed` ga olib keladi.
- **Bloklovchi ishlar** (zxing, pdfium, XML-RPC) `asyncio.to_thread` da ishlaydi. XML-RPC har oqimda o'z `ServerProxy` si bilan va timeout bilan.
- **Bir nusxa**: `data/docbot.lock` (OS darajasidagi qulf). **Tiriklik**: `data/heartbeat` har 30 soniyada yangilanadi.
  Task Scheduler'dagi watchdog (S4U, har daqiqa) jarayon o'lgan yoki heartbeat 3 daqiqadan eski bo'lsa, botni qayta ishga tushiradi.

## Hujjatni aniqlash

| Qadam | Nima bilan | Ishonch |
|---|---|---|
| QR portal havola `/my/orders/<id>?access_token=...` | id bo'yicha o'qish + `access_token` ni solishtirish | to'liq: token faqat shu yozuvniki. Boshqa bazada chop etilgan bo'lsa, mos kelmaydi va AI'ga o'tadi |
| QR/Code128 hujjat nomi (`OD/INT/01514`, `S82419`) | `name` bo'yicha qidirish (nomlar yagona, tekshirilgan) | yuqori |
| PDF matn qatlami | `documents.yaml` dagi `number` qolipi bo'yicha raqamlar, keyin `name` bo'yicha | yuqori (bir nechta hujjat chiqsa, odamdan so'raladi) |
| AI (`gpt-5.4-mini`, strict JSON schema) | raqam → yozuv, keyin `verify`: summa aniq, ismlar noaniq (fuzzy ≥ 0.80 mos, < 0.50 zid), telefon oxirgi 9 raqam | ≥ 1 mos va 0 zid bo'lsa avtomatik, aks holda odamdan so'raladi |
| Odam | "✅ Ha" (taklif) yoki raqamni javob qilib yozish | yakuniy |

Qidiruv har doim `allowed_company_ids` = API foydalanuvchisining barcha kompaniyalari bilan ishlaydi (3 kompaniya).
QR matni faqat parse qilinadi, havolaga so'rov yuborilmaydi. AI javobi faqat ma'lumot, buyruq sifatida bajarilmaydi.

## Holatlar

`received → qr_ok → ready → attached`, kod yo'q bo'lsa `received → ai_processing → ready`. Hujjat emas: `ignored`
(`received` yoki `ai_processing` dan; `qr_ok` dan emas, chunki bizning kod o'qilgan). Bizning hujjat, lekin aniqlanmasa: `needs_review`.
Urinishlar tugasa `failed`, matn rejimida "Kerak emas" bosilsa `rejected`. `duplicate` va `doc_key` navbatda qolgan, lekin ishlatilmaydi.
Ruxsat etilgan o'tishlar: `pipeline/states.py`. `NOTIFY_ON` = {attached, needs_review, duplicate, failed}: bu holatga o'tish
`notify_pending` ni yoqadi.

"Bizning hujjat" = bizning kod (QR/shtrix-kod/PDF raqami) o'qilgan (`extracted_data.our_code`) yoki AI o'xshashligi
≥ `ai.min_similarity` (70%). Sinovda hujjatlar 92–100% oldi, hujjat bo'lmagan rasm 0%.

## Idempotentlik

- Telegram update qayta kelsa: `UNIQUE(chat_id, message_id, file_unique_id)`.
- Bir job bir marta: attachment `description` = `telegram-docbot job <id>`. Qayta urinish shu belgi bo'yicha o'z attachment'ini va
  shu attachment bilan `mail.message` bor-yo'qligini tekshiradi. Yuklash va xabar orasida jarayon yiqilsa, yetishmagan qismigina qo'shiladi.
  Bir xil fayl yangi xabarda kelsa, bu yangi job va u yana biriktiriladi (foydalanuvchi qoidasi).
- AI natijasi `extracted_data.ai` ga saqlanadi va qayta urinishda qayta so'ralmaydi.

## Ichki DB

| Jadval | Maqsad |
|---|---|
| `jobs` | har bir fayl: manba (chat, xabar, yuboruvchi, fayl), lokal yo'l, holat, usul (qr/text/ai/human), kod matnlari, `extracted_data` (sha1, refs, AI, tekshiruv, taklif, attachment id), Odoo model/id/nom, xato, outbox (`notify_pending`, `review_message_id`), navbat maydonlari |
| `audit_log` | har bir o'zgarish (kim, qachon, usul, natija). `ai_read` hodisalari AI kunlik limitini sanash uchun |

Migratsiyalar: `0001` (boshlang'ich), `0002` (attach oqimi: yangi ustunlar, `odoo_created` → `attached`),
`0003`/`0004` (xodimlar ro'yxati qo'shildi, keyin olib tashlandi).

## Xavfsizlik

- Kim tashlasa ham qabul qilinadi (foydalanuvchi qoidasi). Himoya chat darajasida: faqat ruxsat etilgan guruhlar va shaxsiy chatlar.
  Hujjat bo'lmagan rasmlarning lokal nusxasi darhol o'chiriladi.
- Faqat ruxsat etilgan chatlar: `ALLOWED_CHAT_IDS` va admin qo'shgan (yoki ruxsat bergan) guruhlar (`telegram/chats.py`,
  `data/chats.json`; guruh supergroup'ga aylanib id'si o'zgarsa, ruxsat yangi id'ga o'tadi). Matnli suhbat e'tiborsiz qoladi
  (matn rejimi `notify.messages: true` dan tashqari).
- Fayl turi (rasm yoki PDF, baytlar bo'yicha aniqlanadi) va hajmi (≤ 20 MB, Bot API chegarasi) tekshiriladi.
- Sirlar faqat `.env` da. Log va DB'ga tushadigan xato matnlari maskalanadi (`redaction.py`). Odoo xatosidan faqat oxirgi qator olinadi.

## Qarorlar

- **Nega QR avval:** 0.05 s, bepul va aniq. AI taxminan 4 s va pullik, shuning uchun faqat zaxira yo'l.
- **Nega kichik QR uchun 2x kattalashtirish:** Telegram rasmni 1280 px ga siqadi, A4 suratida 41×41 modulli QR ~65–85 px bo'ladi.
  Simulyatsiyada kattalashtirib qayta urinish ko'p holatni qutqardi.
- **Nega faqat 👍:** foydalanuvchi so'rovi: faqat hujjatlarga reaksiya, har rasmga javob emas. 👀 ("qabul qilindi") olib tashlangan,
  chunki rasm hujjat ekani oldindan ma'lum emas. Yagona matnlar: `/start` ga "Assalomu alaykum, xush kelibsiz!" va aniqlanmagan
  hujjatga "tiniqroq tashlang" reply. `notify.messages: true` savol + tugmalar rejimini yoqadi.
- **Nega Odoo'ga darhol qayta ulanish:** Odoo.sh bo'sh turgan keep-alive ulanishni yopadi. Tuzatishdan oldin bo'sh vaqtdan keyingi
  birinchi fayl `SSLEOFError` bilan backoff'ga tushib ~35 soniyada biriktirilgan (2026-09-25). Endi yangi ulanish bilan darhol qayta uriniladi.
- **Nega yuklab olish workerda:** handler tez qoladi, yuklab olish xatosi oddiy retry bo'ladi.
