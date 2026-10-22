# dunyabunya monitoring dashboard (CEO uchun)

Mijoz guruhlari monitoringi ma'lumotlarini (shikoyatlar, toifalar, javob tezligi, dinamika) ko'rsatadigan
Streamlit ilovasi. Botning bazasini **faqat o'qiydi**, Telegram va Odoo'ga hech narsa yubormaydi.
O'ng pastki burchakdagi dumaloq tugma **AI yordamchi** chatini ochadi (hozir OpenAI, keyin Claude): u savollarga javob beradi va
dashboard tarkibini CEO xohlagancha o'zgartiradi — bo'limlar, sahifalar, nomlar, yangi grafik/jadvallar (dizayn —
ranglar, shrift — o'zgarmaydi: Dizayn agenti 2026-10-22 da olib tashlandi). Faqat dashboard'ning o'z fayli
(`data\state.db`) o'zgaradi; chatni va bazani o'chirib bo'lmaydi (pastda: "AI yordamchi"). **AI agentlar** sahifasida yangi agent yaratish mumkin: vazifani yozasiz, AI model tanlaydi,
system prompt yozadi va nimalar kerakligini (Odoo, o'z Telegram boti) aytadi (pastda: "Agent qo'shish").

## Ishga tushirish va to'xtatish

Loyiha papkasida (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File dashboard\run_dashboard.ps1          # haqiqiy baza (faqat o'qish)
powershell -ExecutionPolicy Bypass -File dashboard\run_dashboard.ps1 -Demo    # demo baza
powershell -ExecutionPolicy Bypass -File dashboard\run_dashboard.ps1 -Stop    # to'xtatish
```

So'ng brauzerda: **http://127.0.0.1:8502** (8501-port bu serverda boshqa Streamlit ilovasi bilan band).

- Birinchi marta skript `dashboard\.venv` muhitini yaratadi va `requirements.txt`dagi kutubxonalarni
  **faqat shu muhitga** o'rnatadi (botning `.venv`iga tegmaydi). Keyingi ishga tushirishlar tez.
- Loglar: `dashboard\logs\dashboard.out.log` va `dashboard.err.log` (oldingisi `.prev` ga ko'chadi).
  Loglarga xabar matni, parol yoki kalit yozilmaydi.
- Jarayon buyruq qatori `-m streamlit run app.py` (botning `-m app.` qo'riqchilari bilan adashmaydi).
- `-Stop` faqat shu skript ishga tushirgan jarayonni to'xtatadi (PID: `dashboard\logs\dashboard.pid`).
- Dashboard bilan birga **agentlarning Telegram botlari** jarayoni ham ishga tushadi va to'xtaydi
  (`agent_bots.py`, PID: `logs\agent_bots.pid`, log: `logs\agent_bots.err.log`; demo rejimda ishga tushmaydi).
  Hech bir agentga bot ulanmagan bo'lsa, u shunchaki kutib turadi.

## Sozlamalar (`dashboard\.env`)

`dashboard\.env.example` dan nusxa oling: `dashboard\.env`. Botning asosiy `.env` fayli o'qilmaydi.

| Sozlama | Standart | Ma'nosi |
|---|---|---|
| `DB_PATH` | `../data/monitor.db` | Monitoring bazasi (faqat o'qish) |
| `REPORTS_DIR`, `BACKUP_DIR` | `../reports`, `../backups` | Excel hisobotlar va zaxira nusxalar (faqat ro'yxat va yuklab olish) |
| `MEDIA_DIR` | `../data/media` | Bot saqlagan rasm/GIF'lar (faqat kompaniyaga oid suhbatlardan); guruhning suhbat lentasida ko'rsatiladi |
| `REPORT_TIME` | `11:00` | **Botning `REPORT_TIME` qiymati bilan bir xil bo'lsin** (hozir ikkalasida `00:00`) |
| `SLA_MINUTES` | `5` | Botning javob muddati |
| `WORK_HOURS` | `09:00-19:00` | Faqat "Javob tezligi" sahifasidagi ko'rinish uchun |
| `DASHBOARD_PASSWORD` | bo'sh | Parol. `127.0.0.1` dan boshqa manzilda **majburiy** |
| `DASHBOARD_HOST`, `DASHBOARD_PORT` | `127.0.0.1`, `8502` | Manzil va port |
| `DEMO_MODE` | `0` | `1` = demo baza |
| `CACHE_TTL_OPEN`, `CACHE_TTL_CLOSED` | `60`, `600` | Kesh (soniya): davom etayotgan / yopilgan davrlar |
| `QUERY_TIMEOUT_SECONDS`, `RESULT_LIMIT` | `10`, `1000` | So'rov vaqt chegarasi va natija soni chegarasi |
| `RISK_*` | — | Xavf bali vaznlari va chegaralari (qizil ≥ 70, sariq 40–69) |
| `CHAT_PROVIDER` | `openai` | AI provayderi: `openai` yoki `anthropic` (Claude'ga o'tish — shu qator + kalit) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | bo'sh | Tanlangan provayder kaliti (bo'sh bo'lsa chat o'chiq). `ANTHROPIC_WORKSPACE_ID` — kerak bo'lsa |
| `CHAT_MODEL`, `CHAT_EFFORT` | bo'sh, `low` | Model (bo'sh = `gpt-5.4-mini` / `claude-opus-5-5`) va "o'ylash" darajasi (`medium` — aniqroq) |
| `CHAT_ENABLED`, `CHAT_FALLBACKS` | `1`, `1` | Chatni o'chirish; rad etilganda zaxira model (faqat anthropic) |
| `CHAT_MAX_STEPS`, `CHAT_CONTEXT_MESSAGES`, `CHAT_DAILY_LIMIT` | `6`, `20`, `200` | Agent qadamlari, kontekstdagi xabarlar, kunlik xabar limiti |
| `STATE_DB_PATH` | `data/state.db` | Ko'rinish o'zgarishlari va chat tarixi (dashboard'ning o'z fayli) |
| `AGENT_BUILDER_MODEL` | bo'sh | «Agent qo'shish» da vazifani tahlil qiladigan model (bo'sh = `gpt-5.4` / `claude-opus-5-5`) |
| `AGENT_MAX` | `20` | Ko'pi bilan nechta agent yaratish mumkin |
| `AGENT_ODOO_MODELS` | `res.partner, sale.order, ...` | Agentlar Odoo'dan o'qiy oladigan modellar (vergul bilan) |
| `AGENT_BOT_DAILY_LIMIT`, `AGENT_BOT_HISTORY` | `200`, `12` | Har bir agent botiga kuniga savollar limiti; bot eslab qoladigan oxirgi xabarlar |
| `PROTECTED_BOT_USERNAMES` | ishlab turgan botlar | Bu botlarning tokenini agentga ulab bo'lmaydi (ularning xabarlari yo'qolmasligi uchun) |

O'zgartirgandan keyin dashboard'ni qayta ishga tushiring (`-Stop`, keyin qayta).

## AI yordamchi (o'ng pastdagi tugma)

Har sahifaning o'ng pastki burchagida dumaloq tugma turadi; bosilsa chat paneli ochiladi (yana bosilsa yopiladi).
Panel ixcham: sahifadagi ma'lumot ko'rinib turishi uchun unda faqat oxirgi 6 ta xabar, to'liq suhbat — ⤢ tugmasida.
Panel agent ishlayotganda va sahifa yangilanganda ham ochiq qoladi.

Uchta o'rnatilgan agent: **Boshqaruvchi** umumiy savollarga o'zi javob beradi va ishni ikki agentga beradi —
**Savol-javob agenti** monitoring ma'lumotini tayyor so'rovlar orqali faqat o'qiydi ("Bugun nechta shikoyat
bo'ldi?", "Qaysi guruhda javobsiz ko'p?"); **Ko'rinish agenti** dashboard tarkibini o'zgartiradi. Ular yoniga
«AI agentlar» sahifasida yaratilgan agentlar qo'shiladi (pastda: "Agent qo'shish"). Dizayn agenti 2026-10-22 da
olib tashlandi (foydalanuvchi qarori): rang, shrift va yorug'/qorong'i rejim o'zgarmaydi, chat buni so'rasa
"bo'lmaydi" deydi.

CEO dashboard tarkibini o'zgartira oladi (qaror: 2026-10-17); faqat chatni va bazani o'chirib bo'lmaydi:

- **Bo'lim, ustun, sahifa:** istalganini olib tashlash va qaytarish, ko'chirish ("Shikoyatlarni olib tashla",
  "So'nggi shikoyatlarni tepaga chiqar", "Tizim sahifasini och", "Guruhlar jadvalidan Menejer ustunini olib tashla").
- **Nom berish:** "Umumiy sahifaning nomini «Bosh sahifa» qil", "So'nggi shikoyatlar bo'limini «Bugungi
  shikoyatlar» deb nomla". Bo'sh nom — asl nom qaytadi.
- **Yangi bo'limlar** dashboard'ning tayyor ma'lumot manbalaridan (asosiy raqamlar, guruhlar reytingi, 8 toifa,
  javobsiz va kutayotgan so'rovlar, shikoyatlar, xabar qidiruvi, xodimlar, kunlik dinamika, bot hisobot xulosalari):
  KPI kartalari, jadval, ro'yxat yoki grafik (ustunli, yotiq, chiziqli, maydonli, doira). Masalan: "Bosh sahifaga
  toifalar bo'yicha shikoyatlar doirasini qo'sh". Davr aytilmasa, bo'lim sidebar'dagi davrga ergashadi. Matnli
  **eslatma** ham qo'shish mumkin (oddiy yoki rangli quti).
- **O'z sahifalari:** "Xodimlar nazorati degan sahifa och, unda ..." — chap menyuda **Mening sahifalarim** ostida
  chiqadi; o'chirish ham mumkin.

- Ma'lumot o'chirilmaydi: olib tashlangan bo'lim faqat yashiriladi, chatda yaratilgan bo'lim o'chirilsa ham
  **Qaytarish** bilan qaytadi. Har o'zgarish `data\state.db` da versiya bo'lib saqlanadi:
  **Qaytarish** tugmasi (AI'siz) oxirgi xabar o'zgarishlarini bekor qiladi, **Tarix** — istalgan holatga
  qaytish, **Asl holat** — hammasini standart ko'rinishga qaytarish. Ko'rinish hamma ko'ruvchilar uchun bitta.
- Chat tarixi ham shu faylda saqlanadi (sahifa yangilansa yoki dashboard qayta ishga tushsa ham qoladi).
  ⤢ tugmasi — kunlar bo'yicha to'liq suhbat; **Tarix** tugmasi — o'zgarishlar lentasi (qo'shildi / olib
  tashlandi / ko'chirildi / nomlandi / bekor qilindi, vaqt, sahifa va bo'limlar) va istalgan holatga
  qaytish. **Asl holat** hammasini (bo'limlar, nomlar, o'z sahifalari) standartga qaytaradi.
- **Rasm biriktirish:** chat maydonidagi ＋ tugmasi (yoki rasmni sudrab tashlash) — bir xabarga 3 tagacha rasm
  (png/jpg/webp, 10 MB gacha). Dashboard'ning bir qismi skrinshoti: "Shu joyini tepaga chiqar", "Buni olib
  tashla", "Bu raqam nima?" — agent rasmdagi sarlavhadan bo'limni topadi. Boshqa sayt skrinshoti: shunga o'xshash
  bo'limlar qurish mumkin (uning ranglari ko'chirilmaydi).
  Rasmlar kichraytirilib `data\chat_files\` ga saqlanadi (gitga kirmaydi) va chat tarixida ko'rinadi; rasm AI
  provayderiga yuboriladi (skrinshotdagi mijoz ma'lumotlari ham).
- AI provayderiga (OpenAI / Anthropic) yuboriladi: CEO yozgan matn, sahifa va bo'lim nomlari, savolga javob uchun kerakli ma'lumot
  parchalari (raqamlar, guruh nomlari, shikoyat xulosalari). Ko'rinish agenti mijoz xabarlarining
  matnini ko'rmaydi (yangi bo'lim tuzishda faqat ustunlar, raqamlar va guruh nomlari).
- Loglarga xabar matni va kalit yozilmaydi (faqat agent/tool nomlari, token soni, request id).
- Bitta xabar odatda 3–4 model so'rovi. `gpt-5.4-mini` bilan bir xabar 1 sentdan kam; Claude Opus 5.5 bilan
  taxminan 3–8 sent. Claude'ga o'tish: `CHAT_PROVIDER=anthropic` va `ANTHROPIC_API_KEY`, keyin qayta ishga tushirish.
- Bo'limlar ro'yxati va ularning nomlari kodda (`views\*.py` ichida `layout.Block`, nomlari `ui\i18n.py` da
  `blk.*`). Yangi bo'lim kodga qo'shilsa, u avtomatik chiqadi va agent uni taniydi.

## Agent qo'shish, modellar, ulanishlar va Telegram botlar

**AI agentlar** sahifasi agentlarni daraxt ko'rinishida ko'rsatadi: tepada Boshqaruvchi, ostida u chaqiradigan
agentlar, ularning ostida o'z sub-agentlari (ko'pi bilan **3 qavat** — har qavat javobga vaqt va xarajat qo'shadi).
Agent faqat o'z ostidagi agentlarni chaqiradi; ota-agentning promptiga ostidagilar ro'yxati avtomatik qo'shiladi.

**«Agent qo'shish»:**

1. Agent nomi va vazifasini to'liq yozasiz (kim uchun, qaysi ma'lumot bilan, qanday natija) yoki **namuna**dan
   boshlaysiz (Shikoyatlar tahlilchisi, Javob tezligi nazoratchisi, Odoo savdo yordamchisi, Telegram yordamchi —
   bosilsa maydonlar to'ladi) → **Tahlil qilish**. Bor agent nomi qayta berilmaydi.
2. AI (`AGENT_BUILDER_MODEL`) taklif beradi: **model** (vazifaga yetadigan eng arzoni), daraxtdagi **joyi**,
   **vositalar**, **o'z Telegram boti** kerakmi, **hozircha mavjud bo'lmagan** narsalar, vazifa noaniq bo'lsa
   **savollar** va **system prompt**. Har birining sababi yoziladi; hammasini o'zgartirish mumkin. Savollarga shu
   yerning o'zida javob yoziladi → **Javoblar bilan qayta tahlil qilish** (javoblar vazifa matniga qo'shiladi).
3. Kerakli ulanishlarni kiritasiz (Odoo, Telegram bot) — har biri **Tekshirish** bilan sinaladi. Keyin ham
   agent sahifasida qo'shish mumkin.
4. **Agentni yaratish** — agent daraxtda chiqadi, chatda ota-agenti orqali ishlay boshlaydi va uning sahifasi
   ochiladi.
5. **Sinab ko'rish** (agent sahifasining tepasida): agentga to'g'ridan-to'g'ri yozasiz — u o'z modeli, vositalari
   va sub-agentlari bilan haqiqiy javob beradi; ostida vaqt, ishlatgan vositalari, token soni va (bo'lsa)
   dashboard o'zgarishi ko'rinadi. Bu suhbat saqlanmaydi, chatning kunlik limitiga kirmaydi.
6. **Vazifasi** (agent sahifasida): vazifani o'zgartirib **AI bilan promptni qayta yozish** — AI yangi prompt
   yozadi, ko'rib chiqib **Qabul qilish va saqlash**; eski prompt «Prompt tarixi» da qoladi. AI boshqa vosita yoki
   model tavsiya qilsa, bu yozib qo'yiladi (ularni «Agent sozlamalari» da o'zingiz o'zgartirasiz).

**Vositalar guruhlari:** Monitoring ma'lumotlari (faqat o'qish), Odoo (faqat o'qish), Dashboard ko'rinishi
(dashboard'ni hamma uchun o'zgartiradi, **Qaytarish** bilan bekor qilinadi). Mavjud emas (AI ularni alohida aytadi,
agent esa "hozircha qila olmayman" deydi): Odoo'ga yozish, mijozlarga yoki guruhlarga birinchi bo'lib yozish,
belgilangan vaqtda ishlash (masalan har kuni 9:00 da hisobot), dashboard dizaynini o'zgartirish,
e-mail/SMS/qo'ng'iroq, internet, boshqa tizimlar.

**Modellar:** har bir agentning — o'rnatilganlarining ham — modeli uning sahifasida tanlanadi va chatdagi keyingi
xabardan ishlaydi: `gpt-5.4-nano` (eng tez, eng arzon), `gpt-5.4-mini` (standart), `gpt-5.4` (murakkab tahlil),
`gpt-5.5` (eng kuchli, qimmatroq); `ANTHROPIC_API_KEY` qo'shilsa — Claude Haiku / Sonnet / Opus 5.5 ham.
O'rnatilgan agentlarda «Standart» — `CHAT_MODEL`.

**Odoo ulanishi:** manzil, baza nomi, login va API kalit. Odoo'da agent uchun **alohida, faqat o'qish huquqli
foydalanuvchi** ochish tavsiya etiladi (asosiy cheklov — Odoo'ning o'z huquqlari). Bundan tashqari agent faqat
`fields_get`, `search_read`, `search_count`, `read_group` ni chaqira oladi, faqat `AGENT_ODOO_MODELS` dagi
modellarni o'qiydi; filtr JSON sifatida tekshiriladi (kod sifatida bajarilmaydi). Bitta ulanishni bir nechta agent
ishlatishi mumkin.

**Agentning Telegram boti:** @BotFather'da `/newbot` bilan agent uchun **yangi** bot ochiladi va tokeni
kiritiladi; **«Kimlar yoza oladi»** ga Telegram ID yoki @username yoziladi. Boshqa odam yozsa, bot unga uning ID
raqamini aytadi (soatiga bir marta) — shu raqamni ro'yxatga qo'shish kifoya. Bot faqat shaxsiy chatlarda va faqat
matnga javob beradi, suhbatni eslab qoladi (`/new` — yangi suhbat, `/start` — tanishtiruv). Ishlab turgan botning
tokeni (masalan `@dunyabunya_assistant_bot`) yoki webhook o'rnatilgan bot qabul qilinmaydi. Agar token boshqa
dasturda ham ishlatilayotgani aniqlansa (Telegram 409), bot darhol to'xtaydi va sahifada yoziladi — sabab
bartaraf etilgach **«Qayta ulash»**.

**Kalitlar:** Odoo API kaliti va bot tokeni `data\state.db` da Windows DPAPI bilan shifrlangan holda saqlanadi
(boshqa kompyuterda ochilmaydi), sahifada qayta ko'rsatilmaydi (faqat oxirgi 4 belgisi), logga va AI'ga
yuborilmaydi. Agent o'chirilsa, uning boti (token) va boshqa agent ishlatmayotgan Odoo ulanishi ham o'chadi;
uning ostidagi agentlar bir qavat yuqoriga ko'tariladi.

## Demo rejim

`-Demo` kaliti `dashboard\demo\demo_monitor.db` dan o'qiydi: 200 guruh, 60 kun, ~110 ming sun'iy xabar
(qurilish mollari mavzusida, o'zbek/rus tilida). Sxema haqiqiy bazadan **faqat o'qib** olinadi,
ma'lumotlarning hammasi sun'iy. Demo baza 24 soatdan eski bo'lsa avtomatik qayta yaratiladi
(`-RebuildDemo` — majburan). Har sahifada "DEMO" banneri turadi; demo baza haqiqiy bazaga aralashmaydi.

## Sahifalar

- **Umumiy** — "hammasi joyidami?" holati, asosiy raqamlar, diqqat talab qiladigan guruhlar, hozir javob
  kutayotganlar, kun xulosasi (qoida bo'yicha + bot hisobotidagi AI xulosa), grafiklar, so'nggi shikoyatlar.
- **Guruhlar** — reyting (xavf bali bilan) va guruh tafsiloti: dinamika, javob tarixi, javobsiz
  so'rovlar, mijozlar, suhbat lentasi, eksport.
- **Bo'limlar** — 8 toifa: kartalar, toifa tafsiloti, takrorlanayotgan mavzular, saqlangan AI xulosalar.
- **AI agentlar** — chatdagi yordamchining agentlari daraxt ko'rinishida (Boshqaruvchi va u chaqiradigan
  Savol-javob, Ko'rinish agentlari va yaratilgan agentlar). Agent kartasini bosish uning sozlamalarini ochadi: model, vositalar
  va **system prompt** — tahrirlash, saqlash, standartga qaytarish, tarixdan tiklash. Saqlangan prompt chatdagi
  keyingi xabardan ishlatiladi (`data\state.db`, `agent_prompts` jadvali). Faqat ko'rsatma qismi tahrirlanadi;
  bo'limlar katalogi, ma'lumot manbalari va ta'riflarni dashboard promptning oxiriga o'zi
  qo'shadi (`assistant\prompts.py`), shuning uchun prompt o'zgarsa ham vositalar ishlaydi. Chatni o'chirib
  bo'lmasligi va bazaga yozilmasligi promptga emas, kodga bog'liq.
- **Javob tezligi**, **Xodimlar**, **Dinamika**, **Qidiruv**, **Hisobotlar arxivi**, **Tizim va ma'lumot sifati**.

Standart ko'rinish sodda: ba'zi bo'limlar, ustunlar va texnik sahifalar (**Hisobotlar arxivi**, **Tizim**)
boshida yashirin — chatda "qo'sh" deyilsa chiqadi. Kodda: `views\*.py` dagi `Place(..., hidden=True)`,
`Block(..., hidden_items=...)`, `ui\layout.py` dagi `DEFAULT_HIDDEN_PAGES`. Chatdagi o'zgarishlar shu
standartning ustiga yoziladi (`data\state.db`); **Asl holat** — standartga qaytish.

Davr, guruh va toifa tanlovlari URL'da saqlanadi — havolani saqlab yoki yuborib bo'ladi.
Barcha ta'riflar har sahifadagi **"Ta'riflar"** tugmasida.

## Tahrirlanadigan fayllar

- `config\categories.toml` — toifalar tartibi, rangi va **mas'ul bo'limi** (standart bo'limlar taxminiy).
  Nomlar botdagi nomlar bilan aynan bir xil bo'lishi shart.
- `config\known_issues.toml` — ma'lum muammolar (sana oralig'i + matn). Tanlangan davrga to'g'ri kelsa,
  sahifa tepasida ogohlantirish chiqadi. Muammo hal bo'lsa, `end` sanasini yozing.

## Xavfsizlik

- Baza `mode=ro` URI va `PRAGMA query_only=ON` bilan ochiladi; har so'rov uchun qisqa ulanish, bir vaqtda
  bitta; ~10 soniyadan uzoq so'rov to'xtatiladi. Bot kodi import qilinmaydi.
- Xabar matnlari HTML'ga qo'yishdan oldin escape qilinadi; CSV/Excel eksportida `=`, `+`, `-`, `@` bilan
  boshlanadigan matn formula bo'lib ishlamasligi uchun oldiga `'` qo'yiladi.
- Fayllar faqat `hisobot_YYYY-MM-DD.xlsx` / `monitor_YYYY-MM-DD.db` shabloni bo'yicha ro'yxatlanadi.
- AI agentlar kod va SQL yozmaydi: Savol-javob agenti faqat `core\queries.py` dagi tayyor so'rovlarni
  (o'sha faqat o'qish ulanishi orqali) chaqiradi; chatda yaratilgan bo'limlar ham faqat shu tayyor ma'lumot
  manbalaridan quriladi (AI manba, parametr va ko'rinishni tanlaydi, xolos). Ko'rinish agenti faqat
  `data\state.db` dagi qatlamni o'zgartiradi. Mijoz xabaridagi matn agentga buyruq bo'lib o'tmaydi; har o'zgarish
  **Qaytarish** bilan bekor qilinadi.
- Dashboard LAN'ga ochilsa, parolni bilgan har kim chatdan ham foydalana oladi, ko'rinishni o'zgartira oladi
  va agent yarata oladi.
- Agentlar Odoo'ga faqat o'qish uchun ulanadi (yuqorida: "Odoo ulanishi"); Telegram botiga faqat ruxsat
  berilgan odamlar yoza oladi; kalit va tokenlar shifrlangan.

## LAN orqali ochish (hozir yoqilmagan — faqat yo'riqnoma, qo'lda)

1. `dashboard\.env`: `DASHBOARD_HOST=0.0.0.0` va kuchli `DASHBOARD_PASSWORD` (parolsiz ishga tushmaydi).
2. Administrator PowerShell'da firewall qoidasi (faqat ichki tarmoq uchun):
   `New-NetFirewallRule -DisplayName "dunyabunya dashboard" -Direction Inbound -Protocol TCP -LocalPort 8502 -Action Allow -Profile Domain,Private -RemoteAddress LocalSubnet`
3. Ulanish shifrlanmagan (HTTP). Internetga ochish kerak bo'lsa — faqat HTTPS reverse proxy orqali.

Server qayta yonganda avtomatik ishga tushirish kerak bo'lsa, Task Scheduler'da `run_dashboard.ps1` ni
ishga tushiradigan vazifa yarating (skript bu ishni o'zi qilmaydi).

## Muammolarni bartaraf etish

| Belgi | Sabab va yechim |
|---|---|
| "Port 8502 band" | `DASHBOARD_PORT` ni boshqa portga o'zgartiring |
| "Monitoring bazasi topilmadi" | `DB_PATH` ni tekshiring (dashboard papkasiga nisbatan) |
| "Demo baza topilmadi" | `run_dashboard.ps1 -Demo -RebuildDemo` |
| Raqamlar yangilanmayapti | Sidebar'dagi **Yangilash** tugmasi (keshni tozalaydi) |
| "So'rov juda uzoq davom etdi" | Qisqaroq davr tanlang; `QUERY_TIMEOUT_SECONDS` |
| Sahifa ochilmayapti | `dashboard\logs\dashboard.err.log` ni ko'ring |
| "AI yordamchi sozlanmagan" | `dashboard\.env` ga tanlangan provayder kalitini (`OPENAI_API_KEY` yoki `ANTHROPIC_API_KEY`) yozing va qayta ishga tushiring |
| "AI hisobida mablag' tugagan" | OpenAI hisobini to'ldiring (yoki `CHAT_PROVIDER=anthropic`) |
| "AI kaliti noto'g'ri" | Kalitni va kerak bo'lsa `ANTHROPIC_WORKSPACE_ID` ni tekshiring |
| Bo'lim yo'qolib qoldi | Chatda "qaytar" deng yoki **Tarix** / **Asl holat** tugmalari |
| Chatda yaratilgan bo'limda "ma'lumot olinmadi" | Bo'lim sozlamasi endi mos emas (masalan guruh nomi o'zgargan): chatda yangilashni so'rang |
| Agent boti javob bermayapti | `logs\agent_bots.err.log`; jarayon ishlayaptimi (`run_dashboard.ps1` uni ishga tushiradi); yozgan odam «Kimlar yoza oladi» ro'yxatidami |
| "Bot to'xtatildi: token boshqa dasturda" | O'sha dasturni to'xtating yoki yangi bot oching, so'ng agent sahifasida **«Qayta ulash»** |
| "Odoo rad etdi" / "Login yoki API kalit noto'g'ri" | Odoo'dagi API kalit muddati tugagan bo'lishi mumkin: yangi kalit bilan yangi ulanish qo'shing |

## Testlar va tekshiruv vositalari

```powershell
cd dashboard
.venv\Scripts\python.exe -m pytest              # core, sahifalar, ko'rinish qatlami, agentlar (soxta klient, API chaqirilmaydi), haqiqiy bazada faqat o'qish smoke test
.venv\Scripts\python.exe tests\bench_pages.py    # tezlik: har sahifa keshsiz va kesh bilan (to'liq demo baza)
.venv\Scripts\python.exe tests\explain_queries.py  # asosiy so'rovlarning SQLite reja (indeks ishlatilishi)
.venv\Scripts\python.exe demo\build_demo.py      # demo bazani qayta yaratish
```

## Tuzilish

```
dashboard\
  app.py                 kirish nuqtasi (navigatsiya, parol, sidebar, chat)
  config\                categories.toml, known_issues.toml
  core\                  ma'lumotlar qatlami: settings, db (faqat o'qish), queries (BARCHA SQL), metrics
                         (BARCHA ta'riflar), timeutil, text, export, issues, state (dashboard'ning o'z fayli)
  ui\                    i18n (matnlar), theme, charts, components, shell, auth, data (kesh), style.css,
                         layout (bloklar va ko'rinish qatlami), chat (AI yordamchi paneli), custom (chatda
                         yaratilgan bo'limlar), overrides (o'z nomlar), media (lentadagi rasm/GIF'lar)
  views\                 sahifalar: har biri bloklar ro'yxati (SQL yo'q); own — CEO'ning o'z sahifalari;
                         agents — AI agentlar daraxti, sozlamalari, «Sinab ko'rish», vazifa va system prompt
                         tahriri; agent_builder — «Agent qo'shish» (namunalar, savollarga javob);
                         agent_connections — Odoo va Telegram ulanish formalari
  assistant\             AI agentlar: llm (OpenAI / Claude), orchestrator, data_agent + data_tools, ui_agent,
                         prompts; registry (agentlar daraxti), team (agentlar bir-birini
                         chaqirishi, yaratilgan agentni ishga tushirish), builder («Agent qo'shish» tahlili),
                         models (modellar ro'yxati), odoo_tools (Odoo'dan faqat o'qish)
  agent_bots.py          agentlarning Telegram botlari jarayoni (run_dashboard.ps1 ishga tushiradi)
  core\secrets.py        kalit/tokenlarni shifrlash (DPAPI); core\telegram.py — Bot API mijoz
  data\state.db          ko'rinish o'zgarishlari, chat tarixi, agentlar, ularning promptlari, modellari,
                         ulanishlari (shifrlangan) va bot suhbatlari (gitga kirmaydi)
  demo\                  demo baza yaratuvchi
  tests\                 pytest + bench + explain
  .streamlit\config.toml  Streamlit sozlamalari va o'zgarmas dizayn ([theme])
  run_dashboard.ps1, requirements.txt, .env.example
```
