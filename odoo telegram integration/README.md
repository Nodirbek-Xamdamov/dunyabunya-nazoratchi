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

## 4.5. Mijoz guruhlari monitoringi

Bot mijozlar bor guruhlarda (~200 ta) **faqat o'qiydi**: hech narsa yozmaydi, reaksiya
qo'ymaydi, xabarlarni hech qayerga uzatmaydi. Hamma xabar SQLite bazaga yoziladi, model ularni
tasniflaydi, "5 daqiqa qoidasi" tekshiriladi va har kuni `REPORT_TIME` da (Asia/Tashkent;
ishlab chiqarishda 00:00, sinov davrida 11:00) Excel hisobot hisobot guruhining "General"
topigiga yuboriladi.

```
guruh xabari -> group_monitor (bazaga yozadi, modelni kutmaydi)
                    |
        messages (pending) --> monitor_classifier (fon: guruh bo'yicha paketlab tasniflaydi)
                    |                    |
                    |          classifications, manager_checks (pending)
                    |                    |
              monitor_sla (har daqiqa: answered / missed)
                    |
        monitor_report (00:00: sonlar SQL bilan, xulosa model bilan -> hisobot_YYYY-MM-DD.xlsx)
```

| Fayl | Vazifasi |
|---|---|
| `app/monitor_db.py` | Monitoring bazasi: jadvallar, barcha so'rovlar, zaxira nusxa (baza bilan ishlaydigan yagona modul) |
| `app/bot/handlers/group_monitor.py` | Guruh xabarlari va tahrirlarini bazaga yozadi |
| `app/monitor_classifier.py` | Fon tasniflovchi: prompt, paketlash, javobni tekshirish, qayta urinish |
| `app/monitor_sla.py` | Guruh adminlari/menejeri, "5 daqiqa qoidasi" |
| `app/monitor_report.py` | Kunlik Excel hisobot, yuborish, yarim tun vazifasi, zaxira |
| `app/bot/handlers/monitor_admin.py` | `/test_report` |
| `app/llm_client.py` | Modelga so'rov (lokal Ollama yoki OpenAI) |

### Baza (SQLite, `DB_PATH`)

WAL rejimi, `busy_timeout`, `foreign_keys` yoqilgan. Jadvallar bot ishga tushganda o'zi yaratiladi.

| Jadval | Nima saqlanadi |
|---|---|
| `groups` | Guruh, adminlari, menejeri (`manager_user_id`, `manager_username`) |
| `messages` | Har bir xabar: kim, matn/izoh, media turi, xodimmi, vaqt (UTC), mahalliy sana, tahrir; `UNIQUE(chat_id, message_id)` |
| `classifications` | Mijoz xabarining tasnifi: kompaniyaga oidmi, toifa, sentiment, bir jumlalik mazmun, menejer kerakmi, model |
| `manager_checks` | 5 daqiqa qoidasi tekshiruvlari: `pending` / `answered` / `missed` |
| `daily_stats` | Kun bo'yicha sonlar: 8 toifa, jami, filtrlangan, javobsiz |
| `daily_summaries` | Kun bo'yicha matnli xulosalar va hisobot yuborilgan vaqt |

`daily_stats` va `daily_summaries` kun sayin to'planadi -- keyingi oylik taqqoslash ("shikoyatlar
kamaydimi") shulardan qilinadi (oylik hisobotning o'zi hali yozilmagan). PostgreSQL'ga o'tish
uchun faqat `app/monitor_db.py`dagi `_database_url()` va SQLite PRAGMA'lari o'zgaradi.
Botning asosiy (Odoo buyurtmalari) bazasi -- alohida, PostgreSQL (`DATABASE_URL`).

### Xabar yig'ish

- Saqlanadi: matn, media izohi (caption), tahrirlangan xabar (yangilanadi); rasm/ovoz/video uchun
  `media_type` va Telegram fayl ID'si. Ovozli xabar, audio va video (ovozi) `GROUP_STT_ENABLED=true`
  bo'lsa OpenAI transkripsiya API'si orqali matnga aylantiriladi (`OPENAI_STT_MODEL`,
  `messages.transcript`) va shu matn bo'yicha tasniflanadi. Rasmning o'zi tahlil qilinmaydi.
- Saqlanmaydi: xizmat xabarlari, botlarning xabarlari, hisobot guruhi va
  `GROUP_MONITOR_EXCLUDED_GROUP_IDS`dagi guruhlar.
- Xodim (`is_staff`) = guruh admini yoki egasi. Xodim xabarlari kontekst bo'ladi, statistikaga
  faqat mijoz xabarlari kiradi.
- Bot guruhdagi oddiy xabarlarni ko'rishi uchun shu guruhda **admin** bo'lishi kerak
  (yoki @BotFather -> `/setprivacy` -> Disable).

### Tasniflash

Har bir mijoz xabari uchun: kompaniyaga oidmi; toifa; sentiment (Shikoyat / Ijobiy / Neytral);
bir jumlalik o'zbekcha mazmun; menejer javobi kerakmi. Toifalar:

Dastavka va Logistika · Mahsulot Sifati va Nuqsonlar · Komplektatsiya va Hajm · Xodimlar va
Xizmat Ko'rsatish · Narx va To'lovlar · Qaytarish va Kafolat · Ijobiy Fikrlar · Boshqa

- Faqat stiker/emoji yoki matnsiz xabar modelga yuborilmaydi (qoida bilan "aloqasiz").
- Xabarlar guruh bo'yicha paketlanadi (`CLASSIFY_BATCH_SIZE`) va suhbat konteksti bilan beriladi.
- Model JSON qaytaradi, javob tekshiriladi; xato bo'lsa qayta so'raladi. Har xabar bir marta
  tasniflanadi; `CLASSIFY_MAX_ATTEMPTS` martadan keyin ham bo'lmasa "failed" bo'ladi.
- Model: `GROUP_AI_PROVIDER` / `GROUP_AI_MODEL` (hozir lokal `gemma4:12b`, Ollama).

### 5 daqiqa qoidasi

- Menejer = guruh adminlari orasida unvoni (custom title) `MANAGER_TITLES`dagi so'zni o'z ichiga
  olgan odam. Kuniga bir marta va admin o'zgarganda yangilanadi.
- Mijoz kompaniyaga murojaat qilsa (savol, buyurtma, shikoyat) tekshiruv ochiladi. `SLA_MINUTES`
  daqiqa ichida menejer shu guruhga yozsa -- `answered`, yozmasa -- `missed` (hisobotga tushadi).
- Ketma-ket kelgan so'rovlar bitta tekshiruvga birlashadi (muddat birinchi xabardan).
- Guruhda menejer bo'lmasa: istalgan adminning javobi hisobga olinadi, javobsiz qolgani
  hisobotda "menejer yo'q" bilan chiqadi.
- `SLA_WORK_HOURS=09:00-19:00` qo'yilsa ish vaqtidan tashqaridagi so'rovlar tekshirilmaydi
  (hozir bo'sh = 24/7).

### Kunlik Excel hisobot

Har kuni `REPORT_TIME` da undan oldingi 24 soat uchun `reports/hisobot_YYYY-MM-DD.xlsx` tuziladi
va `REPORT_CHAT_ID` guruhining General topigiga yuboriladi. `REPORT_TIME=00:00` -- o'tgan kalendar
kuni; `11:00` (sinov) -- kechagi 11:00 dan bugungi 11:00 gacha, fayl nomidagi sana davr boshlangan
kun. Bot o'chiq turgan bo'lsa, ishga tushganda yuborilmagan kunlarni yuboradi. **Raqamlarni faqat
SQL/kod hisoblaydi**; model faqat matnli xulosa yozadi. "Boshqa" toifasi uchun xabarlar birma-bir
yozilmaydi -- gap umuman nimalar haqida ketgani aytiladi.

- 1-varaq "Kunlik hisobot": toifalar jadvali (Toifa | Jami | Shikoyat | Ijobiy | Neytral | Ulush),
  "Jami kompaniyaga oid", "Kompaniyaga aloqasiz (filtrlangan)", har toifa bo'yicha xulosa,
  umumiy xulosa (oldingi kun bilan shikoyatlar solishtirmasi va javobsiz so'rovlar soni bilan).
- 2-varaq "Javobsiz so'rovlar": Vaqt | Guruh | Mijoz | So'rov mazmuni | Menejer.
- Yuborishda xato bo'lsa 3 marta uriniladi, bo'lmasa `ADMIN_CHAT_ID`ga xabar boradi.
- `/test_report [bugun|kecha|YYYY-MM-DD]` -- hisobotni darhol yuboradi (faqat adminlar:
  `ADMIN_USER_IDS`, `ADMIN_CHAT_ID`, hisobot guruhining adminlari; shaxsiy chatda yoki hisobot
  guruhida).

### Zaxira va qayta ishga tushish

- Har kuni hisobotdan keyin baza nusxasi `backups/monitor_YYYY-MM-DD.db` ga olinadi (SQLite
  backup API), oxirgi `BACKUP_KEEP` (14) tasi saqlanadi.
- Bot qulab tushsa avtomatik qayta ishga tushishi uchun (Windows, Task Scheduler) tayyor
  skript bor: `scripts/bot_watchdog.ps1` (bot ishlamayotgan bo'lsa ishga tushiradi). Uni har
  daqiqa va kompyuter yonganda ishlaydigan qilib bir marta ro'yxatdan o'tkazing (PowerShell):

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument '-NoProfile -ExecutionPolicy Bypass -File "D:\Demos\odoo telegram integration\scripts\bot_watchdog.ps1"'
$triggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)))
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName "odoo_telegram_bot_watchdog" -Action $action -Trigger $triggers -Principal $principal
```

### Ishga tushirish va sinash

```
.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull gemma4:12b                          # lokal model (GROUP_AI_PROVIDER=ollama)
.venv\Scripts\python.exe -m app.bot             # bot: yig'ish, tasniflash, 5 daqiqa qoidasi, hisobot
.venv\Scripts\python.exe -m pytest -q           # testlar (haqiqiy model/Telegram chaqirilmaydi)
.venv\Scripts\python.exe scripts\test_monitor.py  # haqiqiy model bilan, vaqtinchalik bazada:
                                                 # 30 ta namuna xabar, 1 daqiqalik muddat, Excel
```

Sozlamalar `.env`da (`.env.example`ga qarang): `BOT_TOKEN`, `DB_PATH`, `REPORT_CHAT_ID`,
`TIMEZONE`, `MANAGER_TITLES`, `SLA_MINUTES`, `GROUP_AI_PROVIDER`, `GROUP_AI_MODEL`,
`OPENAI_API_KEY` (faqat OpenAI tanlansa). Token va kalitlar logga yozilmaydi.

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
