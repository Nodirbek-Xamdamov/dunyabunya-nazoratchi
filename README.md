# telegram to odoo attachment bot

<!-- qisqacha -->
> **Qisqacha.** Telegram guruhiga tashlangan hujjat rasmi yoki PDF'ini Odoo 18'dagi mavjud hujjatning chatter'iga biriktiradi (QR/shtrix-kod, PDF matni yoki AI orqali aniqlaydi). Odoo'da boshqa hech narsa o'zgarmaydi.
>
> **Texnologiyalar:** Python, Aiogram 3, Odoo 18 XML-RPC, OpenAI, SQLite
>
> **Ishga tushirish:** `python -m docbot run` (sozlamalar `.env` va `config/` da)

Telegram'ga tashlangan hujjat rasmi yoki PDF'ini Odoo 18'dagi **mavjud** hujjatning **chatter'iga biriktiradi**. Boshqa hech narsa qilmaydi:
Odoo'da yangi yozuv yaratilmaydi, maydon yoki holat o'zgarmaydi. Faqat fayl va "Telegram: yuboruvchi" izohi (Note) qo'shiladi.

Bot guruhdagi (va shaxsiy chatdagi) **har bir rasm va PDF'ni** tekshiradi, kim tashlaganidan qat'i nazar. Hujjat quyidagi tartibda aniqlanadi:

1. **QR / shtrix-kod.** Chop etilgan hujjatlardagi kod o'qiladi. Sotuv va xarid buyurtmalarida QR ichida Odoo portal havolasi bor
   (yozuv id + maxfiy token), ombor cheki va yo'llanmalarda hujjat nomi (`OD/INT/01514`, `S82419`).
2. **PDF matni.** QR bo'lmagan PDF'da hujjat raqami matn qatlamidan olinadi.
3. **AI (OpenAI).** Kod o'qilmasa, rasmni `gpt-5.4-mini` ko'radi. AI avval rasm bizning chop etilgan hujjatlarga necha foiz
   o'xshashini baholaydi, keyin raqam, xaridor, telefon, menejer va summani o'qiydi. Topilgan yozuv summa, xaridor, telefon va menejer
   bo'yicha solishtiriladi: kamida bittasi mos kelishi va hech biri zid bo'lmasligi kerak.

Natija:

| Holat | Bot nima qiladi |
|---|---|
| Hujjat topildi | chatter'ga yuklaydi va rasmga 👍 qo'yadi, boshqa hech narsa yozmaydi |
| Bizning hujjat (QR bizniki, yoki o'xshashlik ≥ `ai.min_similarity` (80%) va dunyabunya logotipi ko'rinadi), lekin qaysi hujjat ekani aniqlanmadi | faqat shu rasmga reply qiladi: "📷 Hujjat aniqlanmadi. Rasmni boshqatdan tiniqroq qilib tashlang (QR kod aniq ko'rinsin)" (`notify.unclear_text`) |
| Hujjat emas (odamlar, mahsulot, boshqa narsa) | jimgina e'tiborsiz qoladi: reaksiya ham, javob ham yo'q, lokal nusxa darhol o'chiriladi |
| Xato (masalan, Odoo soatlab ishlamadi) | 👎, adminga xabar |

- **Qayta tashlangan hujjat ham biriktiriladi:** bir rasm yangi xabarda qayta yuborilsa, yana yuklanadi. Faqat bitta xabar
  (masalan, uzilishdan keyingi qayta urinishda) ikki marta yuklanmaydi. Buning uchun attachment'ning `description` maydoniga
  `telegram-docbot job <id>` yoziladi.
- **`/start`**: bot "Assalomu alaykum, xush kelibsiz!" deb yozadi, boshqa hech narsa. Ro'yxatdan o'tish yo'q.
- AI o'xshashlik bahosi sinovi (2026-09-25): chatter skrinshoti 92%, xira qog'oz simulyatsiyasi 96%, aniq skrinshot 100%,
  hujjat bo'lmagan rasm 0%.
- Kod topilmagan har bir rasm OpenAI'ga yuboriladi. Bu guruhdagi shaxsiy suratlarga ham tegishli, shuning uchun AI xarajatini va
  maxfiylikni hisobga oling. Kunlik chegara: `ai.daily_limit`. Chegara oshsa, kodsiz rasmlar e'tiborsiz qoladi.
- Matnli savollar va tugmalar kerak bo'lsa: `notify.messages: true` (odatda o'chiq).

**Ko'p sahifali hujjat** (albom yoki ketma-ket yuborilgan rasmlar). Har bir sahifa biriktiriladi:
1-sahifa sarlavhadagi raqam bilan, oxirgi sahifa QR kod bilan topiladi. O'rtadagi sahifa quyidagi tartibda tekshiriladi:
o'ng yuqori burchakdagi kichik hujjat raqami → pastdagi sahifa raqami (3 → 2 → 1 zanjiri, yoki QR sahifaga) → tovar
qatorlarining davomi (34-qatordan keyin 35). Guruh = bitta albom, yoki bitta odamning bir chatda `pages.window_seconds` (120 s)
ichidagi rasmlari. Ko'p sahifali guruhdagi QR sahifaning ham sahifa raqami o'qiladi, shunda zanjir QR sahifaga ulanadi.
Qoidalar: 1-sahifa (sahifa 1 yoki 1-qatordan boshlanadi) yangi hujjat, oldingisiga ulanmaydi; ikki sahifa orasida boshqa hujjatning
1-sahifasi bo'lsa, ular bog'lanmaydi; albomdagi oddiy surat hujjatga qo'shilmaydi; topilmagan o'rta sahifaga javob yozilmaydi; albomga
ko'pi bilan bitta "tiniqroq tashlang". Fayl nomida sahifa raqami bo'ladi: `S87295_2-bet_20260929_110340.jpg`.

**Tushib qolgan sahifa.** QR kodli oxirgi sahifa boshqa rasmlar o'qilib bo'lishini kutadi, keyin shu hujjatga biriktirilgan
sahifalarni tekshiradi. Masalan, 1–20, 21–40, 61–80 va QR bilan 81–100 qatorlar tashlansa, 41–60-qatorli sahifa yo'q: QR
sahifaga javob yoziladi — "⚠️ S87295 hujjatida sahifa yetishmayapti: 41–60-qatorlar. Shu sahifani ham tiniq qilib tashlang."
Sahifa raqamlari o'qilgan bo'lsa, ular ustun: "3-bet (41–60-qatorlar)". Tashlangan sahifalar baribir biriktiriladi. Joyi
aniqlanmagan sahifa bo'lsa (u yo'qolgan sahifa bo'lishi mumkin), javob yozilmaydi. Matn: `notify.missing_pages_text`
(bo'sh = tekshirilmaydi). Hujjat = bitta odamning bitta yozuvga biriktirilgan, oralig'i 120 s dan oshmagan rasmlari.

**Burilgan rasmlar.** Yotiq yoki teskari surat avval tik holga keltiriladi va Odoo'ga **tik holda** yuklanadi (xom holida emas).
Burilishni QR kod aniqlaydi. QR bo'lmasa, AI aytadi (`rotation`). AI yo'nalishni ba'zan adashtiradi, shuning uchun bot tik qilgach
yana bir bor tekshiradi va tuzatishlarni qo'shib boradi (eng ko'pi 3 ta AI so'rov). Haqiqiy namuna 90/180/270° burilganda uchalasi ham tik bo'ldi.

**Tekshiruv.** AI yo'lida xaridor (nomi yoki telefoni) mos kelsa, hujjat biriktiriladi: summa keyin o'zgargan bo'lishi mumkin, qog'ozdagi
telefon esa yetkazish raqami bo'lishi mumkin. Xaridor zid bo'lsa (raqam noto'g'ri o'qilgan), biriktirilmaydi. Faqat raqam o'qilgan bo'lsa,
o'xshashlik >= `ai.trust_number_from` (90%) va logotip ko'rinsa, raqam yetadi.

**Pauza / tayyor.** `/pauza` (admin, botga shaxsiy chatda) yoki `docbot groups pause`: guruhlarga umuman tegilmaydi, na xabar olinadi,
na yoziladi. Shaxsiy chatdagi fayllar esa ishlanaveradi. `/tayyor` yoki `docbot groups live`: guruhlar ishga tushadi, faqat **shu paytdan
keyingi** xabarlar olinadi. Ishlab turgan bot o'zgarishni darhol ko'radi. `docbot requeue --failed --review`: tuzatishdan keyin qayta ishlash.

**Guruhda.** Bot guruhga **admin qilib** qo'shiladi, aks holda Telegram unga rasmlarni ko'rsatmaydi. ADMIN_IDS dagi odam qo'shsa,
guruh avtomatik ulanadi (`data/chats.json`) va adminga xabar keladi. Boshqa odam qo'shsa, adminga "✅ Ruxsat berish" tugmali xabar
keladi. Zaxira yo'l: admin guruhda `/start` yozadi. Guruhdagi matnli suhbatga bot aralashmaydi.
Bot bir nechta guruhda ishlaydi, har biri `data/chats.json` da. **Faqat yangi xabarlar:** guruh ulangan vaqtdan oldin
yozilgan xabarlarga bot tegmaydi (Telegram botga tarixni bermaydi; `ChatRegistry.is_new` qo'shimcha himoya). Bot qisqa to'xtab
qolsa, shu orada kelgan yangi rasmlarni (Telegram 24 soatgacha saqlaydi) qayta yoqilganda ishlaydi.

Tezlik (2026-09-25, test Odoo): QR bilan ~4 soniya (yuklab olish + QR ~1 s, Odoo'da qidirish ~0.5 s, chatter'ga yuklash ~2.5 s),
AI bilan ~8 soniya.

## Holat (2026-09-25)

2026-09-28 dan **production**: `jonibekodoo-dunyabunya-prod-prod-26530779.dev.odoo.com`, Odoo foydalanuvchisi `db_bot@dunyabunya.com`.
24/7 watchdog bilan ishlaydi. Guruh "Changan DB nakladnoy" 16:27 (Toshkent) dan boshlab faqat yangi xabarlar uchun yoqilgan.

## O'rnatish (Windows, PowerShell)

```powershell
cd D:\Production\telegram-odoo-bot
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"

Copy-Item .env.example .env          # keyin .env'ni to'ldiring
.\.venv\Scripts\python -m docbot check-config
.\.venv\Scripts\python -m pytest
```

## Ishga tushirish va 24/7

```powershell
.\.venv\Scripts\python -m docbot run                                        # oldingi oynada (Ctrl+C - to'xtatish)
powershell -ExecutionPolicy Bypass -File scripts\install_watchdog.ps1 -S4U   # 24/7: Task Scheduler + watchdog
```

- `scripts\watchdog.ps1` har daqiqada tekshiradi: `data\docbot.pid` dagi jarayon tirikmi, u haqiqatan `docbot run`mi va `data\heartbeat`
  oxirgi 3 daqiqada yangilanganmi. Aks holda botni (qayta) ishga tushiradi. `-S4U` bilan vazifa hech kim tizimga kirmagan bo'lsa ham
  va server qayta yoqilgandan keyin ham ishlaydi. Log: `data\logs\watchdog.log`.
- Bot bir nusxada ishlaydi (`data\docbot.lock`): ikkinchi nusxa darhol chiqib ketadi.
- Botni to'xtatish (watchdog uni bir daqiqada qayta yoqadi, butunlay to'xtatish uchun avval vazifani o'chiring):
  `Unregister-ScheduledTask -TaskName telegram_odoo_bot_watchdog -Confirm:$false`, keyin
  `Stop-Process -Id (Get-Content data\docbot.pid)`.
- Odoo, OpenAI yoki Telegram vaqtincha ishlamasa, fayllar navbatda kutadi va backoff bilan qayta urinadi (~4 soat).
  Urinishlar tugasa, yuboruvchi va adminlarga xabar boradi.

`python -m docbot ...` buyruqlari:

| Buyruq | Nima qiladi |
|---|---|
| `run` | botni ishga tushiradi (migratsiyalar avtomatik) |
| `status` | nechta fayl qaysi holatda, javob kutayotgan savollar va xatolar |
| `check-config [--strict]` | konfiguratsiyani tekshiradi (tarmoqsiz) |
| `migrate` | DB sxemasini oxirgi versiyaga yangilaydi |

Telegram'da admin uchun `/status`.

## Konfiguratsiya

| Fayl | Ichida nima | Git'ga |
|---|---|---|
| `.env` | sirlar: `BOT_TOKEN`, Odoo URL/DB/user/API key, `ALLOWED_CHAT_IDS` (shaxsiy chat id = foydalanuvchi id), `ADMIN_IDS`, `OPENAI_API_KEY` | **tushmaydi** |
| `config/settings.yaml` | navbat, fayllar, PDF, AI (model, kunlik limit), Odoo timeout, xabarlar, log | tushadi |
| `config/documents.yaml` | hujjat turlari: Odoo modeli, QR/shtrix-kod qoliplari, raqam ko'rinishi, AI tekshiruv maydonlari | tushadi |

**Yangi bo'lim qo'shish** (masalan, hisob-faktura) kod yozishni talab qilmaydi. Buning uchun:

1. Odoo'da o'sha hujjatning chop etish shabloniga QR qo'shiladi. Eng yaxshisi, ichida faqat hujjat nomi bo'lgan QR:
   `qweb_reports` modulidagi `get_qr_search_url()` shuni qiladi.
2. `documents.yaml` ga shunday blok qo'shiladi:
   ```yaml
   - key: invoice
     title: Hisob-faktura
     model: account.move
     number: 'INV/\d{4}/\d+'
     codes:
       - '^(?P<name>INV/\d{4}/\d+)$'
   ```
3. Bot qayta ishga tushiriladi.

Qog'oz suratlarida QR yaxshi o'qilishi uchun QR ichidagi matn qisqa (faqat hujjat nomi) yoki QR kattaroq bo'lishi kerak. Simulyatsiyada
qisqa QR xira suratda ham 15/15 o'qildi, 111 belgili portal havolasi esa 0/15. Hozirgi QR o'lchami 90 px. Sozlama `.xb-qr-img` da.

## Navbat qanday ishlaydi

Har bir fayl = bitta `jobs` qatori. Jadval ayni paytda navbat ham, shuning uchun restartdan keyin hech narsa yo'qolmaydi.

```
received ─bizning kod/raqam─► qr_ok ─Odoo'da topildi─► ready ─chatter'ga yuklandi─► attached (👍)
   │                               │ topilmadi              ▲
   └─kod yo'q──► ai_processing ─raqam topildi + tekshiruv o'tdi
                    │ bizning hujjat, lekin aniqlanmadi ──► needs_review ("tiniqroq tashlang" reply)
                    └ hujjat emas (o'xshashlik < 70%) ──► ignored (jim, fayl o'chiriladi)
(istalgan faol bosqich) ─urinishlar tugadi─► failed (👎, adminga xabar)
```

- **Har bir xabar bir marta, qayta tashlansa yana.** Bir rasm yangi xabarda qayta yuborilsa, yana biriktiriladi. Bitta job'ning
  qayta urinishi esa attachment `description` idagi `telegram-docbot job <id>` belgisi bo'yicha o'z yuklamasini topadi, shuning uchun
  ikkinchi nusxa yoki ikkinchi chatter xabari paydo bo'lmaydi.
- **Claim, lease, fencing, retry, audit.** (a) bosqichdagidek: `pipeline/worker.py`, `db/repositories/jobs.py`.
- **Xabarnomalar outbox orqali.** Job `attached / needs_review / failed` bo'lganda `notify_pending` belgilanadi. Notifier
  xabarni yuborgach, belgini o'chiradi. Telegram ishlamay qolsa, xabar kechikadi, lekin yo'qolmaydi.
- **AI javobi saqlanadi.** Odoo xatosidan keyingi qayta urinishda AI qayta chaqirilmaydi (ikki marta pul to'lanmaydi).
  AI kunlik limiti `ai.daily_limit`. Limit oshsa, kodsiz rasmlar e'tiborsiz qoladi (bizning QR bo'lsa, "tiniqroq tashlang").
- **Lokal nusxalar** (`data/images/`) `files.keep_days` kundan keyin o'chiriladi, Odoo'dagi nusxa qoladi.

## Loyiha tuzilmasi

```
config/                 settings.yaml, documents.yaml
scripts/                watchdog.ps1, install_watchdog.ps1
src/docbot/
  app.py                ishlayotgan bot: polling + worker'lar + notifier + heartbeat + tozalash, bitta nusxa qulfi
  config.py, cli.py     konfiguratsiya, python -m docbot ...
  documents.py          kod matni / hujjat raqami -> qaysi Odoo yozuvi (RecordRef)
  codes.py              QR/shtrix-kod o'qish (zxing-cpp, qayta urinishlar), PDF (pypdfium2)
  odoo.py               XML-RPC klient: qidirish, chatter'ga idempotent biriktirish
  ai.py, verify.py      OpenAI Responses API (JSON schema) va natijani Odoo bilan solishtirish
  pipeline/             states, worker (navbat), processor (bosqichlar), finder, files, review (odam javoblari)
  telegram/             handlers, notifier (outbox), files (yuklab olish)
  db/                   modellar, Alembic migratsiyalari, repositories (jobs, audit)
tests/                  238 ta test
```

## Testlar

```powershell
.\.venv\Scripts\python -m pytest
```

Qamrov: konfiguratsiya, navbat va worker'lar ((a) bosqich), hujjat qoliplari (haqiqiy `documents.yaml`), QR/shtrix-kod/PDF o'qish
(sintetik rasmlar), AI klienti (soxta HTTP server), Odoo klienti (soxta XML-RPC server: kompaniyalar, idempotentlik, xatolar),
AI tekshiruvi, butun oqim (QR, shtrix-kod, PDF matni, AI, savol-javob, dublikat, Odoo uzilishi, xato xabarlari, AI limiti).

## Keyingi qadamlar / ochiq masalalar

- Odoo'da bot uchun alohida foydalanuvchi ochish. Hozir chatter'da muallif sifatida `admin@dunyabunya.com` egasi ko'rinadi.
  Bu foydalanuvchiga hujjatlarni o'qish va chatter'ga yozish huquqi kerak.
- Kalitlarni yangilash (chatda ko'ringan edi): bot tokeni, Odoo API key, OpenAI key.
- Haqiqiy qog'oz suratlari bilan sinash va QR'ni qisqartirish yoki kattalashtirish (Odoo `qweb_reports`).
- Prod Odoo'ga o'tishda `.env` dagi `ODOO_URL/ODOO_DB`. Test bazada chop etilgan hujjatlarning QR tokeni prod bilan mos kelmaydi:
  bunday hujjatlar AI yo'liga o'tadi.
- Aniqlanmay qolgan (tiniqroq qayta yuborilgan) rasmlar `needs_review` da turib qoladi (`status` ko'rsatadi). Kerak bo'lsa,
  ma'lum kundan keyin avtomatik yopish qo'shiladi.
- Kod topilmagan har bir guruh surati OpenAI'ga ketadi: xarajat va maxfiylik. Kerak bo'lsa, arzon oldindan filtr (masalan, oq qog'oz
  va jadval borligini tekshirish) qo'shiladi.
