# Odoo ↔ Telegram Integratsiya Tizimi

<!-- qisqacha -->
> **Qisqacha.** Odoo va Telegram o'rtasidagi integratsiya (`@dunyabunya_assistant_bot`): Odoo'dan kelgan buyurtma va PDF hujjatni mijozga Telegram orqali yetkazadi, guruhlardagi xabarlarni toifalarga ajratib kuzatadi va kunlik hisobot beradi.
>
> **Texnologiyalar:** Python, FastAPI, Aiogram 3, Telethon (userbot), PostgreSQL
>
> **Ishga tushirish:** FastAPI + bot + userbot alohida jarayonlar (pastda batafsil; sozlamalar `.env` da)

FastAPI + Aiogram 3 (Bot) + Telethon (Premium Userbot) asosida ishlaydigan
asinxron integratsiya. Odoo'dan kelgan buyurtma va PDF hujjatni avtomatik
ravishda mijozga Telegram orqali yetkazadi.

## 1. Loyiha strukturasi

```
odoo_telegram_integration/
├── app/
│   ├── __init__.py
│   ├── config.py          # .env orqali sozlamalar
│   ├── database.py        # Async SQLAlchemy engine/session
│   ├── models.py           # Order modeli (pending/sent/expired)
│   ├── utils.py            # username normalize, PDF saqlash/o'chirish
│   ├── schemas.py          # Pydantic sxemalar
│   ├── fastapi_app.py      # FastAPI endpointlari (webhook, chat-ids)
│   ├── bot.py               # Aiogram 3 bot (/start ishlov beruvchi)
│   └── userbot.py           # Telethon userbot (birinchi xabar yuboruvchi)
├── data/
│   ├── pdfs/                # Vaqtinchalik PDF fayllar (yuborilgach o'chadi)
│   └── webhook_logs/        # Audit uchun xom webhook JSON loglari
├── deploy/
│   ├── fastapi.service
│   ├── bot.service
│   └── userbot.service
├── requirements.txt
├── .env.example
└── README.md
```

Uchta jarayon **mustaqil systemd servis** sifatida ishlaydi va faqat umumiy
baza (PostgreSQL/SQLite) orqali bog'lanadi:

1. **fastapi.service** — Odoo'dan webhook qabul qiladi, bazaga yozadi.
2. **userbot.service** — bazani kuzatib, yangi `pending` yozuvlar uchun
   foydalanuvchiga birinchi xabarni yozadi (`notified=True` qilib belgilaydi).
3. **bot.service** — foydalanuvchi `/start` bosganda ma'lumot va PDF'ni yuboradi.

## 2. Ubuntu serverda o'rnatish

### 2.1. Tizim paketlari

Serveringizda Python 3.12.3 o'rnatilgan bo'lgani uchun alohida Python versiyasini
qo'shimcha o'rnatish shart emas — faqat `venv` va `pip` kerak:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip git
python3 --version   # 3.12.3 ekanini tasdiqlang
# PostgreSQL ishlatmoqchi bo'lsangiz:
sudo apt install -y postgresql postgresql-contrib
```

### 2.2. Loyihani serverga joylashtirish

```bash
sudo mkdir -p /opt/odoo_telegram_integration
sudo chown $USER:$USER /opt/odoo_telegram_integration
# loyiha fayllarini shu papkaga ko'chiring (scp, git clone va h.k.)
cd /opt/odoo_telegram_integration
```

### 2.3. Virtual environment va kutubxonalar

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> Loyihadagi barcha kutubxonalar (FastAPI, SQLAlchemy 2.0, Aiogram 3, Telethon)
> Python 3.12 bilan to'liq mos ishlaydi — qo'shimcha moslashtirish shart emas.

### 2.4. `.env` faylini sozlash

```bash
cp .env.example .env
nano .env
```

To'ldirilishi shart bo'lgan qiymatlar:

| O'zgaruvchi | Tavsif | Qayerdan olinadi |
|---|---|---|
| `API_KEY` | Odoo bilan bog'lanish uchun maxfiy kalit | O'zingiz generatsiya qiling (masalan `openssl rand -hex 32`) |
| `BOT_TOKEN` | Aiogram bot tokeni | @BotFather |
| `BOT_USERNAME` | Bot username'i (`@...`) | @BotFather |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | Userbot uchun | https://my.telegram.org -> API Development Tools |
| `DATABASE_URL` | DB ulanish satri | PostgreSQL yoki SQLite |

PostgreSQL ishlatish uchun:

```bash
sudo -u postgres psql -c "CREATE DATABASE odoo_telegram_db;"
sudo -u postgres psql -c "CREATE USER db_user WITH PASSWORD 'db_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE odoo_telegram_db TO db_user;"
```

va `.env`:
```
DATABASE_URL=postgresql+asyncpg://db_user:db_password@localhost:5432/odoo_telegram_db
```

### 2.5. Telethon Userbot uchun birinchi (interaktiv) login

Systemd headless muhitda telefon kodini so'rab bo'lmaydi, shuning uchun
sessiyani **avval qo'lda, terminal orqali** yaratib olish kerak:

```bash
source venv/bin/activate
python -m app.userbot
# Telefon raqami, SMS/Telegram kodi va (agar bor bo'lsa) 2FA parolni kiriting
# Session muvaffaqiyatli yaratilgach, Ctrl+C bilan to'xtating
```

Bu jarayon `userbot_session.session` faylini yaratadi — shundan keyin
systemd orqali headless ishlay oladi.

### 2.6. Ishchi papka va foydalanuvchi (xavfsizlik uchun)

```bash
sudo useradd -r -s /bin/false appuser
sudo chown -R appuser:appuser /opt/odoo_telegram_integration
sudo mkdir -p /var/log/odoo_telegram_integration
sudo chown appuser:appuser /var/log/odoo_telegram_integration
```

> **Eslatma:** Agar `userbot.service`ni `appuser` nomidan ishlatmoqchi
> bo'lsangiz, 2.5-bandni ham `sudo -u appuser venv/bin/python -m app.userbot`
> ko'rinishida bajaring, aks holda session fayl egasi mos kelmay qoladi.

### 2.7. systemd servislarini o'rnatish

```bash
sudo cp deploy/fastapi.service /etc/systemd/system/
sudo cp deploy/bot.service /etc/systemd/system/
sudo cp deploy/userbot.service /etc/systemd/system/

sudo systemctl daemon-reload

sudo systemctl enable fastapi.service bot.service userbot.service
sudo systemctl start fastapi.service bot.service userbot.service
```

### 2.8. Holatni va loglarni tekshirish

```bash
sudo systemctl status fastapi.service
sudo systemctl status bot.service
sudo systemctl status userbot.service

# Real-time loglar
sudo journalctl -u fastapi.service -f
sudo journalctl -u bot.service -f
sudo journalctl -u userbot.service -f
```

## 3. Test qilish

### 3.1. Health check

```bash
curl http://localhost:8000/health
```

### 3.2. Odoo webhook simulyatsiyasi

```bash
curl -X POST http://localhost:8000/api/v1/webhook/odoo \
  -H "Content-Type: application/json" \
  -H "X-API-Key: CHANGE_ME_SUPER_SECRET_KEY" \
  -d '{
        "user": {"username": " @Nodir "},
        "order": {"buyurtma_raqami": "ORD-1001", "mahsulot": "Noutbuk Dell", "narx": "8500000 so'\''m"},
        "pdf_base64": "'"$(base64 -w0 /path/to/sample.pdf)"'"
      }'
```

Muvaffaqiyatli javob:
```json
{"success": true, "order_id": 1, "normalized_username": "nodir", "status": "pending"}
```

Shundan so'ng userbot avtomatik `@nodir`ga xabar yozadi, mijoz botga
`/start` bossa — ma'lumot va PDF yuboriladi.

### 3.3. chat-id'larni olish (Odoo tomonidan)

```bash
curl -H "X-API-Key: CHANGE_ME_SUPER_SECRET_KEY" \
     "http://localhost:8000/api/v1/chat-ids?synced=false"
```

Odoo ma'lumotni o'ziga saqlab olgach:

```bash
curl -X POST http://localhost:8000/api/v1/chat-ids/mark-synced \
  -H "Content-Type: application/json" \
  -H "X-API-Key: CHANGE_ME_SUPER_SECRET_KEY" \
  -d '{"ids": [1, 2, 3]}'
```

## 4. Guruh (group) qo'llab-quvvatlash

Buyurtmani bitta foydalanuvchi o'rniga Telegram **guruhiga** yuborish, PDF hujjat bilan
birga anonim tasdiqlash so'rovnomasi (poll: ✅ Tasdiqlash / ❌ Rad etish / ✏️ O'zgartirish)
ko'rinishida ishlaydi.

### 4.1. Operatsion talab (kod bilan avtomatlashtirilmaydi)

Guruh oqimi ishlashi uchun **ikkala akkaunt ham** tegishli guruhga qo'lda qo'shilishi va
**admin** qilinishi kerak:

- **Rasmiy bot** (`@dunyabunya_assistant_bot` va h.k.) — guruhga PDF va so'rovnoma
  yuborish uchun (Bot API faqat o'zi a'zo bo'lgan chatga yoza oladi).
- **Telethon userbot** (premium akkaunt) — guruh a'zolari ro'yxatini o'qish uchun
  (oddiy Bot API katta guruhda to'liq a'zolar ro'yxatini bermaydi, faqat MTProto klient
  beradi). Admin huquqi shart — userbot faqat o'zi **admin** bo'lgan guruhlarni
  skanerlaydi (`app/userbot.py:sync_group_memberships`, har `GROUP_SYNC_INTERVAL_SECONDS`
  — default 900 soniyada bir marta ishlaydi).

### 4.2. Guruh a'zoligini Odoo'ga uzatish (kim qaysi guruhda ekanini bilish uchun)

Userbot skanerlab, botga avval `/start` bosgan (bizga tanish) foydalanuvchilarning qaysi
guruh(lar)da ekanini `group_memberships` jadvaliga yozadi. **Bu faqat ma'lumot uzatish —
hech narsani avtomatik yubormaydi.** Odoo buni xuddi `/api/v1/chat-ids` bilan bir xil
naqshda tortib oladi, o'z bazasida saqlab qo'yadi, va shu ma'lumot asosida **o'zi qaror
qiladi** — keyingi buyurtmani o'sha foydalanuvchining shaxsiy chat_id'siga yuborsinmi,
guruh_id'siga yuborsinmi, yoki ikkalasiga ham. To'liq endpoint spetsifikatsiyasi 4.4-bandda.

### 4.3. Buyurtmani yuborish — Odoo har safar mustaqil tanlaydi

Webhookdagi `user` (username/chat_id) va `group_id` maydonlari **bir-biridan butunlay
mustaqil**:

- Faqat `user` berilsa → faqat o'sha foydalanuvchi(lar)ga individual yuboriladi.
- Faqat `group_id` berilsa → faqat o'sha guruhga yuboriladi.
- **Ikkalasi ham berilsa → ikkalasiga ham yuboriladi** (2 ta alohida Order yaratiladi —
  biri individual, biri guruh uchun).
- Ikkalasi ham berilmasa → eski xatti-harakat saqlanadi (`no_username` bilan bitta
  buyurtma, orqaga moslik uchun).

Hech narsa avtomatik ravishda guruhga ketmaydi — buni har safar Odoo `group_id`
maydonini qo'shish orqali aniq ko'rsatishi kerak.

```bash
curl -X POST http://localhost:8000/api/v1/webhook/odoo \
  -H "Content-Type: application/json" \
  -H "X-API-Key: CHANGE_ME_SUPER_SECRET_KEY" \
  -d '{
        "doc_id": "ORD-1001",
        "user": {"username": "nodir"},
        "group_id": -1001234567890,
        "order": {"buyurtma_raqami": "ORD-1001"},
        "pdf_base64": "'"$(base64 -w0 /path/to/sample.pdf)"'"
      }'
```

Javob (ikkalasi ham berilganda):
```json
{
  "success": true,
  "doc_id": "ORD-1001",
  "group": {"success": true, "order_id": 46, "group_id": -1001234567890, "notified": true},
  "individual": {"created_orders_count": 1, "orders": [{"order_id": 47, "normalized_username": "nodir", "chat_id": 123456789, "notified": true}]}
}
```

`group` yoki `individual` maydonlaridan biri ishlatilmasa — mos ravishda `null` qaytadi.

Guruhga PDF hujjat + anonim so'rovnoma (✅ Tasdiqlash / ❌ Rad etish / ✏️ O'zgartirish)
yuboriladi. Poll anonim bo'lgani uchun kim qanday ovoz berganini bilib bo'lmaydi — faqat
har variantning umumiy soni kuzatiladi va ovoz berilganda avtomatik yangilanadi
(`app/bot.py:handle_group_poll_update`). Natijani Odoo shu yerdan oladi:

```bash
curl -H "X-API-Key: CHANGE_ME_SUPER_SECRET_KEY" "http://localhost:8000/api/v1/group-orders"
# -> [{"order_id": 46, "doc_id": "ORD-1001", "group_id": -1001234567890,
#      "confirm_count": 3, "reject_count": 0, "changes_count": 1, "poll_closed": false}]
```

### 4.4. To'liq endpoint spetsifikatsiyasi (Odoo dasturchilari uchun)

**`GET /api/v1/group-memberships`** — foydalanuvchining qaysi guruh(lar)da ekanini olish

| | |
|---|---|
| Method / Path | `GET /api/v1/group-memberships` |
| Header | `X-API-Key: <API_KEY>` (majburiy) |
| Query param | `synced` (ixtiyoriy, `true`/`false`) — faqat hali Odoo tomonidan o'qilmaganlarni olish uchun `synced=false` |
| Muvaffaqiyatli javob | `200 OK`, `list[GroupMembershipResponse]` |

Javob elementi (`GroupMembershipResponse`):

| Maydon | Turi | Tavsif |
|---|---|---|
| `id` | `int` | Ushbu a'zolik yozuvining ichki ID'si — `mark-synced`ga aynan shuni yuborasiz |
| `chat_id` | `int` | Foydalanuvchining shaxsiy Telegram chat_id'si (bot bilan yozishgani) |
| `username` | `str \| null` | Telegram username (agar bo'lsa) |
| `group_id` | `int` | Guruhning Telegram chat_id'si (manfiy son) — buyurtmani shu guruhga yuborish uchun ishlatiladi |
| `group_title` | `str \| null` | Guruh nomi |

Misol:
```bash
curl -H "X-API-Key: CHANGE_ME_SUPER_SECRET_KEY" \
     "http://localhost:8000/api/v1/group-memberships?synced=false"
```
```json
[
  {"id": 1, "chat_id": 123456789, "username": "nodir", "group_id": -1001234567890, "group_title": "Dunyabunya VIP mijozlar"},
  {"id": 2, "chat_id": 987654321, "username": "aziz", "group_id": -1001234567890, "group_title": "Dunyabunya VIP mijozlar"}
]
```

Ma'lumotni o'ziga saqlab olgach, Odoo shu yozuvlarni sinxronlangan deb belgilashi kerak
(aks holda keyingi so'rovda yana qaytadi) — yuqoridagi javobdagi `id` qiymatlarini yuboring:

**`POST /api/v1/group-memberships/mark-synced`**

| | |
|---|---|
| Method / Path | `POST /api/v1/group-memberships/mark-synced` |
| Header | `X-API-Key`, `Content-Type: application/json` |
| Body | `{"ids": [1, 2]}` — `GroupMembershipResponse.id` qiymatlari ro'yxati |
| Javob | `{"success": true, "updated_count": 2, "updated_ids": [1, 2]}` |

**`POST /api/v1/webhook/odoo`** — yangi buyurtma yuborish (individual va/yoki guruh)

| Maydon | Turi | Tavsif |
|---|---|---|
| `pdf_base64` | `str` | **Majburiy.** PDF hujjat, base64 |
| `doc_id` | `str \| null` | Buyurtma/hujjat raqami |
| `message` | `str \| null` | Tayyor xabar matni (bo'lmasa standart shablon ishlatiladi) |
| `subject` | `str \| null` | PDF fayl nomi uchun mavzu |
| `user.username` | `str \| list[str] \| null` | Individual yuborish uchun |
| `user.chat_id` | `int \| null` | Agar oldindan ma'lum bo'lsa (masalan shu endpointdan olingan) |
| `group_id` | `int \| null` | Guruhga yuborish uchun — `/api/v1/group-memberships`dan olingan `group_id` |
| `manager.name` | `str \| null` | Menejer ismi |
| `order` | `object` | Buyurtma tafsilotlari (erkin JSON) |

**`GET /api/v1/group-orders`** — guruhga yuborilgan buyurtmalarning poll natijalari

| | |
|---|---|
| Method / Path | `GET /api/v1/group-orders` |
| Header | `X-API-Key` |
| Javob | `list[GroupOrderPollResponse]`: `order_id`, `doc_id`, `group_id`, `poll_id`, `confirm_count`, `reject_count`, `changes_count`, `poll_closed` |

## 4.5. Mijoz guruhlarini kuzatish

Bot mijozlar bor guruhlarga (200+) qo'shiladi va fonda har bir xabarni tahlil qiladi.
Natijalar ichki (xodimlar) forum-guruhiga (`FEEDBACK_HUB_GROUP_ID`, `.env`da
`CENTRAL_FORUM_CHAT_ID` deb ham yozish mumkin) tushadi. Mijoz guruhlarida bot **jim turadi**:
reaksiya qo'ymaydi, javob yozmaydi. Yagona istisno -- `GROUP_ALERT_MODE=group` rejimidagi
javobsiz savol ogohlantirishi (pastda).

### Modullar

| Fayl | Vazifasi |
|---|---|
| `app/bot/handlers/group_monitor.py` | Guruh xabarlarini tinglash, topiklarga tashlash, bazaga yozish, `dm` rejimidagi javob kutish |
| `app/ai_processor.py` | Tahlil quvuri: rasm / video kadrlari / nutqni tayyorlaydi, xabar turiga qarab modelni tanlaydi |
| `app/group_ai.py` | Tasniflash: prompt, JSON sxema, 8 ta toifa, ishonch chegarasi, kalit-so'zlarga qaytish |
| `app/stt.py` | Nutq -> matn: lokal model yoki OpenAI |
| `app/group_timers.py` | `group` rejimidagi javob kutish va guruhga yoziladigan ogohlantirish |
| `app/group_registry.py` | Guruhlar (`monitored_groups`) va menejerlar (`group_managers`) ro'yxati |
| `app/group_stats.py` | `message_logs` va `daily_stats`ga yozish |
| `app/daily_report.py` | Yarim tundagi HTML hisobot |
| `app/bot/handlers/hub_admin.py` | Ichki guruhdagi buyruqlar |

Yangi jadvallar (`monitored_groups`, `group_managers`, `message_logs`, `daily_stats`,
`group_inquiry_timers`) bot ishga tushganda o'zi yaratiladi -- migratsiya kerak emas.

### Xabar qanday tahlil qilinadi

| Xabar turi | Qanday |
|---|---|
| Matn | Tasniflovchi model (`GROUP_AI_PROVIDER` / `GROUP_AI_MODEL`, odatda lokal Gemma) |
| Rasm (surat yoki rasm fayli) | O'sha model, rasmning o'zi bilan (`GROUP_IMAGE_ANALYSIS_ENABLED=true`). Model rasmda nima ko'ringanini bir jumla bilan yozib ham beradi |
| Ovozli xabar, audio | Nutq matnga o'giriladi (`GROUP_STT_PROVIDER`: `local` yoki `openai`), matni tasniflanadi |
| Video, dumaloq video | Ovozi matnga o'giriladi + `GROUP_VIDEO_FRAMES` ta kadr rasm sifatida qo'shiladi |

Ovozli xabar / audio / video tahlili uchun alohida model: `GROUP_MEDIA_AI_PROVIDER=openai`,
`GROUP_MEDIA_AI_MODEL=gpt-4o-mini` (bo'sh bo'lsa -- matn bilan bir xil model).

Model avval xabar **kompaniyaga oidmi** degan savolga javob beradi (tovar, narx, buyurtma,
yetkazib berish, xodimlar ...). Oid bo'lmagan oddiy suhbat butunlay tashlab yuboriladi:
bazaga yozilmaydi, hech qayerga tashlanmaydi -- faqat soni sanaladi. Oid bo'lsa 8 ta
toifadan biriga tushadi:

1. Dastavka va Logistika
2. Mahsulot Sifati va Nuqsonlar
3. Komplektatsiya va Hajm
4. Xodimlar va Xizmat Ko'rsatish
5. Narx va To'lovlar
6. Qaytarish va Kafolat
7. Ijobiy Fikrlar
8. Boshqa

### Ichki guruhga nima tushadi

| Guruhda nima bo'ldi | Bot nima qiladi |
|---|---|
| Mijoz shikoyat qildi | Xabarni shu toifaning topigiga **bir marta** tashlaydi: guruh nomi, kim yozgani, xabarning o'zi (matn yoki media nusxasi), xabarga havola; ovozli xabar bo'lsa matni, rasm bo'lsa unda nima ko'ringani |
| Mijoz ijobiy fikr bildirdi | "Ijobiy Fikrlar" topigiga |
| Kimdir "tovar yo'q ekan" dedi | "Tovar yo'q" topigiga |
| Mijoz oddiy savol/buyurtma yozdi | Hech qayerga tashlanmaydi, faqat `message_logs`ga yoziladi va javob kutish boshlanadi |
| Kompaniyaga aloqasiz suhbat | Hech narsa (faqat sanaladi) |

Topiklar: bot toifaga mos topikni nomi bo'yicha topadi; topilmasa **o'zi yaratadi**
(`HUB_AUTO_CREATE_TOPICS=true`, botga ichki guruhda "Mavzularni boshqarish" huquqi kerak).
Yarata olmasa shikoyat umumiy "Shikoyatlar" topigiga, u ham bo'lmasa guruhning umumiy
qismiga tushadi. Topik ID'larini qo'lda ham berish mumkin: `HUB_TOPIC_IDS=delivery=12,price=14`
(kalitlar: `delivery, quality, completeness, staff, price, returns, positive, other, stock,
unanswered`). Qo'lda ochilgan topikni bot unga bir marta xabar yozilgach "ko'radi".

### Javobsiz qolgan savol (`GROUP_ALERT_MODE`)

Mijoz tovar, narx yoki buyurtma haqida savol yozsa `STAFF_REPLY_TIMEOUT_MINUTES` (5) daqiqalik
kutish boshlanadi. Xodim "10 minutda javob beramiz" desa kutish 10 daqiqaga suriladi. Kutishlar
bazada turadi -- bot qayta ishga tushsa ham yo'qolmaydi.

| Rejim | Kutishni nima bekor qiladi | Muddat o'tsa |
|---|---|---|
| `dm` (standart) | Murojaat qilingan xodimning javobi; hech kimga qaratilmagan savol uchun -- istalgan xodimning javobi | Xodimning (yoki guruh menejerining) **shaxsiy chatiga** eslatma. Bot mijoz guruhiga hech narsa yozmaydi |
| `group` | Shu guruhga **istalgan** xodim yozgan xabar | **Guruhning o'ziga** alohida (reply'siz) xabar: shu guruhning barcha menejerlari belgilanadi -- `@menejer1 @menejer2` + `GROUP_ALERT_TEXT` |

`group` rejimida: guruhga menejer biriktirilmagan bo'lsa (yoki bot guruhga yoza olmasa)
ogohlantirish guruhga emas, ichki guruhning "Javobsiz savollar" topigiga tushadi; bir marta
belgilangach, xodim javob yozmaguncha keyingi belgilash `GROUP_ALERT_REPEAT_MINUTES` (60)
daqiqadan oldin bo'lmaydi.

**Xodim** = ichki guruh a'zosi yoki shu guruhga biriktirilgan menejer.
**Guruh menejeri** ikki yo'l bilan biriktiriladi:

- mijoz guruhida xodimni admin qilib, unvoniga (custom title) "Menejer" deb yozing
  (`GROUP_MANAGER_TITLE_KEYWORDS`; u ichki guruhda ham bo'lishi kerak; faqat supergroup'larda);
- yoki ichki guruhda: `/menejer_qosh <guruh> @username [ism]`.

### Ichki guruhdagi buyruqlar

| Buyruq | Vazifasi |
|---|---|
| `/hisobot [bugun \| kecha \| YYYY-MM-DD]` | Statistika hisobotini (HTML fayl) shu yerga yuboradi |
| `/guruhlar [qidiruv]` | Kuzatilayotgan guruhlar (ID'lari bilan) |
| `/guruh_ochir <guruh>` / `/guruh_yoq <guruh>` | Guruhni kuzatuvdan chiqarish / qaytarish |
| `/menejerlar [guruh]` | Guruh(lar)ga biriktirilgan menejerlar |
| `/menejer_qosh <guruh> @username [ism]` | Menejerni qo'lda biriktirish |
| `/menejer_ochir <guruh> @username` | Qo'lda biriktirilgan menejerni olib tashlash |

`<guruh>` -- guruh ID'si yoki nomining bir qismi. Guruh birinchi xabari kelganda ro'yxatga
o'zi tushadi.

### Kunlik hisobot

Har kuni **00:00** da (`REPORT_TIMEZONE=Asia/Tashkent`) o'tgan 24 soat uchun
`reports/stats_YYYY-MM-DD.html` yoziladi: **bitta oddiy jadval, toifalarga bo'lingan** --
har bir toifa nomi va soni, ostida shu kungi shikoyatlar ("Ijobiy Fikrlar" ostida ijobiy
fikrlar): vaqt, guruh, mijoz, xabar. Karta va diagrammalar yo'q (2026-10-13).
`DAILY_REPORT_CHAT_ID` berilsa fayl shu chatga ham yuboriladi, ostida sonlar bilan qisqa
xulosa (bo'sh kun yuborilmaydi).
Bot yarim tunda o'chiq bo'lgan bo'lsa, ishga tushganda kechagi hisobotni tuzib oladi.

Qo'lda: `.venv\Scripts\python.exe -m app.daily_report --date 2026-10-11 [--send]`.

### Ishga tushirish

```
.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull gemma4:12b                      # lokal model (GROUP_AI_PROVIDER=ollama)
.venv\Scripts\python.exe -m app.bot         # bot: kuzatish, eslatmalar, kunlik hisobot
.venv\Scripts\python.exe -m pytest -q       # testlar (haqiqiy model/Telegram chaqirilmaydi)
```

`.env`dagi kerakli qiymatlar: `BOT_TOKEN`, `FEEDBACK_HUB_GROUP_ID`, `GROUP_AI_PROVIDER`,
`GROUP_AI_MODEL`, (OpenAI ishlatilsa) `OPENAI_API_KEY` -- to'liq ro'yxat `.env.example`da.

Ishlashi uchun kerak:

- Bot **har bir mijoz guruhida admin** bo'lishi kerak (yoki @BotFather -> `/setprivacy` ->
  Disable) -- aks holda Telegram botga oddiy xabarlarni ko'rsatmaydi.
- Bot ichki guruhda ham admin bo'lishi kerak ("Mavzularni boshqarish" huquqi bilan).
- `dm` rejimida xodim eslatmani shaxsiy olishi uchun botga bir marta `/start` bosgan
  bo'lishi kerak; bosmagan bo'lsa eslatma ichki guruhga, uni belgilab yuboriladi.
- Bot 20 MB dan katta faylni yuklab ololmaydi; `GROUP_STT_MAX_DURATION_SECONDS` dan uzun
  yozuvlar o'tkazib yuboriladi.

### Modellar va o'lchovlar

- **Tasniflash** (`scripts/eval_group_ai.py --provider ... --model ...`, 158 ta belgilangan
  misol). 2026-10-12: gemma4:12b -- asosiy 105 misoldan 103 tasi, toifa 53 tadan 52;
  gpt-4o-mini -- 105 dan 91, toifa 53 tadan 46 (javob kutadigan savolni ko'proq o'tkazib
  yuboradi, yetkazib berish vaqtini "javob beraman" va'dasi deb oladi). 2026-10-10:
  gpt-5.4-mini 105/105, gemma4:e4b 102, qwen3.5:9b 89.
- **Nutq -> matn**, o'zbekcha (20 ta ochiq FLEURS yozuvi, so'z xatosi): lokal
  `islomov/rubaistt_v2_medium` -- 18%, OpenAI `gpt-4o-transcribe` -- 35%, OpenAI `whisper-1`
  -- 98% (o'zbekchani tanimaydi). Shu sabab standart: `GROUP_STT_PROVIDER=local`.
  Lokal model call-center STT loyihasining `~/.cache/faster-whisper-ct2/` dagi nusxasidan
  olinadi; ishonchli ruscha nutq `GROUP_STT_RU_MODEL` (large-v3-turbo) orqali; videokarta
  uchun cuBLAS/cuDNN DLL'lari `cuda_runtime/` papkasida.
- **Rasm**: gemma4:12b bitta rasmni ~2 soniyada tahlil qiladi.
- Model javob bermasa yoki sozlanmagan bo'lsa -- oddiy kalit-so'zlar; xulosaga ishonch
  `GROUP_AI_MIN_CONFIDENCE` foizdan past bo'lsa xabar tashlanmaydi.

## 5. Ixtiyoriy: Nginx reverse proxy + SSL

Agar API tashqi internetdan (Odoo boshqa serverda bo'lsa) ochiq bo'lishi
kerak bo'lsa, uni to'g'ridan-to'g'ri ochiq qoldirmang — Nginx + SSL orqali
yoping:

```nginx
server {
    listen 443 ssl;
    server_name api.sizningdomeningiz.uz;

    ssl_certificate     /etc/letsencrypt/live/api.sizningdomeningiz.uz/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.sizningdomeningiz.uz/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

SSL sertifikat uchun Certbot: `sudo certbot --nginx -d api.sizningdomeningiz.uz`

Shuningdek, `ufw` orqali faqat 443/80 portlarini oching, 8000-portni
tashqariga yopiq qoldiring:

```bash
sudo ufw allow 22
sudo ufw allow 80
sudo ufw allow 443
sudo ufw enable
```

## 6. Muhim eslatmalar

- **PDF fayllar** `data/pdfs/`da saqlanadi va tegishli buyurtma 30 kundan keyin
  (sinxronlangan bo'lsa) bazadan tozalanganda diskdan ham avtomatik o'chiriladi
  (`background_maintenance_loop`).
- **`ORDER_EXPIRATION_HOURS`** — FastAPI background task har soatda
  (`EXPIRATION_CHECK_INTERVAL_SECONDS`) eskirgan `pending` yozuvlarni
  `expired`ga o'tkazadi.
- **Backup**: PostgreSQL ishlatilsa, `pg_dump` orqali kunlik backup
  sozlashni tavsiya qilamiz (`cron` + systemd timer).
- Servislarni yangilashda: kodni yangilang → `sudo systemctl restart
  fastapi.service bot.service userbot.service`.
