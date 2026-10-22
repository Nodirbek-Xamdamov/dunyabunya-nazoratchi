# Arxitektura

Bu hujjat loyihaning **hozirgi tuzilishini**, **o'sish uchun kerakli maqsadli tuzilishni** va
ikkisi orasidagi **o'tish rejasini** tushuntiradi. Kod bilan birga yangilanib boradi: modul
qo'shilsa, jarayon yoki baza o'zgarsa — shu hujjat ham o'zgaradi.

Mazmuni:

0. [Bir qarashda](#0-bir-qarashda)
1. [Hozirgi holat](#1-hozirgi-holat)
2. [O'sishga xalaqit beradigan muammolar](#2-osishga-xalaqit-beradigan-muammolar)
3. [Maqsadli arxitektura](#3-maqsadli-arxitektura)
4. [Yangi kanal va integratsiyalarni ulash](#4-yangi-kanal-va-integratsiyalarni-ulash)
5. [Modullar orasidagi qoidalar](#5-modullar-orasidagi-qoidalar)
6. [O'tish rejasi](#6-otish-rejasi)
7. [Hal qilinishi kerak bo'lgan savollar](#7-hal-qilinishi-kerak-bolgan-savollar)
8. [Atamalar](#8-atamalar)

---

## 0. Bir qarashda

Tizim uch ish qiladi:

1. **Buyurtma va mijoz boti.** Odoo'dagi buyurtmani (PDF bilan) mijozga Telegram orqali yetkazadi,
   mijoz tasdiqlaydi yoki rad etadi; botda mijoz buyurtma beradi, tijorat takliflari, balans va
   statistikasini ko'radi. Odoo bu ma'lumotlarni API orqali olib ketadi.
2. **Mijozlar bilan muloqot monitoringi.** Mijoz guruhlaridagi har bir xabarni yig'adi, AI bilan
   toifalarga ajratadi, "5 daqiqa qoidasi"ni tekshiradi va har kuni Excel hisobot beradi.
3. **CEO dashboard'i.** Monitoring natijalarini ko'rsatadi; ichida AI yordamchi (3 ta o'rnatilgan agent va yaratilganlari) bor.

Loyiha kengayadi: muloqot **kanallari** ko'payadi (shaxsiy chatlar, telefon qo'ng'iroqlari —
MyZvonki), **Odoo** ma'lumotlari monitoringga bog'lanadi. Buning uchun tizim quyidagi shaklga
keltiriladi:

```
  KANALLAR (kirish)              YADRO                              NATIJALAR (chiqish)
 +-------------------------+   +-------------------------------+   +-------------------------+
 | Telegram guruhlari      |-->|  Yagona muloqot bazasi        |-->| Kunlik / oylik hisobot  |
 | Telegram shaxsiy chatlar|-->|  (suhbat, hodisa, mijoz)      |-->| CEO dashboard           |
 | Qo'ng'iroqlar (MyZvonki)|-->|               |               |-->| AI agentlar             |
 | ... keyin: sayt chati,  |   |  Qayta ishlash navbati:       |-->| Ogohlantirishlar        |
 |     Instagram           |   |  ovoz->matn, AI tasnif,       |   +-------------------------+
 +-------------------------+   |  5 daqiqa qoidasi             |
                               +---------------+---------------+
                                               |
                                    +----------+----------+
                                    | Odoo integratsiyasi |  mijozlar, obyektlar,
                                    | (ikki tomonlama)    |  buyurtmalar, lidlar
                                    +---------------------+
```

Asosiy g'oya: **har bir kanal o'z ma'lumotini bitta umumiy shaklga keltirib bazaga yozadi**, qolgan
hamma narsa (tasnif, qoidalar, hisobot, dashboard, AI) kanalni bilmasdan ishlaydi. Yangi kanal
qo'shish = bitta yangi "adapter" yozish, qolgan tizim o'zgarmaydi.

---

## 1. Hozirgi holat

### 1.1. Jarayonlar

Hammasi AI-SERVER'da (Windows Server 2019, RTX 5060 Ti) ishlaydi va hozircha **qo'lda** ishga
tushirilgan.

| Jarayon | Buyruq | Nima qiladi | Baza | Tashqi ulanishlar |
|---|---|---|---|---|
| **api** (FastAPI, :8000, 3 worker) | `uvicorn app.fastapi_app:app` | Odoo webhook'i (yangi buyurtma), Odoo uchun sinxronlash API'lari, botning ichki API'si (Odoo'ga yagona yo'l), haydovchi boti (taklif-bot) rasmlari, CEO agent hisobotlari proksisi | PostgreSQL | Odoo (XML-RPC), Telegram Bot API (buyurtma xabari), :8001 |
| **bot** (aiogram) | `python -m app.bot` | Mijoz boti (menyu, buyurtma tasdig'i, ma'lumot tahriri), guruhda mention → Odoo lid, guruh xabarlarini yig'ish; **fonda 5 ta vazifa**: tozalash, Odoo tekshiruvi, AI tasnif, 5 daqiqa qoidasi, hisobot + zaxira | PostgreSQL + SQLite `monitor.db` | Telegram, api (HTTP), Ollama, OpenAI |
| **userbot** (Telethon, premium akkaunt) | `python -m app.userbot` | Buyurtma uchun mijozga birinchi xabar, shaxsiy chatga avto-javob, guruh a'zolarini skanerlash | PostgreSQL | Telegram (MTProto) |
| **dashboard** (Streamlit, :8502) | `dashboard\run_dashboard.ps1` | CEO dashboard'i, AI chat, «AI agentlar» sahifasi (agentlar daraxti, «Agent qo'shish», har agentga model) | `state.db` (o'ziniki), `monitor.db` (faqat o'qiydi) | OpenAI, Odoo (agentlar, faqat o'qish) |
| **agent_bots** (2026-10-20 dan) | `dashboard\agent_bots.py` (`run_dashboard.ps1` ishga tushiradi) | Sahifada yaratilgan agentlarning o'z Telegram botlari: ruxsat berilgan odamlarga agent orqali javob | `state.db` | Telegram Bot API, OpenAI, Odoo (faqat o'qish) |

### 1.2. Ma'lumotlar qayerda

| Joy | Nima saqlanadi | Kim yozadi | Kim o'qiydi |
|---|---|---|---|
| PostgreSQL `odoo_telegram_db` (12 jadval) | Buyurtmalar, botga yozgan mijozlar, guruh a'zoliklari, balans, yetkazish rasmlari, obyekt lokatsiyalari va tahrirlari, guruhdagi so'rovlar | api, bot, userbot | o'sha uchtasi; Odoo — api orqali |
| SQLite `data/monitor.db` (6 jadval) | Guruhlar, xabarlar, tasniflar, 5 daqiqa tekshiruvlari, kunlik sonlar va xulosalar | bot | bot, dashboard |
| SQLite `dashboard/data/state.db` | Dashboard ko'rinishi, chat tarixi, agentlar va ularning promptlari | dashboard | dashboard |
| `data/` | PDF'lar, rasmlar, webhook loglari | api, bot | api, bot |
| `reports/`, `backups/` | Excel hisobotlar, monitoring bazasi zaxiralari | bot | odamlar |

### 1.3. Hozirgi sxema

```
                          +--------------------+
   Odoo (CRM) <---------->|  api (FastAPI)     |<------ taklif-bot (haydovchi rasmlari)
   webhook + XML-RPC      |  webhook, sync,    |
   "olish + belgilash"    |  ichki API         |------> Telegram: buyurtma xabari
                          +---------+----------+
                                    | ichki API (HTTP)
            +-----------------------+------------------------+
            |                                                |
 +----------+-------------------------------+    +-----------+--------------+
 | bot (aiogram)                            |    | userbot (Telethon)       |
 |  - mijoz menyusi, buyurtma tasdig'i      |    |  - mijozga 1-xabar       |
 |  - guruh xabarlarini yig'ish             |    |  - a'zolarni skanerlash  |
 |  - FON: tasnif, 5 daqiqa qoidasi,        |    |  - shaxsiy chatga        |
 |    hisobot, zaxira, tozalash,            |    |    avto-javob            |
 |    Odoo tekshiruvi                       |    +-----------+--------------+
 +-----+-------------------------+----------+                |
       |                         |                           |
       v                         v                           v
  SQLite monitor.db        PostgreSQL odoo_telegram_db <-----+---- (api ham yozadi)
  (monitoring)             (buyurtma, mijoz, a'zolik ...)
       |
       | faqat o'qish
       v
 +----------------------------+
 | dashboard (Streamlit)      |----> OpenAI (AI chat, 4 agent)
 |  + state.db                |
 +----------------------------+

 bot ----> Ollama gemma4:12b (tasnif, xulosa) | OpenAI gpt-4o-transcribe (ovoz -> matn)
 bot ----> "Tema test" guruhi (kunlik Excel hisobot)
```

### 1.4. Muloqot monitoringining oqimi (hozir)

```
guruh xabari -> bot/group_monitor (darhol monitor.db'ga, holati "pending")
             -> monitor_classifier (fon: guruh bo'yicha paketlab, gemma4 bilan tasnif)
             -> monitor_sla (har daqiqa: "javob berildi" / "javobsiz")
             -> monitor_report (har kuni 00:00: sonlar SQL bilan, xulosa model bilan -> Excel)
             -> dashboard (faqat o'qiydi)
```

### 1.5. Yaxshi tomonlari — saqlab qolinadi

- **Odoo'ga bitta eshik:** Odoo bilan faqat api gaplashadi, bot uning ichki API'sidan foydalanadi.
- **Avval saqla, keyin qayta ishla:** xabar darhol bazaga yoziladi, AI uni keyin tasniflaydi —
  model sekinlashsa yoki bot qayta yonsa ham xabar yo'qolmaydi.
- **Raqamlarni faqat SQL hisoblaydi**, model faqat matnli xulosa yozadi — hisobotdagi raqam
  "o'ylab topilmaydi".
- **Monitoring bazasiga bitta modul tegadi** (`monitor_db.py`); dashboard'da hamma SQL bitta
  faylda (`dashboard/core/queries.py`).
- **Dashboard bot kodiga bog'lanmagan:** `app` paketini import qilmaydi, bazani faqat o'qiydi.
- **AI agentlar faqat tayyor vositalar bilan ishlaydi** — AI yozgan SQL yoki kod ishga tushmaydi.
- **Testlar** haqiqiy model, Telegram va Odoo'ni chaqirmaydi.

---

## 2. O'sishga xalaqit beradigan muammolar

| # | Muammo | Nima uchun xalaqit beradi |
|---|---|---|
| 1 | **bot jarayoni hamma ishni qiladi**: mijoz boti, xabar yig'ish va 5 ta fon vazifa | Tasnif yoki hisobot qotib qolsa, yoki monitoring o'zgarishi uchun bot qayta ishga tushirilsa — mijoz menyusi ham to'xtaydi. Qo'ng'iroqlar qo'shilsa, yuk yana shu jarayonga tushadi. |
| 2 | **Monitoring modeli faqat Telegram guruhiga moslangan**: `groups` + `messages(chat_id, message_id)` | Shaxsiy chat va qo'ng'iroq bu jadvallarga sig'maydi: qo'ng'iroqda chat_id yo'q, lekin telefon raqami, davomiylik, yo'nalish va yozuv bor. |
| 3 | **Bitta mijoz — to'rt xil yozuv**: guruhdagi Telegram akkaunti, botdagi `telegram_users`, Odoo'dagi partner, MyZvonki'dagi telefon raqami | "Shu mijoz bilan hamma kanaldagi muloqot qanday?" degan savolga javob berib bo'lmaydi. Hisobotda guruh bor, mijoz yo'q. |
| 4 | **Ikki xil baza**: PostgreSQL va SQLite | SQLite'ga bir vaqtda bitta jarayon yoza oladi. Yozuvchilar ko'paysa (bot, userbot, qo'ng'iroqlar, worker) "database is locked" boshlanadi. Dashboard SQLite jadvallarini to'g'ridan-to'g'ri o'qiydi: jadval o'zgarsa, dashboard buziladi. |
| 5 | **AI chaqiruvlari to'rt joyda alohida**: `llm_client.py` (tasnif), `stt.py` (ovoz), `translate.py`, `dashboard/assistant/llm.py` (chat) | Har birining o'z sozlamasi, kaliti va xato ishlovi bor. Xarajatni bir joyda ko'rib bo'lmaydi, modelni almashtirish uchun bir necha joyni o'zgartirish kerak. |
| 6 | **Sozlamalar tarqoq**: bitta katta `.env` (~60 qiymat, hamma jarayon uchun) va dashboard'ning alohida `.env`i | `REPORT_TIME` va `SLA_MINUTES` ikki joyda qo'lda bir xil saqlanadi (2026-10-19 da aynan shunday qilindi). Birini unutsa, dashboard va hisobot har xil raqam ko'rsatadi. |
| 7 | **Ishga tushirish qo'lda** | Server qayta yonsa, hech narsa o'zi turmaydi. `scripts/bot_watchdog.ps1` yozilgan, lekin ro'yxatdan o'tkazilmagan. Loglar har xil joyda. |
| 8 | **Katta fayllar, aralash papka** | `fastapi_app.py` — 1700 qator va ~30 endpoint bitta faylda, `handlers/menu.py` — 740 qator. Monitoring modullari (`monitor_*.py`) `app/` ildizida buyurtma kodi bilan aralash. |
| 9 | **Takror va begona vazifalar** | Odoo uchun "olish + sinxronlangan deb belgilash" naqshi 6 marta takrorlangan. api boshqa loyihaning (CEO agent) hisobotlarini proksilaydi. |

---

## 3. Maqsadli arxitektura

### 3.1. Asosiy qarorlar

1. **Modulli monolit.** Bitta repo, bitta Python paket, ichida chegaralari aniq modullar. Mikroservis
   emas: jamoa kichik, server bitta — mikroservis bu yerda faqat murakkablik qo'shadi.
2. **Har bir rol — alohida jarayon:** `api`, `bot`, `userbot`, `worker` (yangi), `dashboard`. Biri
   to'xtasa yoki qayta yonsa, qolganlari ishlayveradi.
3. **Kanalga bog'liq bo'lmagan muloqot modeli:** hamma kanal bir xil tushunchalarga keltiriladi —
   *suhbat*, *hodisa*, *ishtirokchi*, *mijoz* (3.5-band).
4. **Avval saqla, keyin qayta ishla** — endi hamma kanal uchun: kanal hodisani darhol yozadi,
   qolganini worker qiladi.
5. **Bitta baza — PostgreSQL**, bo'limlarga (schema) ajratilgan. Dashboard faqat o'qish huquqli
   foydalanuvchi bilan va tayyor ko'rinishlar (view) orqali o'qiydi.
6. **Tashqi tizimlarga bitta eshik:** Odoo — faqat `integrations/odoo` orqali, AI — faqat `ai/`
   orqali, Telegram — faqat `channels/` va bot orqali.
7. **AI faqat tayyor vositalar bilan ishlaydi:** har bir modul o'z vositalarini e'lon qiladi,
   agentlar shulardan tanlaydi. AI yozgan SQL yoki kod hech qachon ishga tushmaydi.

### 3.2. Qatlamlar

```
+----------------------------------------------------------------------------+
| KIRISH NUQTALARI (jarayonlar) — o'zida mantiq yo'q, faqat modullarni ulaydi |
|   api (FastAPI)   bot (aiogram)   userbot (Telethon)   worker   dashboard  |
+-------------------------------------+--------------------------------------+
| KANALLAR (adapterlar)               | INTEGRATSIYALAR                      |
|   telegram_groups                   |   odoo (XML-RPC, sinxronlash)        |
|   telegram_private                  |   myzvonki (API)                     |
|   calls                             |                                      |
+-------------------------------------+--------------------------------------+
| YADRO (biznes mantiq)                                                      |
|   orders (buyurtmalar)   comms (suhbat, hodisa)   contacts (mijoz, xodim)  |
|   pipeline (ovoz->matn -> tasnif -> qoidalar)    reports (hisobotlar)      |
+----------------------------------------------------------------------------+
| UMUMIY XIZMATLAR                                                           |
|   ai (modellar, STT, promptlar, vositalar reyestri)   storage (PostgreSQL, |
|   fayllar, migratsiyalar)   config   logging / health                      |
+----------------------------------------------------------------------------+
```

Qoida: **yuqoridagi qatlam pastdagini chaqiradi, aksi emas.** Yadro Telegram, Odoo yoki MyZvonki
borligini bilmaydi — u faqat "hodisa", "suhbat", "mijoz" bilan ishlaydi.

### 3.3. Papka tuzilishi

```
odoo telegram integration/
  app/
    entrypoints/          jarayonlarning kirish nuqtalari (har biri ~50 qator)
      api.py                FastAPI ilovasi: routerlarni ulaydi
      bot.py                aiogram: mijoz boti + Telegram kanallarini qabul qilish
      userbot.py            Telethon
      worker.py             fon vazifalar rejalashtiruvchisi (YANGI)
    api/                  FastAPI routerlari
      odoo_webhooks.py      Odoo -> biz (yangi buyurtma, balans)
      odoo_sync.py          Odoo olib ketadigan ma'lumotlar (bitta umumiy naqsh)
      bot_internal.py       bot uchun ichki API (Odoo'ga so'rovlar)
      calls_webhook.py      MyZvonki -> biz (YANGI)
      health.py
    bot/                  mijoz boti interfeysi: handlers (start, menu, orders, edit_info),
                          keyboards, i18n
    channels/             har bir kanal o'z ma'lumotini "hodisa"ga aylantiradi
      telegram_groups/      hozirgi group_monitor + chat_events
      telegram_private/     shaxsiy chatlar (YANGI)
      calls/                qo'ng'iroqlar (YANGI)
    integrations/
      odoo/                 client.py (XML-RPC), sync.py, mapping.py
      myzvonki/             client.py (YANGI)
    domain/
      orders/               buyurtma, yetkazish, obyekt (hozirgi models.py)
      comms/                suhbat, hodisa, tasnif, tekshiruv (hozirgi monitor_db.py)
      contacts/             mijoz, identifikatorlar, xodim (YANGI)
    pipeline/             transcribe.py, classify.py, sla.py, alerts.py
                          (hozirgi monitor_classifier.py, monitor_sla.py)
    reports/              daily_excel.py (hozirgi monitor_report.py), monthly.py
    ai/                   gateway.py (provayderlar), stt.py, prompts/, tools/ (reyestr)
    storage/              db.py (ulanish, sessiya), migrations/ (Alembic), files.py
    config.py             bo'limlarga ajratilgan sozlamalar
  dashboard/              alohida ilova (o'z venv'i), bazani faqat o'qiydi
  deploy/
    windows/              watchdog'lar, start/stop/status skriptlari
    linux/                systemd, docker-compose (keyin ko'chish uchun)
  docs/ARCHITECTURE.md
  tests/                  modullar bo'yicha papkalar
```

### 3.4. Jarayonlar (maqsad)

| Jarayon | Vazifasi | Nima **qilmaydi** |
|---|---|---|
| **api** | Odoo va MyZvonki webhook'lari, Odoo sinxronlash API'lari, botning ichki API'si, health | Uzoq ishlarni o'zi bajarmaydi — bazaga yozib, worker'ga qoldiradi |
| **bot** | Mijoz boti; guruh va shaxsiy (Telegram Business) xabarlarini qabul qilib saqlash | AI, hisobot, fon vazifalar yo'q |
| **userbot** | Buyurtma uchun mijozga birinchi xabar, a'zolarni skanerlash, kompaniya akkauntining shaxsiy chatlarini saqlash | Tahlil qilmaydi |
| **worker** (yangi) | Navbatni qayta ishlash (ovoz->matn, tasnif, qoidalar), hisobotlar, zaxira, tozalash, Odoo tekshiruvi, MyZvonki'dan tortish, ogohlantirishlar | Telegram'dan xabar qabul qilmaydi |
| **dashboard** | CEO ko'rinishi, AI agentlar va ularni yaratish | O'zining `dashboard` bo'limidan boshqa joyga yozmaydi |
| **agent_bots** | Yaratilgan agentlarning Telegram botlari (har botga bitta o'quvchi) | Birinchi bo'lib yozmaydi, guruhlarga yozmaydi; token boshqa dasturda ishlatilsa to'xtaydi |

### 3.5. Yagona muloqot modeli (eng muhim qism)

Hamma kanal quyidagi tushunchalarga keltiriladi:

| Tushuncha | Nima | Misol |
|---|---|---|
| **Kanal** | Muloqot turi | `telegram_group`, `telegram_private`, `phone_call` |
| **Suhbat** (conversation) | Kanal ichidagi doimiy "joy" | Telegram guruhi; shaxsiy chat; bitta telefon raqami bilan qo'ng'iroqlar |
| **Ishtirokchi** (identity) | Kim yozdi yoki qo'ng'iroq qildi | Telegram akkaunt yoki telefon raqami; xodimmi yoki mijozmi |
| **Mijoz** (customer) | Bir nechta ishtirokchini bitta odam/kompaniyaga bog'laydi | Odoo `res.partner` (+ obyekt) |
| **Xodim** (staff) | Kompaniya tomoni | Menejer: Telegram akkauntlari, telefoni, mas'ul guruhlari |
| **Hodisa** (event) | Bitta xabar yoki qo'ng'iroq | Matn, ovozli xabar, tahrir, kiruvchi/chiquvchi qo'ng'iroq |
| **Tasnif** (classification) | Hodisaning AI tahlili | Toifa, kayfiyat, mazmun, menejer kerakmi |
| **Tekshiruv** (SLA check) | "Javob berildimi" | So'rov vaqti, muddat, natija, kim javob berdi |

```
 customer (Odoo partner) 1 ---- * identity (telegram akkaunt / telefon) * ---- 0..1 staff
                                       |
                                       | yozadi / qo'ng'iroq qiladi
                                       v
 conversation (kanal, tashqi ID, mijoz?, menejer?) 1 ---- * event (xabar / qo'ng'iroq)
        |                                                         |
        * sla_check (so'rov -> javob)                             1 classification
```

Hozirgi jadvallar shunday o'tadi:

| Hozir (`monitor.db`) | Maqsad (`comms` / `contacts`) |
|---|---|
| `groups` | `conversations` (kanal = `telegram_group`) |
| `messages` | `events` (tur = `message`) |
| `classifications` | `classifications` (o'zgarishsiz) |
| `manager_checks` | `sla_checks` |
| `daily_stats`, `daily_summaries` | o'zgarishsiz (+ `channel` ustuni) |
| guruhdagi `user_id`, `telegram_users` (PostgreSQL) | `identities` |
| Odoo partner | `customers` (Odoo ID bilan; nomi, telefoni, menejeri nusxasi) |

### 3.6. Qayta ishlash navbati (pipeline)

```
hodisa saqlandi (pending)
   -> [ovozli xabar / qo'ng'iroq] ovoz -> matn
   -> AI tasnif: kompaniyaga oidmi, toifa, kayfiyat, mazmun, menejer kerakmi
   -> qoidalar: 5 daqiqa (SLA), shikoyat ogohlantirishi, javobsiz qo'ng'iroq ...
   -> natijalar: kunlik sonlar, hisobot, dashboard, ogohlantirish
```

- Har qadam o'z natijasi va holatini yozadi (`pending` / `done` / `failed`) — worker qayta yonsa,
  qolgan joyidan davom etadi; bitta hodisani ikki marta qayta ishlash zarar qilmaydi (idempotent).
- Xato bo'lsa N marta qayta uriniladi, keyin `failed` — dashboard'ning «Tizim» sahifasida ko'rinadi.
- Qabul qilish (kanal) hech qachon qayta ishlashni kutmaydi.
- **Sig'im:** lokal gemma4:12b ~1 soniya/xabar. Qo'ng'iroqlar (uzun matn) qo'shilsa, navbat
  uzayishi mumkin — shunda ustuvorlik (shikoyat va so'rovlar oldin) va kerak bo'lsa bulutli
  modelga qo'shimcha yo'naltirish sozlamasi qo'shiladi.

### 3.7. AI qatlami (`app/ai/`)

- **Gateway** — hamma model chaqiruvi shu yerdan: provayder (Ollama, OpenAI, Anthropic), har
  vazifaga o'z modeli, JSON sxema, qayta urinish, timeout, token va xarajat hisobi, matnsiz log.
- **Vazifa → model** (sozlamada, kodda emas):

  | Vazifa | Hozir | Izoh |
  |---|---|---|
  | Xabar tasnifi | lokal gemma4:12b | Bepul, GPU'da |
  | Kunlik xulosa | lokal gemma4:12b | |
  | Guruhdagi ovozli xabarlar | OpenAI gpt-4o-transcribe | |
  | Qo'ng'iroq yozuvlari | — | Lokal `rubaistt_v2_medium` o'lchovda aniqroq chiqqan (so'z xatosi 17,6% vs 34,8%) |
  | Dashboard chat va agentlar | OpenAI gpt-5.4-mini | Keyin Claude |

- **Promptlar** versiyalanadi va tahrirlanadi (dashboard agentlarida bor; keyin tasnif prompti ham).
- **Hozir bor (dashboard, 2026-10-20):** agentlar daraxti (≤ 3 qavat), har bir agentga model tanlash,
  «Agent qo'shish» (AI vazifadan model, joy, vosita guruhlari, ulanishlar va promptni taklif qiladi),
  vosita guruhlari (monitoring, Odoo — faqat o'qish, ko'rinish), agentning o'z Telegram boti.
  Kod: `dashboard/assistant/{registry,team,builder,models,odoo_tools}.py`, `dashboard/agent_bots.py`.
- **Vositalar reyestri:** har bir modul o'qish vositalarini e'lon qiladi — `comms` (shikoyatlar,
  javobsiz so'rovlar...), `orders`/Odoo (mijozning buyurtmalari, balansi), `calls` (javobsiz
  qo'ng'iroqlar, qo'ng'iroq mazmuni). Agentlar shu ro'yxatdan tanlaydi. «Agent qo'shish» sahifasi
  shu reyestrga quriladi: yangi modul qo'shilsa, uning vositalari agentlarga avtomatik chiqadi.
- **Evals va tracing:** tasnif uchun real xabarlardan belgilangan test to'plami, agentlar uchun
  savollar to'plami; har bir chat xabari bo'yicha qaysi agent, qaysi vosita, qancha token va vaqt.

### 3.8. Ma'lumotlar bazasi

Bitta PostgreSQL (serverda allaqachon bor, 17-versiya), bo'limlarga ajratilgan:

| Bo'lim (schema) | Nima | Kim yozadi |
|---|---|---|
| `orders` | Hozirgi 12 jadval (buyurtmalar, a'zoliklar, balans...) | api, bot, userbot |
| `comms` | Suhbatlar, hodisalar, tasniflar, tekshiruvlar, kunlik sonlar | kanallar, worker |
| `contacts` | Mijozlar, identifikatorlar, xodimlar | worker (Odoo'dan), kanallar |
| `integration` | Odoo'ga uzatiladigan yozuvlar navbati, MyZvonki holati | api, worker |
| `dashboard` | Ko'rinish, chat tarixi, agent promptlari | dashboard |
| `reporting` | Dashboard uchun tayyor ko'rinishlar (view) — dashboard bilan **shartnoma** | migratsiyalar |

- Jadval o'zgarishlari **Alembic migratsiyalari** bilan (hozir: ishga tushganda `create_all`).
- Dashboard alohida **faqat o'qish** huquqli foydalanuvchi bilan ulanadi va faqat `reporting`
  view'larini o'qiydi — ichki jadvallar o'zgarsa, view moslanadi, dashboard buzilmaydi.
- Zaxira: har kuni `pg_dump`, 14 tasi saqlanadi; fayllar (`data/`) alohida.
- SQLite faqat testlar va demo uchun qoladi.

### 3.9. Sozlamalar va kalitlar

- **`.env`** — faqat maxfiy narsalar va shu kompyuterga xos yo'llar: tokenlar, API kalitlari, baza
  paroli.
- **Biznes sozlamalari bitta joyda** (`config/settings.toml` yoki bazadagi jadval) — hamma jarayon
  va dashboard shu yerdan o'qiydi: `TIMEZONE`, `REPORT_TIME`, `SLA_MINUTES`, `MANAGER_TITLES`,
  `STAFF_NAME_KEYWORDS`, ish vaqti.
- `config.py` modullar bo'yicha bo'linadi: `telegram`, `odoo`, `ai`, `monitoring`, `reports`,
  `calls`.
- Kalitlar logga tushmaydi (hozirgi `_SecretFilter` hamma jarayonga).

### 3.10. Ishga tushirish va kuzatish

- Tizim **shu AI-SERVER'da qoladi** (lokal modellar GPU'da). Har bir jarayon xizmat sifatida
  ishlaydi: Task Scheduler watchdog'i (S4U, har daqiqa va server yonganda — boshqa loyihalardagidek).
  `deploy/windows/` da hammasi uchun bitta start/stop/status skripti.
- **Health:** har bir jarayon har daqiqada `process_heartbeats` jadvaliga "tirikman" deb yozadi;
  dashboard «Tizim» sahifasi hammasini ko'rsatadi; jarayon N daqiqa jim bo'lsa, worker
  `ADMIN_CHAT_ID` ga xabar beradi.
- **Loglar:** bitta `logs/` papka, har jarayonga alohida, aylanuvchi (rotating); xabar matni va
  kalitlar yozilmaydi.
- Linux'ga ko'chish varianti (`docker-compose`, systemd) `deploy/linux/` da saqlanadi.

### 3.11. Xavfsizlik va shaxsiy ma'lumotlar

- **Eng kam huquq:** dashboard bazaga faqat o'qish bilan ulanadi; AI agentlar faqat reyestrdagi
  vositalarni chaqiradi; Odoo'ga yozish faqat api orqali, aniq oqimlar bilan.
- **Shaxsiy ma'lumot:** shaxsiy chatlar va qo'ng'iroq yozuvlari — saqlash muddati (masalan,
  yozuvlar 180 kun), kim ko'ra/eshita olishi va xodimlar roziligi oldindan belgilanadi. Dashboard
  paroli, keyin rollar (CEO, menejer — faqat o'z guruhlari).
- **Prompt injection:** mijoz matni AI uchun faqat ma'lumot, buyruq emas (hozirgi qoida).
- **Ochiq endpointlar:** api faqat HTTPS orqali tashqariga chiqadi; Odoo uchun API kaliti;
  MyZvonki webhook'i uchun maxfiy kalit yoki imzo tekshiruvi.

### 3.12. Qo'shni loyihalar

Shu serverda yana `dunyabunya ceo agent` (CEO Telegram yordamchisi, Odoo bilan ishlaydi) va
`telegram-odoo-bot` (hujjat rasmlarini Odoo'ga biriktiradi) ishlaydi. Ular alohida loyiha bo'lib
qoladi. Keyinchalik CEO agent ham shu loyihaning vositalar reyestridan foydalanishi mumkin
(masalan, "kecha qaysi guruhda javobsiz so'rov ko'p bo'ldi"). api'dagi CEO agent hisobotlari
proksisi o'sha loyihaga yoki umumiy reverse proxy'ga o'tkaziladi.

---

## 4. Yangi kanal va integratsiyalarni ulash

### 4.1. Shaxsiy chatlar (Telegram)

| Yo'l | Qanday | Afzalligi | Kamchiligi |
|---|---|---|---|
| **A. Kompaniya akkaunti** (hozirgi userbot) | userbot shaxsiy xabarlarni allaqachon qabul qiladi (avto-javob) — ularni `comms` ga saqlash kifoya | Eng oson, yangi akkaunt kerak emas | Faqat shu bitta akkaunt |
| **B. Xodimlar akkauntlari — Telegram Business** | Xodim (Telegram Premium) Sozlamalar → Telegram Business → Chatbotlar'da bizning botni ulaydi; bot uning shaxsiy chatlaridagi xabarlarni oladi (BotFather'da Business Mode yoqiladi) | Xodim parolini yoki sessiyasini serverda saqlamaymiz; xodim o'zi qaysi chatlarni ruxsat berishini tanlaydi | Har xodimga Premium; xodim roziligi |
| C. Har xodim uchun userbot sessiyasi | Telethon bilan xodim akkauntiga kirish | — | **Tavsiya etilmaydi:** akkaunt to'liq nazorati serverda, Telegram bloklashi mumkin |

Tavsiya: avval **A**, keyin **B**. Shaxsiy chatdagi 5 daqiqa qoidasi: mijoz yozdi → akkaunt egasi
(yoki istalgan xodim) N daqiqada javob berishi kerak.

### 4.2. Qo'ng'iroqlar (MyZvonki)

- **Ulanish:** MyZvonki API — qo'ng'iroq tugaganda api'ga webhook keladi yoki worker har N daqiqada
  ro'yxatni tortadi (ularning API imkoniyatiga qarab tanlanadi).
- **Saqlanadi:** hodisa (tur = `call`): yo'nalish, raqam, xodim, boshlanish vaqti, davomiylik, javob
  berildimi, yozuv (faylning o'zi yoki faqat havola), transkript.
- **Qayta ishlash:** yozuv → ovoz->matn → tasnif (bitta qo'ng'iroq = bitta hodisa) → qoidalar
  (masalan, javobsiz kiruvchi qo'ng'iroqqa N daqiqada qayta qo'ng'iroq qilinmadi → "javobsiz").
- **Mijozni topish:** telefon raqami → `contacts` (Odoo partner telefoni bilan solishtiriladi;
  raqamni bir xil shaklga keltirish `normalize_uz_phone` da bor).

### 4.3. Odoo

- **Bitta eshik:** Odoo bilan faqat `integrations/odoo` gaplashadi; uni faqat api va worker
  jarayonlari ishlatadi. Bot api orqali so'raydi.
- **Istisno — dashboard agentlari:** sahifada yaratilgan agentlar Odoo'ga **o'z cheklangan
  foydalanuvchisi** bilan (har ulanish alohida login + API kalit, DPAPI bilan shifrlangan) va **faqat
  o'qish** uchun ulanadi (`dashboard/assistant/odoo_tools.py`: 4 ta o'qish metodi, ruxsat etilgan
  modellar ro'yxati). Bu integratsiya foydalanuvchisining kalitini dashboard'ga bermaslik uchun
  ataylab shunday: agentning huquqi Odoo'ning o'zida cheklanadi.
- **Odoo → biz:** webhook (buyurtma), balans sinxronlash; worker vaqti-vaqti bilan kerakli partner
  maydonlarini (nomi, telefonlari, menejeri, Telegram ID'lari) `contacts` ga nusxalaydi — mijozni
  tez topish uchun.
- **Biz → Odoo:** lidlar (`crm.lead`), kontakt tahrirlari, fayllar. Odoo olib ketadigan 6 xil
  "olish + belgilash" endpointi bitta umumiy mexanizmga keltiriladi (`integration.outbox` jadvali,
  endpointlar hozirgidek ishlashda davom etadi).
- **Monitoring bilan bog'lash:** har bir suhbat (guruh) Odoo mijozi yoki obyektiga bog'lanadi —
  dashboard'da qo'lda yoki avtomatik (guruh a'zolari va partnerning Telegram ID'si bo'yicha). Shunda
  dashboard va agentlar "bu guruh qaysi mijoz, buyurtmalari, balansi, menejeri" ni ko'radi. Keyin,
  ruxsat bilan: shikoyat yoki javobsiz so'rov → Odoo'da vazifa.

### 4.4. Har qanday yangi kanal uchun tartib

1. `channels/<kanal>/` — tashqi ma'lumotni `event` ga aylantiradigan adapter (+ kerak bo'lsa
   `integrations/<tizim>/` da client).
2. Kanal turini va qoidalarini sozlamaga qo'shish (SLA muddati, STT modeli).
3. `reporting` view'lariga kanal ustuni — dashboard va hisobot o'zi ko'rsatadi.
4. Kerak bo'lsa yangi o'qish vositalari — agentlar ularni reyestrdan oladi.
5. Testlar: tashqi tizimning yozib olingan namunaviy javoblari bilan (haqiqiy API chaqirilmaydi).

---

## 5. Modullar orasidagi qoidalar

| Modul | Kimni chaqira oladi | Nima qilmaydi |
|---|---|---|
| `entrypoints/` | hamma modulni ulaydi | O'zida biznes mantiq saqlamaydi |
| `channels/*` | `domain`, `storage` | Bir-birini chaqirmaydi; AI'ni chaqirmaydi; Odoo'ga bormaydi |
| `pipeline/` | `domain`, `ai`, `storage` | Telegram'ga to'g'ridan-to'g'ri yozmaydi (`alerts` orqali) |
| `integrations/odoo` | `storage` | Faqat api va worker jarayonlarida ishlatiladi |
| `domain/*` | `storage` | Telegram, Odoo, AI borligini bilmaydi |
| `ai/` | tashqi provayderlar | Boshqa modullarni import qilmaydi (vositalarni modullar o'zi ro'yxatga yozadi) |
| `reports/` | `domain`, `ai`, `storage` | Raqamni modeldan olmaydi — faqat SQL |
| `dashboard/` | `reporting` view'lari, `dashboard` bo'limi | `app` paketini import qilmaydi; boshqa bo'limlarga yozmaydi |

Umumiy qoidalar: raqamlarni faqat SQL/kod hisoblaydi; har bir tashqi tizimga bitta modul orqali
boriladi; mijoz matni hech qachon buyruq bo'lmaydi; testlar tashqi tizimlarni chaqirmaydi.

---

## 6. O'tish rejasi

Tizim har bosqichda ishlab turadi. Har bosqich alohida tugaydi va alohida qaytariladi.

| Bosqich | Nima qilinadi | Foydalanuvchi uchun nima o'zgaradi | Hajm | Xavf |
|---|---|---|---|---|
| **0. Tartib** | 4 jarayon uchun watchdog (server yonganda o'zi turadi), loglar bitta joyda, biznes sozlamalari bitta joyda, shu hujjat | Server qayta yonsa ham hammasi o'zi ishlaydi | kichik | past |
| **1. Worker ajratish** | Fon vazifalarni (tasnif, 5 daqiqa qoidasi, hisobot, zaxira, tozalash, Odoo tekshiruvi) botdan `worker` jarayoniga ko'chirish | Mijoz boti monitoringdan mustaqil, barqarorroq | kichik | past |
| **2. Papkalarga ajratish** | Xulq o'zgarmaydi: `fastapi_app.py` → routerlar, `monitor_*` → `comms`/`pipeline`/`reports`, `odoo_client` → `integrations/odoo`. Mavjud testlar himoya qiladi | Hech narsa | o'rta | past |
| **3. Yagona muloqot modeli + PostgreSQL** | `comms`/`contacts` bo'limlari, Alembic, `monitor.db` ma'lumotlarini ko'chirish, dashboard'ni `reporting` view'lariga o'tkazish | Dashboard o'sha. Ko'chirish paytida monitoring 10–20 daqiqa to'xtaydi (Telegram xabarlarni 24 soatgacha saqlab turadi — hech narsa yo'qolmaydi) | katta | o'rta |
| **4a. Shaxsiy chatlar** | Avval kompaniya akkaunti, keyin Telegram Business | Hisobot va dashboard'da yangi kanal | o'rta | past |
| **4b. Qo'ng'iroqlar (MyZvonki)** | Adapter, yozuvlar, STT, qo'ng'iroq qoidalari | Qo'ng'iroqlar hisobot va dashboard'da | o'rta–katta | o'rta |
| **4c. Odoo bilan bog'lash** | Suhbat ↔ mijoz/obyekt, dashboard'da buyurtma va balans, agentlarga Odoo vositalari | "Bu guruh qaysi mijoz" ko'rinadi | o'rta | past |
| **5. AI platforma** | Yagona AI gateway, vositalar reyestri, ~~«Agent qo'shish»~~ (dashboard'da 2026-10-20 da qilingan), evals, tracing | O'z agentlarini yaratish | o'rta | past |

Tartib sababi: 0–2 — xavfi past tayyorgarlik; 3 — yangi kanallar uchun poydevor; 4a/4b/4c bir-
biriga bog'liq emas, istalgan tartibda; 5 — 2-bosqichdan keyin parallel qilinishi mumkin.

---

## 7. Hal qilinishi kerak bo'lgan savollar

1. **Shaxsiy chatlar:** qaysi akkauntlar — faqat kompaniya akkaunti yoki xodimlarning o'zi ham
   (Telegram Business, Premium, rozilik)?
2. **MyZvonki:** API kirish (kalit, webhook bormi), yozuvlarni serverga yuklab saqlaymizmi yoki
   faqat havolani?
3. **Tashqi manzil:** Odoo va MyZvonki webhook'lari uchun ommaviy HTTPS manzil kerak — hozir
   bunday manzil yo'q (eski server o'chirilgan).
4. **Saqlash muddati va huquqlar:** xabarlar va yozuvlar qancha saqlanadi, dashboard'da kim nimani
   ko'radi (CEO hammasini, menejer o'z guruhlarini)?
5. **Bitta PostgreSQL'ga o'tish:** 3-bosqichdagi qisqa to'xtashga rozilik.
6. **Nom:** loyiha endi "Odoo–Telegram integratsiyasi"dan kengroq. Papka nomi qolsinmi
   (unga bog'liq skriptlar va vazifalar bor) yoki faqat README va repo'da yangi nom?

---

## 8. Atamalar

| Atama | Ma'nosi |
|---|---|
| Jarayon (process) | Alohida ishlaydigan dastur: api, bot, userbot, worker, dashboard |
| Modulli monolit | Bitta dastur, ichida chegaralari aniq modullar (mikroservis — har modul alohida dastur) |
| Kanal / adapter | Tashqi manbadan (Telegram, MyZvonki) ma'lumotni umumiy shaklga keltiruvchi qism |
| Hodisa (event) | Bitta xabar yoki qo'ng'iroq yozuvi |
| Pipeline / navbat | Hodisalar ketma-ket o'tadigan qayta ishlash qadamlari |
| Worker | Navbatni fonda qayta ishlaydigan jarayon |
| Idempotent | Bir ishni ikki marta qilish natijani buzmaydi |
| Schema (bo'lim) | Bitta bazadagi jadvallar guruhi |
| View (ko'rinish) | Bazadagi tayyor so'rov; dashboard shu orqali o'qiydi |
| Migratsiya (Alembic) | Baza tuzilishi o'zgarishlarini versiyalab qo'llash |
| Webhook | Tashqi tizim o'zi bizga xabar yuboradi |
| Polling | Biz tashqi tizimdan vaqti-vaqti bilan so'raymiz |
| Heartbeat / health | Jarayon "tirikman" deb belgi berishi |
| Gateway | Tashqi xizmatga (AI) yagona kirish joyi |
| Vositalar reyestri | AI agentlar chaqira oladigan tayyor vositalar ro'yxati |
| Evals / tracing | AI sifatini test to'plamida tekshirish / har chaqiruvni kuzatish |
