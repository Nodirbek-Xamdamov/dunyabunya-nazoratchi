# dunyabunya CEO agent

<!-- qisqacha -->
> **Qisqacha.** Rahbar (CEO) uchun Telegram yordamchisi: Odoo'ga ulangan AI agent. Oddiy gap, ovozli xabar, rasm yoki hujjat bilan so'raladi — bot ma'lumotni Odoo'dan topadi, tahlil qiladi, hisobot faylini tayyorlaydi, xodimga vazifa beradi va eslatma qo'yadi.
>
> **Texnologiyalar:** Python, Telegram bot, Odoo API, PostgreSQL, Claude + OpenAI
>
> **Ishga tushirish:** `python -m app.main` (sozlamalar `.env` da, namuna: `.env.example`)

Rahbar uchun Telegram yordamchisi: kompaniyaning Odoo tizimiga ulangan AI agent. Rahbar oddiy gap
(yoki ovozli xabar, rasm, hujjat) bilan so'raydi — bot kerakli ma'lumotni Odoo'dan o'zi topadi,
tahlil qiladi, hisobot faylini tayyorlaydi, xodimga vazifa beradi yoki eslatma qo'yadi.

Bu hujjat botni o'rnatadigan va kuzatib boradigan odam uchun.

## Nima qiladi

| Qayerda | Kim | Nima bo'ladi |
|---|---|---|
| Shaxsiy chat | CEO | To'liq yordamchi: barcha bo'limlar ma'lumoti, tahlil, fayllar, vazifa, eslatma, internet qidiruvi |
| Guruh, General mavzusi | CEO | Xuddi shu yordamchi (javob guruhga yoziladi) |
| Guruh, bo'lim mavzusi | CEO | Xabar topshiriq bo'lsa — Odoo'ga vazifa yoziladi va xodimga xabar ketadi; savol bo'lsa — yordamchi javob beradi |
| Shaxsiy chat | Menejer | Faqat o'z bo'limi ma'lumoti bo'yicha yordamchi |
| Shaxsiy chat | Xodim | `/start` bilan ro'yxatdan o'tadi, vazifalarni oladi, «Bajarildi» tugmasini bosadi |

Guruhda bot faqat CEO'larga (`.env` dagi `CEO_TELEGRAM_USERNAME`) javob beradi.

Yordamchining imkoniyatlari:

- **Istalgan savol.** Tayyor hisobotlardan tashqari, Odoo'ning istalgan modelini o'qiydi: saralab
  (`eng katta 3 ta faktura`), guruhlab (`ta'minotchilar bo'yicha xarid`), ikki davrni solishtirib
  (`sotuv nega tushdi, qaysi mijozlar hisobiga`). Notanish ma'lumotni o'zi qidirib topadi.
- **Fayllar.** Excel, Word, PDF, CSV va grafikli HTML hisobot — Telegram'ga fayl bo'lib keladi.
- **Rasm va hujjat o'qish.** Rasm, PDF, Excel (.xlsx), Word (.docx), CSV, matn.
- **Vazifa.** «Sardorga vazifa ber: ...» — Odoo'ga yozadi, xodimga ovozli xabar yuboradi;
  muddatini surish, yopish, izoh qo'shish.
- **Eslatma.** Bir martalik yoki takrorlanadigan; «har kuni 18:00 da bugungi sotuvni yubor» kabi
  rejalashtirilgan so'rov ham.
- **Xotira.** «Eslab qol: ...» — keyingi suhbatlarda ham hisobga oladi.
- **Internet.** Valyuta kursi, yangilik, qonunchilik (faqat CEO; `WEB_SEARCH_ENABLED`).
- **Boshqa.** Xat yozish, tarjima, hisob-kitob, maslahat.

Kompaniyaga oid har bir raqam faqat Odoo'dan olinadi — model raqam to'qimaydi. Pul va omborga
oid yozish amallari faqat qoralama (draft) yaratadi.

## Tuzilishi

```
Telegram ──> app/main.py (aiogram, parallel polling)
               ├─ telegram/handlers_commands.py   /yordam /status /brifing /id
               ├─ telegram/handlers_manager.py    shaxsiy chat: ro'yxatdan o'tish, CEO va menejer suhbati
               ├─ telegram/handlers.py            guruh: bo'lim mavzulari (vazifa), General (yordamchi)
               ├─ telegram/delivery.py            javob, fayllar, Excel/HTML/Matn tugmalari
               └─ telegram/attachments.py         rasm va hujjatlarni o'qish
                         │
             services/assistant.py                agent sikli (Claude): o'ylash -> tool -> javob
               ├─ services/agent_tools.py         umumiy Odoo tool'lari (o'qish, guruhlash, solishtirish, qidirish)
               ├─ services/odoo_tools.py          tayyor hisobot va eksport tool'lari
               ├─ services/task_tools.py          vazifa yaratish / yangilash
               ├─ services/reminders.py           eslatmalar va rejalashtirilgan so'rovlar
               ├─ services/agent_memory.py        eslab qolingan qaydlar, guruh tarixi
               └─ rbac.py                         rolga qarab tool va modellar
                         │
             services/odoo.py ──> Odoo (XML-RPC)
             services/llm.py  ──> OpenAI (guruh xabarini saralash, ovozni matnga o'girish)
             db/ ──> PostgreSQL (mavzular, menejerlar, suhbat tarixi, vazifa jurnali)
```

Fon ishlari (`main.py` ishga tushiradi): vazifa eslatmalari (`task_reminders.py`), kunlik xulosa
(`briefing.py`), monitoring va haftalik hisobot (`monitor.py`), xato ogohlantirishlari (`alerts.py`),
guruh xotirasi (`memory_digest.py`), eski hisobot fayllarini tozalash (`reports_cleanup.py`),
foydalanuvchi eslatmalari (`reminders.py`).

`storage/` (git'da yo'q): `reports/` — yaratilgan fayllar, `employees.json` — ro'yxatdan o'tgan
xodimlar, `agent_memory.json` — eslab qolingan qaydlar, `reminders.json` — eslatmalar,
`metrics.json` — kunlik hisoblagichlar, `memory/` — guruh yozishmalarining kunlik xulosalari.

## O'rnatish (yangi serverda)

Kerak: Windows Server, Python 3.12, PostgreSQL 17, Odoo 18 API kaliti, Telegram bot tokeni,
Anthropic va OpenAI API kalitlari.

```powershell
cd "D:\Demos\dunyabunya ceo agent"
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          # qiymatlarni to'ldiring
```

1. PostgreSQL'da baza va foydalanuvchi yarating (`DATABASE_URL` ga mos). Jadvallar bot birinchi
   marta ishga tushganda o'zi yaratiladi.
2. Bo'lim mavzularini Odoo loyihalariga bog'lang: `scripts\seed_topics.py` (yangi baza) va
   `.\.venv\Scripts\python scripts\check_topics.py --fix --create`.
3. Xodimlar katalogini to'ldiring: `.\.venv\Scripts\python scripts\seed_employees.py`.
4. 24/7 ishlashi uchun vazifalarni ro'yxatga oling (bir marta):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_watchdog_task.ps1   # bot: har daqiqada tekshiradi
powershell -ExecutionPolicy Bypass -File scripts\setup_backup_task.ps1     # baza zaxirasi: har kuni 03:00
```

Watchdog `/health` javob bermasa botni o'zi ishga tushiradi — qo'lda yoqish shart emas.

## Sozlamalar (`.env`)

To'liq ro'yxat izohlari bilan — `.env.example` da. Eng muhimlari:

| Kalit | Ma'nosi |
|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_GROUP_ID` | Bot va asosiy guruh |
| `CEO_TELEGRAM_USERNAME` | CEO'lar (vergul bilan). Faqat shular to'liq huquqqa ega |
| `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_API_KEY` | Odoo ulanishi |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Yordamchi modeli (`claude-sonnet-5`) |
| `ANTHROPIC_EFFORT` | O'ylash chuqurligi: `low` … `max` (sukut `medium`) |
| `WEB_SEARCH_ENABLED` | Internet qidiruvi (CEO uchun) |
| `OPENAI_API_KEY` | Guruh xabarini saralash va ovozni matnga o'girish |
| `ALERT_CHAT_ID` | Tizim xatolari yuboriladigan chat (`/id` bilan aniqlanadi). `0` — o'chiq |
| `DAILY_BRIEFING_TIME` | CEO'ga kunlik xulosa vaqti, masalan `08:30`. Bo'sh — yuborilmaydi |
| `CONVERSATION_RETENTION_DAYS` | Suhbat tarixini saqlash muddati. `0` — cheklanmagan |

`.env` o'zgargach botni qayta ishga tushiring.

## Kundalik ish

```powershell
powershell -ExecutionPolicy Bypass -File scripts\restart_bot.ps1    # qayta ishga tushirish (/health gacha kutadi)
Invoke-RestMethod http://127.0.0.1:8002/health                      # holat
Get-Content logs\bot.log -Tail 50                                   # so'nggi loglar
.\.venv\Scripts\python -m pytest -q                                 # testlar
.\.venv\Scripts\python scripts\agent_eval.py natija.md              # yordamchini jonli sinash (~40 savol, pullik)
```

`agent_eval.py` — prompt, tool yoki model o'zgartirilgandan keyin: savollar haqiqiy Claude va Odoo
bilan bajariladi, javoblar va tool chaqiruvlari `natija.md` ga yoziladi (botning bazasi va
`storage/` ga tegilmaydi). Natijani ko'z bilan ko'rib chiqing: raqamlar to'g'rimi, javob to'liqmi.

- **`/status`** (botga, CEO) — Telegram, Odoo, baza, zaxira nusxa, disk, bugungi savollar va token sarfi.
- **`/health`** — `200 ok` yoki `503` (polling to'xtagan/osilgan — watchdog bir daqiqada qayta yoqadi).
- **Loglar** — `logs\bot.log` (5 MB × 5 fayl). Bot yiqilgan paytdagi xato — `logs\run.err.*.log`.
- **Ogohlantirishlar** — `ALERT_CHAT_ID` berilgan bo'lsa ERROR darajasidagi xatolar va Odoo/baza
  uzilishi o'sha chatga keladi; haftada bir marta shu chatga tizim holati hisoboti ham boradi.
- **Zaxira nusxa va tiklash** — `scripts\BACKUP_RESTORE.md`.

### Yangilash va orqaga qaytarish

```powershell
git log --oneline -5                       # hozirgi versiya (/status da ham ko'rinadi)
git pull                                   # yoki kerakli commit/branch
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m pytest -q
powershell -ExecutionPolicy Bypass -File scripts\restart_bot.ps1
```

Orqaga qaytarish: `git checkout <oldingi commit>` va yana `restart_bot.ps1`.

### Odoo bazasini almashtirish

Odoo manzili yoki bazasi o'zgarsa (ayniqsa sinov bazasidan asosiy bazaga o'tishda):

1. `.env` da `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_API_KEY` ni yangilang.
2. `.\.venv\Scripts\python scripts\check_topics.py --fix --create` — bo'lim mavzularini yangi
   bazadagi loyihalarga bog'laydi (yo'q loyihani yaratadi). Busiz vazifalar noto'g'ri loyihaga
   yoziladi yoki xato beradi.
3. `scripts\restart_bot.ps1`, so'ng botga `/status`.

`*.dev.odoo.com` — Odoo.sh sinov bazasi: qayta qurilganda manzili almashadi va bot yozgan vazifalar
o'chib ketadi. Bot bunday bazaga ulangan bo'lsa ishga tushganda logga ogohlantirish yozadi va
`/status` da ko'rsatadi.

## Buyruqlar

| Buyruq | Kim | Nima |
|---|---|---|
| `/yordam` | hamma | Rolga mos qisqa qo'llanma |
| `/brifing` | CEO | Bugungi qisqa xulosa (sotuv, to'lov, qarz, vazifalar) |
| `/status` | CEO | Tizim holati |
| `/otask <raqam>` | CEO, menejer | Vazifani o'chirish |
| `/menejer_biriktir @user Bo'lim login api_key Ism` | CEO | Menejerga o'z bo'limi bo'yicha ruxsat |
| `/menejer_ochir @user` | CEO | Ruxsatni olish |
| `/start`, `/unlink` | xodim | Ro'yxatdan o'tish / bog'lanishni bekor qilish |
| `/id` | hamma | Chat va foydalanuvchi raqami |

## Muammo chiqsa

| Belgi | Tekshirish | Yechim |
|---|---|---|
| Bot javob bermayapti | `/health`, `logs\bot.log` oxiri | `restart_bot.ps1`. Ikki joyda bitta token bilan bot ishlamasin (masalan `D:\Work\dunyabunya_agent`) |
| «Odoo serveri bilan aloqa yo'q» | Javobdagi sabab; `/status` | DNS xatosi — manzil o'zgargan: «Odoo bazasini almashtirish». `auth` — API kalit almashgan |
| Vazifa boshqa bo'limga yozilyapti | `python scripts\check_topics.py` | `--fix` bilan qayta bog'lash |
| Javob «AI xizmati javob bermayapti» | `logs\bot.log` da Anthropic/OpenAI xatosi | Balans va kalitni tekshiring; 429 — birozdan keyin o'zi tiklanadi |
| Internetdan qidirmayapti | Logda «Internet qidiruvi ... o'chirildi» | Anthropic Console'da web search yoqilganini tekshiring, so'ng qayta ishga tushiring |
| Xodimga vazifa xabari bormadi | Yordamchi javobidagi sabab | Xodim botda `/start` bosmagan yoki botni bloklagan |
| Hisobot fayli kelmadi | Javobdagi «Sabab: ...» | Ko'pincha Odoo so'rovi xatosi — savolni aniqroq yozing |

## Xavfsizlik

- `.env` va `storage/` git'ga qo'shilmaydi. Menejerlarning Odoo kalitlari bazada shifrlangan
  (`ENCRYPTION_KEY`).
- Rol nazorati kodda (`rbac.py`, `assistant._role_denial`): menejer boshqa bo'lim modelini
  so'rasa tool bajarilmaydi — bu modelga berilgan ko'rsatma emas, texnik cheklov.
- Kalit va parol saqlaydigan Odoo modellari (`ir.config_parameter`, API kalitlar, pochta serveri)
  CEO roli uchun ham o'qilmaydi (`agent_tools.BLOCKED_MODELS`).
- Foydalanuvchi matni, fayl mazmuni, veb-sahifalar va Odoo yozuvlari model uchun ma'lumot —
  ularning ichidagi «ko'rsatmalar» bajarilmaydi (`prompt_guard.py`).
- Hisobot fayllari 64 bitli tasodifiy nom bilan saqlanadi va `REPORT_LINK_TTL_HOURS` dan keyin o'chiriladi.

## Ma'lum cheklovlar

- Kassa chiqimi va valyuta ayirboshlash hujjatini bot orqali *yaratish* o'chirilgan (o'qish ishlaydi).
- Guruhda bot faqat CEO xabarlarini saqlaydi — «guruhda nima gap bo'ldi» savoliga xodimlarning
  javoblari kirmaydi.
- Zaxira nusxalar shu serverning boshqa diskida — serverdan tashqariga nusxalash sozlanmagan.
- Eslab qolingan qaydlar barcha CEO'lar uchun umumiy (menejerlarniki — har kimniki o'ziga).
- Bot qayta ishga tushirilganda shu paytda bajarilayotgan savol javobsiz qoladi — qayta so'rash kerak.
