# W3-01 · Maydonlar xaritasi (sahifa → ko'rsatkich → view → ustun)

Sana: 2026-10-26. Prototip: `D:\Demos\odoo telegram integration\dashboard` (kod o'zgartirilmadi, faqat o'qildi).
Hozirgi manba: botning SQLite bazasi `data\monitor.db` (jadvallar `messages`, `classifications`, `manager_checks`,
`groups`, `daily_stats`, `daily_summaries`, `media_files`) va kamera servisining `camera.db` (`cam_*` jadvallari).
Barcha SQL bitta faylda: `dashboard\core\queries.py`; ko'rsatkich ta'riflari: `dashboard\core\metrics.py`.

Yangi manba: `CONTRACTS.md` §6 view'lari. **Ustun nomlari taklif**, aniq nomlarni W0-04 yozadi.
Belgilar: ✅ view'da bor (yoki §1 obyektidan to'g'ridan-to'g'ri chiqadi) · ⚠️ view bor, lekin kerakli ustun/obyekt
shartnomada yo'q (`[CONTRACT]`) · ❌ ma'lumot manbasi yo'q · ⛔ W3 doirasidan tashqari (qaror kerak).

## 0. Asosiy farq: xabar → episode

Prototip **har bir xabarni** alohida tasniflaydi (`classifications`: bitta kategoriya, kayfiyat, xulosa,
`needs_manager`). Yangi rejada tahlil **episode** bo'yicha (`analyses`: bir nechta kategoriya, jiddiylik,
ishonchlilik, dalil). Shuning uchun "nechta shikoyat" kabi raqamlar **episode soni** bo'ladi (xabar soni emas).
Bu ta'rif o'zgarishi `metrics.py` da qayta yoziladi; dizayn o'zgarmaydi.

## 1. Umumiy tushunchalar (hamma sahifada)

| Prototipdagi tushuncha | Hozir (SQLite) | Yangi view → ustun | Holat |
|---|---|---|---|
| Mijoz xabari | `messages.is_staff = 0` | `v_messages_public.sender_role <> 'employee'` (`people.role`) | ✅ |
| Kompaniyaga oid | `classifications.is_company_related = 1` | `v_analysis_feed.relevance = 'yes'` | ✅ |
| Filtrlangan (aloqasiz) | `is_company_related = 0` | `episodes.relevance = 'no'` soni — feed'da `no` episodlar bo'lmasligi mumkin | ⚠️ `v_daily_category_counts.filtered` kerak |
| Toifa (8 ta, `config/categories.toml`) | `classifications.category` (bitta) | `v_analysis_feed.categories[]` (bir nechta) | ✅ (ko'p toifa: hisob qoidasi W2 bilan kelishiladi) |
| Kayfiyat Shikoyat/Ijobiy/Neytral | `classifications.sentiment` | `v_analysis_feed.sentiment` | ✅ |
| Jiddiylik | yo'q | `v_analysis_feed.severity` | ✅ (yangi filtr, W3-03) |
| Qisqa xulosa | `classifications.summary` | `v_analysis_feed.summary` | ✅ |
| Ishonchlilik + "AI taxmini" | **yo'q** | `v_analysis_feed.confidence`, `model_id`, `prompt_version` | ✅ (prototipga belgi qo'shiladi) |
| Asl xabarga havola | **yo'q** (faqat matn ko'rsatiladi) | `v_analysis_feed.evidence[]` (xabar id + iqtibos) + `v_messages_public.tg_chat_id, tg_message_id, chat_username` | ⚠️ havola qurish uchun chat username / `-100…` id ustuni kerak |
| Javob kerak (menejer) | `classifications.needs_manager` | `v_analysis_feed.needs_reply` | ⚠️ `analyses` da shu maydon yo'q |
| Tasniflanmagan / xato | `messages.status = pending/failed` | `jobs` (status) | ❌ view yo'q (`v_pipeline_status` kerak) |
| Qoida bilan (AI'siz) ajratilgan | `classifications.model = 'rule'` | `v_analysis_feed.model_id` | ✅ |
| Davr, kun chegarasi | `sent_at_utc` + Tashkent offset | UTC ustunlar, kun = `Asia/Tashkent` 00:00 (§4) | ✅ |
| Guruh nomi | `groups.title` | `sources.name` (view orqali) | ✅ |
| Guruh menejeri, adminlar, bot guruhdami | `groups.manager_username, admin_user_ids, is_present` | — | ⚠️ `sources` da yo'q |

## 2. Sahifalar

### 2.1 Umumiy (`views/overview.py`)

| Ko'rsatkich (blok) | Hozir | View → ustun | Holat |
|---|---|---|---|
| Shikoyatlar KPI (soni, ulushi, o'zgarish, sparkline) | `totals`, `daily` | `v_daily_category_counts` (kun, toifa, `complaints`, `related`) | ✅ |
| Javobsiz so'rovlar KPI | `manager_checks.status='missed'` | `v_response_times.status` | ⚠️ "so'rov" obyekti shartnomada yo'q (pastda §3) |
| 5 daqiqada javob (SLA %) | answered / (answered+missed) | `v_response_times` | ⚠️ (§3) |
| Kompaniyaga oid xabarlar KPI | `totals.related` | `v_daily_category_counts.related` | ✅ |
| Ijobiy fikrlar KPI | `totals.positive` | `v_daily_category_counts.positive` | ✅ |
| Median javob vaqti KPI | `checks` → median | `v_response_times.response_s` | ⚠️ (§3) |
| Umumiy holat paneli (yashil/sariq/qizil) | KPI + ma'lumot yangiligi | yuqoridagilar + `v_pipeline_status.last_message_at` | ⚠️ |
| Diqqat talab guruhlar (xavf bali) | `groups_overview` + `checks` | `v_daily_category_counts` (guruh bo'yicha) + `v_response_times` | ⚠️ view'da guruh (`source_id`) kesimi kerak |
| Kun xulosasi (qoida) | hisoblanadi | yuqoridagilar | ✅ |
| Kun xulosasi (AI) | `daily_summaries` (`umumiy`) | `chat_context` faqat chat bo'yicha; kunlik umumiy AI matni yo'q | ❌ `v_daily_summary` kerak (W2-09) |
| Kunlik dinamika grafigi | `daily` | `v_daily_category_counts` | ✅ |
| Toifalar grafigi | `by_category` | `v_daily_category_counts` | ✅ |
| Kayfiyat doirasi | `totals` | `v_daily_category_counts` | ✅ |
| Hafta kuni × soat xaritasi | `hour_weekday` (xabar vaqti) | `v_analysis_feed.started_at` | ✅ |
| Guruh × toifa shikoyatlar | `group_category_complaints` | `v_analysis_feed` (source, categories, sentiment) | ✅ |
| So'nggi shikoyatlar | `latest_complaints` | `v_analysis_feed` + havola | ✅ |
| Hozir javob kutayotganlar | `manager_checks.status='pending'` | `v_response_times.status='pending'` | ⚠️ (§3) |
| Ma'lumot sifati ogohlantirishlari | `config/known_issues.toml` + raqamlar | fayl (DB emas) | ✅ |

### 2.2 Guruhlar (`views/groups.py`)

| Ko'rsatkich | Hozir | View → ustun | Holat |
|---|---|---|---|
| Reyting jadvali: xabar, oid, shikoyat, ijobiy, filtrlangan, javob/javobsiz/kutmoqda, oldingi davr, oxirgi faollik, xavf | `groups_overview` | `v_daily_category_counts` (+`source_id`), `v_response_times`, `v_messages_public.sent_at` | ⚠️ guruh kesimi + menejer/admin/`is_present` |
| Sparkline (guruh × kun) | `group_sparks` | `v_daily_category_counts` (+`source_id`) | ⚠️ |
| Guruh kartochkasi: menejer, adminlar, birinchi/oxirgi xabar | `group_info` | `sources` + `people` | ⚠️ menejer/admin bog'lanishi yo'q |
| Guruh KPI (5 ta) | `totals(chat_id)` | yuqoridagi kabi | ⚠️ |
| Guruh dinamikasi, toifalar, kayfiyat, javob tarixi | `daily`, `by_category`, `checks` | `v_daily_category_counts`, `v_response_times` | ⚠️ guruh kesimi |
| Javobsiz so'rovlar jadvali | `checks` | `v_response_times` | ⚠️ (§3) |
| Guruh mijozlari (yuboruvchi bo'yicha) | `group_customers` | `v_messages_public` + `v_analysis_feed` (xabar → episode bog'i) | ⚠️ episode ↔ xabar bog'i (`episode_messages`) view'da kerak |
| Suhbat lentasi (matn, transkript, reply, rasm/GIF) | `group_feed` + `media_files` | `v_messages_public` (`text`, `transcript`, `reply_to`, `media_type`, `media_path`) | ⚠️ transkript va media yo'li view'ga kirishi kerak; **xom xabar — faqat ruxsatli rol** (W3-05) |
| Xabarlarni yuklab olish | `messages_list` | `v_messages_public` | ✅ (rolga bog'liq) |

### 2.3 Bo'limlar / toifalar (`views/categories.py`)

| Ko'rsatkich | Hozir | View → ustun | Holat |
|---|---|---|---|
| Toifa kartalari, toifa × kun xaritasi | `by_category`, `category_daily` | `v_daily_category_counts` | ✅ |
| Mas'ul bo'lim | `config/categories.toml` | fayl | ✅ |
| Toifa KPI (oid, shikoyat, ijobiy, ulush) | `by_category` | `v_daily_category_counts` | ✅ |
| Toifa dinamikasi, kayfiyati | `category_daily` | `v_daily_category_counts` | ✅ |
| Qaysi guruhlarda ko'p | `category_top_groups` | `v_analysis_feed` | ✅ |
| Takrorlanayotgan mavzular (so'zlar) | `category_summaries` | `v_analysis_feed.summary` | ✅ |
| Eng faol mijozlar | `category_top_customers` | `v_analysis_feed` + qatnashchilar | ⚠️ episode qatnashchilari (`participants`) view'da kerak |
| Saqlangan AI xulosalar (toifa) | `daily_summaries` | — | ❌ `v_daily_summary` (W2-09) |
| Toifa xabarlari jadvali | `messages_list` | `v_analysis_feed` | ✅ |
| Review navbati (odam tuzatishi) — **prototipda yo'q**, W3-03 talabi | — | `review_queue` | ❌ view yo'q (`v_review_queue` kerak) |

### 2.4 Javob tezligi (`views/response.py`)

| Ko'rsatkich | Hozir | View → ustun | Holat |
|---|---|---|---|
| SLA %, javobsiz, kechikib, umuman yo'q, median, P90, so'rovlar soni | `checks` | `v_response_times` | ⚠️ (§3) |
| Javob vaqti taqsimoti, so'rov soati bo'yicha | `checks` | `v_response_times.requested_at, response_s` | ⚠️ |
| Eng yomon 15 guruh | `checks` | `v_response_times.source_id` | ⚠️ |
| Javobsiz/kechikkan ro'yxati (so'rov matni) | `checks` | `v_response_times` + `v_messages_public` | ⚠️ |
| Ish vaqti filtri | `.env WORK_HOURS` | sozlama | ✅ |

### 2.5 Xodimlar (`views/staff.py`)

| Ko'rsatkich | Hozir | View → ustun | Holat |
|---|---|---|---|
| Faol xodimlar, xabar soni, nechta guruhda | `staff_activity` (`is_staff=1`) | `v_messages_public` + `people.role='employee'` | ✅ |
| Javoblar soni, median javob (xodim bo'yicha) | `checks` → `staff_answers` | `v_response_times.responder_person_id, responder_role` | ⚠️ (§3). Faqat neytral raqam, ball yo'q — prototip ham ball bermaydi |
| Menejersiz guruhlar | `groups.manager_username IS NULL` | — | ⚠️ menejer bog'i yo'q |

### 2.6 Dinamika (`views/trends.py`)

| Ko'rsatkich | Hozir | View → ustun | Holat |
|---|---|---|---|
| Hafta-hafta, oy-oy, toifalar haftalik, 90 kun kalendari, chetlangan kunlar | `daily`, `category_daily` | `v_daily_category_counts` + `v_response_times` (javobsiz) | ✅ / ⚠️ javobsiz qismi |

### 2.7 Qidiruv (`views/search.py`)

| Ko'rsatkich | Hozir | View → ustun | Holat |
|---|---|---|---|
| Matn/transkript/xulosa bo'yicha qidiruv, guruh, toifa, kayfiyat, muallif, "javob kerak" filtrlari | `messages_list` (LIKE) | `v_messages_public` + `v_analysis_feed` | ⚠️ transkript, `needs_reply`; xom matn — ruxsatli rol |

### 2.8 Hisobotlar (`views/reports.py`)

| Ko'rsatkich | Hozir | View → ustun | Holat |
|---|---|---|---|
| Excel fayllar ro'yxati | `REPORTS_DIR` papkasi | fayl tizimi (W3-06 o'zi yaratadi) | ✅ |
| Hisobot kuni: toifa raqamlari | `daily_stats` | `v_daily_category_counts` (yoki `daily_stats`) | ✅ |
| Hisobot kuni: AI xulosalar, yuborilgan vaqt | `daily_summaries.summary_text, report_sent_at` | — | ❌ `v_daily_summary` + yetkazish jurnali (W3-07) |

### 2.9 Tizim (`views/system.py`)

| Ko'rsatkich | Hozir | View → ustun | Holat |
|---|---|---|---|
| Ma'lumot yangiligi (oxirgi xabar, oxirgi tasnif, oxirgi hisobot) | `freshness` | `v_messages_public`, `v_analysis_feed` max vaqt; hisobot — yetkazish jurnali | ⚠️ |
| Tasniflash navbati (kutmoqda, xato, AI ulushi, bo'sh transkript) | `quality` | `jobs`, `transcripts` | ❌ `v_pipeline_status` kerak |
| Guruhlar holati (jami, chiqib ketgan, menejersiz, jim) | `groups_health` | `sources` + oxirgi xabar | ⚠️ `is_present`, menejer |
| Zaxira nusxalar va baza hajmi | `BACKUP_DIR` papkasi | W0-03 zaxira papkasi; baza hajmi — Postgres | ⚠️ `v_system_health` (+`heartbeats`) kerak |
| Hisobot bilan moslik | `daily_stats_range` vs `totals` | `v_daily_category_counts` | ✅ |
| Ma'lum muammolar | `config/known_issues.toml` | fayl | ✅ |

### 2.10 Kamera (`views/kamera*.py`, `camera/*`) — boshqa model

Prototip **stol (desk) zonasi** bo'yicha ishlaydi: ismli xodim (`cam_employees`), stol (`cam_desks`), stolda o'tirish
oraliqlari (`cam_intervals`), kadr qamrovi (`cam_coverage`), namoz vaqti hisobi, ish jadvali va me'yoriy daqiqa.
Reja (D-06, D-11) esa **eshikdan kirish/chiqish**, ismsiz "Hodim N", xonada bo'lish vaqti. Hozir faqat «Jonli» sahifa ko'rinadi.

| Ko'rsatkich | Hozir | View → ustun | Holat |
|---|---|---|---|
| Bugun kim nechida keldi / ketdi | `cam_intervals` birinchi/oxirgi | `v_attendance_today.first_in, last_out` | ✅ |
| Xonada jami vaqt | stolda o'tirgan jami | `v_attendance_today.in_room_min` / `v_attendance_daily` | ✅ (ma'nosi boshqa: stol emas, xona) |
| Kun bo'yi bandlik chizig'i (timeline) | `cam_intervals` | `presence_sessions` | ⚠️ `v_presence_sessions` view kerak (§6 da yo'q) |
| Noaniqlik belgisi (`inferred`) | yo'q | `v_attendance_daily.uncertain` / `camera_events.inferred` | ⚠️ timeline uchun sessiya darajasida kerak |
| Hodim nomi | `cam_employees.name` (ism!) | `anon_persons` → "Hodim N" | ✅ (ism ko'rsatilmaydi, D-06) |
| Kunlar bo'yicha jadval, o'rtacha | `cam_intervals` | `v_attendance_daily` | ✅ |
| Kamera qamrovi % ("ma'lumot yo'q" vaqti) | `cam_coverage` | `video_segments` (uzilish belgisi) | ⚠️ `v_camera_coverage` kerak |
| Namoz vaqti, me'yoriy daqiqa, ish jadvali, farq, tanaffuslar, eng uzun o'tirish | `cam_settings`, `cam_employees` | — | ❌ rejada yo'q (D-11 bunday talab qo'ymaydi) |
| Jonli kadr (`latest.jpg`) | servis papkasi | DB emas; yuzli kadr | ⛔ W4 sohasi, maxfiylik (W4-10) |
| Kamera sozlash (stol zonasi, xodim, sozlamalar) — **bazaga yozadi** | `camera.db` ga yozish | — | ⛔ W3 faqat o'qiydi; W4 ga tegishli |

### 2.11 AI yordamchi, AI agentlar, agent qo'shish, agent botlari

| Funksiya | Hozir | Holat |
|---|---|---|
| O'ng pastdagi AI chat (OpenAI/Claude), dashboard ko'rinishini o'zgartiradi | `ui/chat.py`, `assistant/*`, `data/state.db` | ⛔ W3 "modelni chaqirmaydi" (W3 CLAUDE.md) — qaror kerak |
| «AI agentlar», «Agent qo'shish», Odoo ulanishlari | `views/agents*.py`, `assistant/odoo_tools.py` | ⛔ rejada yo'q |
| Agentlarning Telegram botlari (javob yozadi) | `agent_bots.py` | ⛔ CLAUDE.md qoida 4: Telegramga faqat reporter bot yozadi |
| Mening sahifalarim (chatda yaratilgan bo'limlar) | `data/state.db` + yuqoridagi manbalar | ✅ manbalar almashsa ishlaydi (dashboard'ning o'z fayli) |

### 2.12 Kirish (`ui/auth.py`)

| Funksiya | Hozir | View → ustun | Holat |
|---|---|---|---|
| Kirish | bitta umumiy parol (`DASHBOARD_PASSWORD`) | foydalanuvchi/rol jadvali | ❌ `dashboard_users` (login, rol, kamera ruxsati) shartnomada yo'q |
| Ko'rish jurnali | yo'q | `audit_log` (W3 yozadi, §5) | ✅ |

## 3. Yetishmayotganlar (W0 ga `[CONTRACT]` so'rovlari)

`QUESTIONS.md` ga `[W3-01] [CONTRACT]` sifatida yozildi.

1. **So'rov / javob obyekti.** Prototipning asosiy raqamlari (SLA, javobsiz, kechikib, median, P90, kutmoqda, xodim
   javoblari) "mijoz so'radi → 5 daqiqada kimdir javob berdimi" tekshiruvidan chiqadi (`manager_checks`). §1 da bunday
   obyekt yo'q, `v_response_times` esa ustunsiz. Kerak: `source_id, request_message_id, requested_at, deadline,
   status (answered|missed|pending), answered_at, response_s, responder_person_id, responder_role, late_answer_at`.
   Kim hisoblaydi (W2?) — hal qilinsin. Q7 bilan bog'liq.
2. **Guruh kesimi** `v_daily_category_counts` da: `source_id` ustuni (guruh reytingi, sparkline, guruh sahifasi).
   Va `filtered` (aloqasiz) soni.
3. **Guruh metadata** `sources` da: menejer(lar) (`people` ga bog'), adminlar, bot/akkaunt guruhda bormi (`is_present`),
   admin tekshirilgan vaqt.
4. **Asl xabarga havola** uchun: Telegram chat id (`-100…`) va (bo'lsa) public username view'da.
5. **`analyses` da "javob kerak"** (`needs_reply`) maydoni — qidiruv filtri va "javob kutayotganlar".
6. **Episode ↔ xabar va qatnashchilar** view'da (mijozlar ro'yxati, "eng faol mijozlar").
7. **`v_messages_public`** ga transkript va media yo'li (lenta, qidiruv).
8. **`v_daily_summary`** — kunlik umumiy va toifa bo'yicha AI xulosa matni (W2-09).
9. **`v_review_queue`** — W3-03 tuzatish sahifasi uchun (va W3 javobini yozish yo'li, §5).
10. **`v_pipeline_status` / `v_system_health`** — navbat (jobs) holati, bo'sh transkriptlar, heartbeats, oxirgi
    xabar/tahlil vaqti, baza hajmi.
11. **Hisobot yetkazish jurnali** (W3-07 yozadi) — §5 da W3 faqat `audit_log` va `review_queue` ga yozadi.
12. **`v_presence_sessions`** (timeline, `inferred`) va **`v_camera_coverage`** (video uzilishlari).
13. **Dashboard foydalanuvchilari va rollar** (`dashboard_users`: login, parol hash, rol, kamera ruxsati) — W3-05.

## 4. Manbasi yo'q (rejada ham yo'q) — saqlansinmi, qaror kerak

- Kamera: namoz vaqti hisobi, ish jadvali/me'yoriy daqiqa va farq, tanaffuslar soni, eng uzun o'tirish, stol zonalari.
- AI chat, agentlar, Odoo ulanishlari, agent Telegram botlari (W3 qoidalariga zid).
