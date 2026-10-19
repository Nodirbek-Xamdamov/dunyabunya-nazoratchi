# dunyabunya monitoring dashboard (CEO uchun)

Mijoz guruhlari monitoringi ma'lumotlarini (shikoyatlar, toifalar, javob tezligi, dinamika) ko'rsatadigan
Streamlit ilovasi. Botning bazasini **faqat o'qiydi**, Telegram va Odoo'ga hech narsa yubormaydi.
O'ng pastki burchakdagi dumaloq tugma **AI yordamchi** chatini ochadi (hozir OpenAI, keyin Claude): u savollarga javob beradi va
dashboard'ni CEO xohlagancha o'zgartiradi — bo'limlar, sahifalar, nomlar, yangi grafik/jadvallar, dizayn. Faqat
dashboard'ning o'z fayllari (`data\state.db`, `.streamlit\config.toml`) o'zgaradi; chatni va bazani o'chirib bo'lmaydi
(pastda: "AI yordamchi").

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

## Sozlamalar (`dashboard\.env`)

`dashboard\.env.example` dan nusxa oling: `dashboard\.env`. Botning asosiy `.env` fayli o'qilmaydi.

| Sozlama | Standart | Ma'nosi |
|---|---|---|
| `DB_PATH` | `../data/monitor.db` | Monitoring bazasi (faqat o'qish) |
| `REPORTS_DIR`, `BACKUP_DIR` | `../reports`, `../backups` | Excel hisobotlar va zaxira nusxalar (faqat ro'yxat va yuklab olish) |
| `REPORT_TIME` | `11:00` | **Botning `REPORT_TIME` qiymati bilan bir xil bo'lsin** (production'da `00:00`) |
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
| `THEME_CONFIG_PATH` | `.streamlit/config.toml` | Chatda tanlangan dizayn yoziladigan Streamlit sozlamasi (pastda: "Dizayn") |

O'zgartirgandan keyin dashboard'ni qayta ishga tushiring (`-Stop`, keyin qayta).

## AI yordamchi (o'ng pastdagi tugma)

Har sahifaning o'ng pastki burchagida dumaloq tugma turadi; bosilsa chat paneli ochiladi (yana bosilsa yopiladi).
Panel ixcham: sahifadagi ma'lumot ko'rinib turishi uchun unda faqat oxirgi 6 ta xabar, to'liq suhbat — ⤢ tugmasida.
Panel agent ishlayotganda va sahifa yangilanganda ham ochiq qoladi.

To'rtta agent: **Boshqaruvchi** umumiy savollarga o'zi javob beradi va ishni uch agentga beradi —
**Savol-javob agenti** monitoring ma'lumotini tayyor so'rovlar orqali faqat o'qiydi ("Bugun nechta shikoyat
bo'ldi?", "Qaysi guruhda javobsiz ko'p?"); **Ko'rinish agenti** dashboard tarkibini o'zgartiradi; **Dizayn
agenti** ko'rinishini (rang, shrift, fon) o'zgartiradi.

CEO hamma narsani o'zgartira oladi (qaror: 2026-10-17); faqat chatni va bazani o'chirib bo'lmaydi:

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
- **Dizayn:** yorug'/qorong'i rejim, asosiy rang, fon, kartalar, matn va chap panel ranglari, shrift (20 ta Google
  shrifti), burchaklar, matn o'lchami, grafik ranglari va qo'shimcha CSS. Boshqa sayt skrinshotini biriktirib "shu
  saytdek qil" desa, agent uning ranglari, burchaklari va shrift uslubini oladi. Dizayn o'zgarganda ochiq sahifalar
  ~2 soniyada o'zi yangilanadi (chat paneli yana ochiladi).

- Ma'lumot o'chirilmaydi: olib tashlangan bo'lim faqat yashiriladi, chatda yaratilgan bo'lim o'chirilsa ham
  **Qaytarish** bilan qaytadi. Har o'zgarish `data\state.db` da versiya bo'lib saqlanadi:
  **Qaytarish** tugmasi (AI'siz) oxirgi xabar o'zgarishlarini bekor qiladi, **Tarix** — istalgan holatga
  qaytish, **Asl holat** — hammasini standart ko'rinishga qaytarish. Ko'rinish hamma ko'ruvchilar uchun bitta.
- Chat tarixi ham shu faylda saqlanadi (sahifa yangilansa yoki dashboard qayta ishga tushsa ham qoladi).
  ⤢ tugmasi — kunlar bo'yicha to'liq suhbat; **Tarix** tugmasi — o'zgarishlar lentasi (qo'shildi / olib
  tashlandi / ko'chirildi / nomlandi / dizayn / bekor qilindi, vaqt, sahifa va bo'limlar) va istalgan holatga
  qaytish. **Asl holat** hammasini (bo'limlar, nomlar, o'z sahifalari, dizayn) standartga qaytaradi.
- **Rasm biriktirish:** chat maydonidagi ＋ tugmasi (yoki rasmni sudrab tashlash) — bir xabarga 3 tagacha rasm
  (png/jpg/webp, 10 MB gacha). Dashboard'ning bir qismi skrinshoti: "Shu joyini tepaga chiqar", "Buni olib
  tashla", "Bu raqam nima?" — agent rasmdagi sarlavhadan bo'limni topadi. Boshqa sayt skrinshoti: "Shu saytdek
  qil" — Dizayn agenti uning ko'rinishini oladi.
  Rasmlar kichraytirilib `data\chat_files\` ga saqlanadi (gitga kirmaydi) va chat tarixida ko'rinadi; rasm AI
  provayderiga yuboriladi (skrinshotdagi mijoz ma'lumotlari ham).
- AI provayderiga (OpenAI / Anthropic) yuboriladi: CEO yozgan matn, sahifa va bo'lim nomlari, savolga javob uchun kerakli ma'lumot
  parchalari (raqamlar, guruh nomlari, shikoyat xulosalari). Ko'rinish va Dizayn agentlari mijoz xabarlarining
  matnini ko'rmaydi (yangi bo'lim tuzishda faqat ustunlar, raqamlar va guruh nomlari).
- Loglarga xabar matni va kalit yozilmaydi (faqat agent/tool nomlari, token soni, request id).
- Bitta xabar odatda 3–4 model so'rovi. `gpt-5.4-mini` bilan bir xabar 1 sentdan kam; Claude Opus 5.5 bilan
  taxminan 3–8 sent. Claude'ga o'tish: `CHAT_PROVIDER=anthropic` va `ANTHROPIC_API_KEY`, keyin qayta ishga tushirish.
- Bo'limlar ro'yxati va ularning nomlari kodda (`views\*.py` ichida `layout.Block`, nomlari `ui\i18n.py` da
  `blk.*`). Yangi bo'lim kodga qo'shilsa, u avtomatik chiqadi va agent uni taniydi.

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
  Savol-javob, Ko'rinish, Dizayn agentlari). Agent kartasini bosish uning sozlamalarini ochadi: model, vositalar
  va **system prompt** — tahrirlash, saqlash, standartga qaytarish, tarixdan tiklash. Saqlangan prompt chatdagi
  keyingi xabardan ishlatiladi (`data\state.db`, `agent_prompts` jadvali). Faqat ko'rsatma qismi tahrirlanadi;
  bo'limlar katalogi, ma'lumot manbalari, ta'riflar va CSS selektorlarini dashboard promptning oxiriga o'zi
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
  manbalaridan quriladi (AI manba, parametr va ko'rinishni tanlaydi, xolos). Ko'rinish va Dizayn agentlari faqat
  `data\state.db` dagi qatlamni o'zgartiradi. Mijoz xabaridagi matn agentga buyruq bo'lib o'tmaydi; har o'zgarish
  **Qaytarish** bilan bekor qilinadi.
- Qo'shimcha CSS tekshiriladi: rang, fon, shrift, chegara, soya mumkin; narsani yashiradigan yoki suradigan xossalar
  (`display`, `visibility`, `opacity`, `position`, `transform`, o'lchamlar ...), tashqi fayl (`url(`, `@import`) va
  chatga tegishli qoidalar rad etiladi; chat tugmasi har doim ko'rinadi. Matn va fon kontrasti ham tekshiriladi.
- Dashboard LAN'ga ochilsa, parolni bilgan har kim chatdan ham foydalana oladi va ko'rinishni o'zgartira oladi.

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
| Dizayn yoqmadi / o'qish qiyin | Chatda "asl dizaynga qaytar" yoki **Qaytarish** / **Asl holat** tugmalari |
| Sahifa o'zi bir marta yangilandi | Dizayn o'zgargandan keyin shunday bo'lishi kerak (yangi ranglar shu bilan qo'llanadi) |
| Chatda yaratilgan bo'limda "ma'lumot olinmadi" | Bo'lim sozlamasi endi mos emas (masalan guruh nomi o'zgargan): chatda yangilashni so'rang |

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
                         yaratilgan bo'limlar), design (dizaynni qo'llash), overrides (o'z nomlar)
  views\                 sahifalar: har biri bloklar ro'yxati (SQL yo'q); own — CEO'ning o'z sahifalari;
                         agents — AI agentlar daraxti va system prompt tahriri
  assistant\             AI agentlar: llm (OpenAI / Claude), orchestrator, data_agent + data_tools, ui_agent,
                         design_agent, prompts
  core\theme.py          dizayn qiymatlari, tekshiruvi va config.toml'ning [theme] qismi
  data\state.db          ko'rinish o'zgarishlari, chat tarixi va agentlar promptlari (gitga kirmaydi)
  demo\                  demo baza yaratuvchi
  tests\                 pytest + bench + explain
  .streamlit\config.toml  Streamlit sozlamalari; [theme] qismini dashboard o'zi yozadi (chatdagi dizayn)
  run_dashboard.ps1, requirements.txt, .env.example
```
