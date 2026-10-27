# SCHEMA v1.4 — Genesis bazasi (W0-04; v1.1: V008, reja v1.3; v1.2: V009, D-30; v1.3: V010, W0-08; v1.4: V011, W3-03)

Bu hujjatni `apps/w0-core/pg/gen_schema_doc.py` yozadi: 1–8 bo'limlar qo'lda, 9–12 bo'limlar haqiqiy
bazadan (`genesis`) va `roles.py` dan avtomatik. Sxema faqat W0 migratsiyalari bilan o'zgaradi; boshqa oyna
o'zgarish so'rasa `QUESTIONS.md` ga `[CONTRACT]` yozadi, W0 yangi migratsiya qo'shadi va bu versiya oshadi.

Sana: 2026-10-27 · Migratsiyalar: V001 base, V002 telegram, V003 camera, V004 partitions, V005 api_views, V006 queue, V007 gpu_gate, V008 plan_v13, V009 gpu_d30, V010 ops, V011 review_decide · CONTRACTS v1.0 ni amalga oshiradi (+ D-11…D-33).

## 1. Ulanish

| Narsa | Qiymat |
|---|---|
| Server | `127.0.0.1:5433` (faqat localhost, D-21), PostgreSQL 17.11, servis `genesis-postgresql` |
| Baza | `genesis` (egasi `genesis_migrator`) |
| Sxemalar | `core` — jadvallar va funksiyalar; `api` — W3 uchun view'lar (CONTRACTS §6) |
| Rol paroli | `genesis_common.read_secret_env("postgres.env")["<ROL>_PASSWORD"]` (masalan `W1_INGEST_PASSWORD`) |
| search_path | migrator/w0_admin: `core, api`; w1/w2/w4: `core`; w3: `api, core` |

Python misol (oyna venv'ida `psycopg` bilan):
```python
from genesis_common import load_config, read_secret_env
cfg = load_config("w1-tg-ingest"); pg = cfg["postgres"]; pw = read_secret_env("postgres.env")["W1_INGEST_PASSWORD"]
dsn = f"host={pg['host']} port={pg['port']} dbname={pg['database']} user=w1_ingest password={pw}"
```

## 2. Vaqt

- Hamma vaqt ustunlari `timestamptz` (ichkarida UTC). Sessiya vaqt zonasi `Asia/Tashkent`, ya'ni chiqishda +05.
- Hisobot kuni `local_day` = `core.tashkent_day(ts)` = `(ts AT TIME ZONE 'Asia/Tashkent')::date` (P-07, kun chegarasi 00:00).
  `messages`, `episodes`, `response_pairs`, `camera_events` da generated ustun; kamera kunlik jadvallarida
  oddiy ustun (manifest/tahlil yozadi).
- Oylik partition chegarasi ham Toshkent oyi: `2026-11` = `2026-10-31 19:00 UTC` dan `2026-11-30 19:00 UTC` gacha.

## 3. Rollar va huquqlar

`genesis_migrator` — hamma obyekt egasi, faqat `migrate.py` va `archive_month.py` ishlatadi (DDL).
`w0_admin` — W0 ish vaqti: barcha jadvallarda SELECT/INSERT/UPDATE/DELETE/TRUNCATE (`schema_migrations` faqat
o'qish), DDL yo'q; partition yaratish `core.ensure_month_partitions()` orqali (SECURITY DEFINER).
Ilova rollari "yozadi" = SELECT+INSERT+UPDATE; DELETE/TRUNCATE yo'q (o'chirish faqat W0: retention, arxiv).
To'liq matritsa 12-bo'limda. Har migratsiyadan keyin `migrate.py` uni aynan qayta qo'llaydi (avval REVOKE ALL).
Sinov: `apps/w0-core/pg/role_test.py` — 1014 tekshiruv (har rol × har jadval × 5 amal, view, funksiya, DDL).

CONTRACTS §5 ga W0-04 qo'shimchalari (sababi bilan):

| O'zgarish | Sabab |
|---|---|
| `genesis_migrator` roli | foydalanuvchi talabi: migratsiyalar alohida rol bilan; `w0_admin` DDL qila olmaydi |
| w1: `source_members`, `heartbeats` | W3 [CONTRACT] 3 (adminlar), jonlilik (W0-08) |
| w2: `episode_messages`, `daily_summaries`, `response_pairs` | W3 [CONTRACT] 6, 8; D-19 |
| w3: `report_deliveries`, `dashboard_users`, `jobs`, `heartbeats` | W3 [CONTRACT] 11, 13; CONTRACTS §2 da `build_excel_tg`/`build_report_cam` job'larini W3 bajaradi |
| w4: `video_gaps`, `heartbeats`; EXECUTE `import_manifest_record` | manifest `type=gap` (W4-02 [CONTRACT]) |
| hamma: EXECUTE `tashkent_day`, `tg_link` | view va generated ustunlar ularni so'rovchi nomidan chaqiradi |
| w1–w4: `job_types` SELECT; `jobs` da row-level security (V006) | rol faqat CONTRACTS §2 dagi o'z turlarini yaratadi/ko'radi/oladi; w3 va w4 job yaratmaydi, faqat bajaradi (§4a) |
| sequence huquqi berilmaydi | identity ustunlari sequence huquqisiz ishlaydi; id ni `RETURNING id` bilan oling (`currval()` rad etiladi) |

## 4. Partitsiyalar (D-13) va "partitsiyasi yo'q oy"

- `core.messages` (`sent_at`) va `core.transcripts` (`message_sent_at`) — RANGE, oyma-oy, nom `<jadval>_yYYYYmMM`.
- Joriy oy + keyingi `partitions.months_ahead` (=3) oy oldindan bor. W0-08 scheduler har kuni chaqiradi:
  `SELECT core.ensure_month_partitions();` (`w0_admin` bilan; idempotent, natija JSON).
- **Partitsiyasi yo'q oyga yozuv tushsa:** yozuv yo'qolmaydi va xato ham bermaydi — `<jadval>_default` partitionga
  tushadi. Signal: `api.v_system_health.default_partition_rows > 0` (W0-08 admin guruhiga ogohlantiradi; normal
  holatda doim 0). `ensure_month_partitions()` DEFAULT dagi har oy uchun partition yaratadi va qatorlarni unga
  ko'chiradi (bitta tranzaksiyada; `schema_test.py` da sinalgan: 2025-01 xabari DEFAULT -> `messages_y2025m01`).
  Bu W1-05 eski tarixni yuklaganda ham ishlaydi.
- `api.v_partition_health` — partitsiyalar, chegaralar, taxminiy qatorlar.

**Oylik arxiv** (`apps/w0-core/pg/archive_month.py`, `genesis_migrator`):
```
python -E archive_month.py archive --month 2025-10            # messages + transcripts
python -E archive_month.py restore --month 2025-10 --table messages
```
Tartib: faqat `archive.keep_months` (=12) dan eski oy → `DETACH PARTITION` → `pg_dump -Fc -Z 9` →
`G:\genesis\archive\<jadval>\YYYY-MM.dump` → tekshiruv (pg_restore o'qiydi, dump ichidagi qatorlar soni = jadvaldagi) →
sha256 bilan `core.archive_log` → `DROP TABLE`. Drop'dan oldingi har qanday xatoda partition qayta ulanadi
(sinalgan). `restore`: sha256 tekshiruvi → `pg_restore` → `ATTACH PARTITION`.

## 4a. Ish navbati (W0-05): `core.jobs` + `shared/genesis_queue`

Job turlari `core.job_types` da (CONTRACTS §2). Kim yaratadi/bajaradi bazada **row-level security** bilan majburlanadi:
rol faqat o'zi yaratadigan turni INSERT qiladi, faqat o'zi bajaradigan turni ko'radi va oladi; `target_role` berilsa
faqat o'sha rol oladi (masalan `daily_close` W2 va W3 uchun alohida job). `w0_admin` hamma narsa; view'lar egasi
(migrator) sifatida hammasini ko'radi.

| type | gpu_class | yaratadi | bajaradi | lease, s |
|---|---|---|---|---|
| `transcribe` | none | w1_ingest | w2_analyst | 300 |
| `analyze_image` | llm | w1_ingest | w2_analyst | 300 |
| `analyze_episode` | llm | w2_analyst | w2_analyst | 300 |
| `build_context` | llm | w2_analyst | w2_analyst | 300 |
| `daily_close` | none | w0_admin (scheduler) | w2_analyst, w3_dashboard (`target_role`) | 600 |
| `build_excel_tg` | none | w0_admin | w3_dashboard | 600 |
| `camera_day_batch` | vision | w0_admin | w4_camera | 900 |
| `build_report_cam` | none | w0_admin | w3_dashboard | 600 |

Holatlar: `queued` → `running` (lease) → `done`; xato bo'lsa `failed` (`run_after` = hozir + 30 s × 2^(urinish−1), eng ko'pi 1 soat)
va qayta olinadi; `max_attempts` (=5) dan keyin `dead`. Ishchi o'lsa (heartbeat yo'q) lease tugagach keyingi `claim` jobni
qaytaradi (`last_error = 'lease expired: worker … lost'`) yoki oxirgi urinish bo'lsa `dead` qiladi.

O'rnatish (oyna venv'ida): `pip install -e "D:\Demos\odoo telegram integration\shared"` (psycopg 3 ni ham o'rnatadi).
```python
from genesis_queue import connect, JobQueue, run_worker, Permanent
q = JobQueue(connect("w1_ingest"))
q.enqueue("transcribe", {"media_id": 42}, key="media:42")          # takror chaqiriq jim e'tiborsiz (created=False)
run_worker(lambda: connect("w2_analyst"), ["transcribe", "analyze_image"], handler, gpu_classes=["none", "llm"])
```
Qo'lda: `jobs = q.claim([...], limit=1)` → `q.heartbeat(id)` (uzoq ishda) → `q.complete(id, result)` yoki `q.fail(id, xato)`;
`raise Permanent(...)` handler ichida → darhol `dead`. Idempotency kaliti bazada `<type>:<key>` bo'lib saqlanadi.
CLI (`w0_admin`): `python -m genesis_queue status | dead | requeue --id N | --type T [--db genesis_test]`.
Testlar (faqat `genesis_test`): `apps\w0-core\.venv\Scripts\python.exe -E shared\genesis_queue\tests\run_queue_tests.py`.

## 4b. GPU gate (W0-06): `shared/genesis_gpu`, V007 + V009 (D-30)

- Faqat gate (W0, `w0_admin`) Ollama modelini yuklaydi/bo'shatadi (CONTRACTS §3). YOLO ni W4 ishchisi `vision` job ichida yuklaydi;
  gate uni `vision` ga ruxsat berish/bermaslik bilan boshqaradi. Har siklda nvidia-smi o'qiladi; boshqa GPU jarayonlari
  (masalan eski `camsvc.service`) `core.gpu_gate_log` ga yoziladi, ularga tegilmaydi.
- Sozlama `core.config` da, gate har siklda o'qiydi (qayta ishga tushirish kerak emas). V009 (D-30): `gpu.mode`
  (`timeslice` — standart | `coresident` — zaxira, D-14 | `dedicated` — ikkinchi GPU kelganda), `gpu.llm_window` 10:00–22:00
  (Telegram, Gemma), `gpu.vision_window` 22:00–10:00 (kamera, YOLO), `gpu.vision_idle_to_llm` true, `gpu.vision_idle_minutes` 5,
  `gpu.devices` `{"llm": 0, "vision": 0}`; V007 dan: `gpu.switch_guard_minutes` 15, `gpu.drain_timeout_minutes` 30,
  `gpu.vram_limit_mib` 13500 (W0-01: coresident eng ko'pi 10605 + zaxira), `gpu.llm_model`.
- `timeslice` (standart): oyna sinfi + `none`; almashishga 15 daqiqa qolganda faqat `none` (yangi og'ir job yo'q); almashishda
  ishlayotgan joblar tugashi kutiladi (eng ko'pi drain_timeout, o'lgan ishchining jobi lease tugagach qaytadi), keyin Gemma
  bo'shatiladi/yuklanadi va VRAM nvidia-smi bilan tekshiriladi. Kamera oynasida vision faqat Gemma bo'shagach, Telegram oynasida
  Gemma faqat vision joblar tugagach. **D-30:** kamera oynasida vision job (navbatda yoki ishlayotgan) yo'q bo'lsa va
  `gpu.vision_idle_minutes` o'tsa, GPU Gemma ga qaytariladi (`window = llm_idle`, Telegram davom etadi); vision job kelsa
  llm joblar to'xtatiladi, Gemma bo'shatiladi va kamera oladi (oyna tugashiga 15 daqiqadan kam qolganda kameraga o'tilmaydi).
  Telegram oynasi oxiridagi guard faqat kamera haqiqatan GPU ni oladigan bo'lsa ishlaydi. Jurnal: `camera_window_taken`,
  `camera_window_lent_to_llm`.
- `coresident` (zaxira): `none, llm, vision` doim; Gemma doim yuklangan (keep_alive −1).
- `dedicated` (D-30, ikkinchi GPU): hamma sinf; Gemma `gpu.devices.llm` qurilmasiga (Ollama `main_gpu`), YOLO
  `gpu.devices.vision` ga; ishchilar qurilmani `core.gpu_device_for(gpu_class)` dan oladi (`genesis_gpu.device_for`).
  llm va vision bir qurilmada yoki GPU yetmasa gate `timeslice` da ishlaydi va `dedicated_unavailable` yozadi. To'liq sinov
  ikkinchi GPU kelgach.
- VRAM `gpu.vram_limit_mib` dan oshsa (dedicated da har GPU uchun): faqat `none` (yangi GPU ish yo'q), jurnalga yoziladi.
- E'lon: `core.gpu_gate_state.allowed_classes`, amal muddati `gpu.publish_seconds` (90 s). Ishchilar `core.gpu_allowed_classes()`
  ni so'raydi — `genesis_queue` `claim()` buni o'zi qiladi (`gated=True` standart; `none` sinfi hech qachon to'xtatilmaydi).
  Gate o'lsa e'lon eskiradi → faqat `none`. W3: `api.v_gpu_gate`.
- CLI (`apps\w0-core\.venv\Scripts\python.exe -m genesis_gpu ...`, `--db`): `run [--interval 15]` (gate sikli; W0-07 dan servis `genesis_gpu_gate`, `shared/SERVICES.md`; xato siklida darhol faqat `none` e'lon qilinadi, `phase = error`),
  `tick`, `state`, `log`, `mode timeslice|coresident|dedicated --reason ...`, `devices llm=N,vision=M --reason ...`,
  `override --allow none,llm,vision | --mode M --minutes N --reason ...`,
  `cancel ID`. Override va rejim o'zgarishlari `core.gpu_overrides` / `core.gpu_gate_log` da jurnallanadi.
- Testlar (faqat `genesis_test`, tezlatilgan soat, haqiqiy Ollama/nvidia-smi): `shared\genesis_gpu\tests\run_gate_tests.py`.

## 4c. Signallar, scheduler, retention (W0-08): `shared/genesis_ops`, V010

- Servis `genesis_scheduler` (`python -m genesis_ops run`, 30 s sikl, `shared/SERVICES.md`). Har daqiqa signal tekshiruvi,
  kunlik vazifalar `core.scheduler_runs` da kuniga bir marta (o'tkazib yuborilgani keyingi ishga tushishda; xato bo'lsa
  15 daqiqadan keyin qayta), yurak urishi `core.heartbeats` `w0_scheduler`.
- **W3 sirti (W3-07 reporter):** `api.v_alerts` (ochiqlar + 7 kunlik yopilganlar: `alert_id, key, kind, severity, message,
  details, first_seen_at, last_seen_at, resolved_at, is_open, notified_at, notified_by`); yuborgach
  `UPDATE core.alerts SET notified_at = now(), notified_by = '...' WHERE id = ...` (W3 faqat shu ikki ustunni yangilaydi).
  `api.v_system_health` ga `open_alerts`, `critical_alerts`, `scheduler_beat_at` qo'shildi. Bir muammo = bitta ochiq qator
  (`key`); muammo yo'qolsa `resolved_at`. W0 Telegramga hech narsa yubormaydi.
- Signal turlari (`kind`) va chegaralar (`core.config` `alerts.*`): `ingest_silence` — W1 yurak urishi 10 daqiqa yangilanmasa
  (critical; tinglovchi bir marta ishlagan bo'lsagina); `no_messages` — 09:00–20:00 da 180 daqiqa xabar yo'q (warning);
  `queue_backlog` — gate ruxsat bergan, iste'molchisi bor turlarda > 500 job yoki eng eskisi > 60 daqiqa (warning;
  iste'molchisi hali yo'q oynalarning joblari signal bermaydi); `dead_jobs` (> 0); `gpu_gate` — `phase=error` yoki e'lon/yurak
  urishi > 5 daqiqa eski (critical); `disk_space` — E: ≥ 80% warning, ≥ 90% critical; `recorder_heartbeat` — W4 yozuvchisi
  fayli (`var\w4-camera\recorder_heartbeat.json`) yozuv oynasida > 5 daqiqa eski (critical; `core.heartbeats` `w4_recorder` ga
  ko'chiriladi); `scheduler` — scheduler vazifasi yoki sikli xato.
- Kunlik vazifalar (`schedule.*`, Asia/Tashkent): `partitions` 01:00 (`ensure_month_partitions`); `daily_close` 00:05 — kechagi
  kun uchun ikki job (`target_role` w2_analyst va w3_dashboard, kalit `daily_close:<kun>:<rol>`); `camera_batch` 22:10 —
  har yoqilgan kamera uchun `camera_day_batch` (kalit `<kamera>:<kun>`; D-30 kamera oynasi ichida, natijada tekshiriladi);
  `retention` 03:30. `schedule.start_day` (2026-10-27) dan oldingi kunlar uchun job qo'yilmaydi.
- Retention (D-27, D-13, D-29): `retention.delete_enabled = false` — **faqat dry-run** (hisobot
  `var\run\retention\retention_<kun>_dry-run.json`); foydalanuvchi ro'yxatni tasdiqlagach true. Video:
  `retention.video_root` (`E:\data\video`) ichidagi `<kamera>\<YYYY-MM-DD>` papkalari, `retention.video_days` (14) dan eski.
  Hech qachon: `retention.protect_paths` (`E:\Kamera raw data`, `E:\data\video_keep`, `F:\genesis\keep`) va ichidagilar,
  `KEEP`/`.keep` fayli bor kun papkasi, `video_segments.is_keep` kunlari, havolalar, sana nomli bo'lmagan papkalar; himoyalangan
  papka ichidagi video_root butunlay rad etiladi. O'chirilganda `video_segments.deleted_at`. Matn: `purge_unrelated_text`.
- CLI (`-m genesis_ops`, `--db`): `run`, `tick`, `check`, `retention` (doim dry-run), `alerts`, `runs`.
  Testlar: `shared\genesis_ops\tests\run_ops_tests.py` (faqat `genesis_test`).

## 4d. Odam tekshiruvi javobi (W3-03 [CONTRACT]): `core.review_decide`, V011

```sql
core.review_decide(p_review_id bigint, p_decision text, p_reviewed_by text, p_details jsonb DEFAULT '{}') RETURNS jsonb
-- SECURITY DEFINER; EXECUTE: w3_dashboard, w0_admin
-- natija: {"review_id", "decision", "reviewed_by", "reviewed_at", "audit_id"}
```
- Qarorlar: `confirm` | `reject` | `correct`. `correct` da tuzatilgan qiymatlar `p_details` da (masalan `categories`, `sentiment`,
  `relevance`; faqat `note` yetmaydi) va `review_queue.details.human_correction` ga ham yoziladi (W2 maydonlari saqlanadi).
  Har qarorda ixtiyoriy `p_details.note`.
- `p_reviewed_by`: kim qaror qildi, 1..200 belgi. **Vaqtinchalik qiymat `dashboard-local`** (W3-05 kirish/rollar tayyor bo'lguncha).
- Bitta tranzaksiyada: qator `FOR UPDATE` bilan qulflanadi, faqat `status = 'open'` qator yangilanadi (`human_decision,
  reviewed_by, reviewed_at = now()`), `core.audit_log` ga yoziladi: `actor = p_reviewed_by`, `action = 'review_decide'`,
  `object_type = 'review_queue'`, `object_id`, `details` = `decision, item_type, item_id, reason, details (p_details),
  db_user (haqiqiy login), previous (oldingi human_decision/reviewed_by/reviewed_at)`, `client_addr`.
- Xatolar (hech narsa yozilmaydi): noto'g'ri qaror, bo'sh/uzun `reviewed_by`, `p_details` obyekt emas yoki `correct` qiymatsiz →
  `22023`; qator yo'q → `P0002`; qator allaqachon yopilgan → `55000`. Ikki dashboard bir qatorga bir vaqtda javob bersa,
  ikkinchisi birinchisi tugashini kutadi va `55000` oladi.
- Huquq: V011 dan keyin W3 da `core.review_queue` ga UPDATE va `core.audit_log` ga INSERT **yo'q** (yagona yo'l shu funksiya),
  `core.review_queue` SELECT qoldi; o'qish `api.v_review_queue` orqali. W3 dashboardining o'qish sessiyasi read-only, shuning uchun
  funksiyani alohida (yozish ruxsatli) tranzaksiyada chaqirish kerak. V011 down qilinsa eski to'g'ridan-to'g'ri huquqlar qaytadi.
- Sinov: `apps/w0-core/pg/review_test.py` (vaqtinchalik baza, haqiqiy loginlar, 28 tekshiruv), `role_test.py`.

## 5. [CONTRACT] so'rovlari va qarorlar

| # | So'rov | Qaror | Qayerda / sabab |
|---|---|---|---|
| W3-1 | So'rov→javob obyekti, `v_response_times` ustunlari, kim hisoblaydi | qabul (D-19) | `core.response_pairs` (W2-09 qoida bilan to'ldiradi), `api.v_response_times`: `status` (pending/answered/missed), `response_s`, `sla_s`, `within_sla`, `late_answer_at`, `responder_*`, havolalar |
| W3-2 | `v_daily_category_counts.source_id`, `filtered` | qabul, o'zgartirib | `source_id` qo'shildi; `filtered` kategoriyasiz bo'lgani uchun alohida `api.v_daily_source_counts.filtered_episodes` (+ related/unsure/pending, xabar soni) |
| W3-3 | `sources` da menejer/admin/is_present | qabul | `sources.is_present`, `admins_checked_at`; `core.source_members` (manager/admin); `api.v_sources.managers/admins` |
| W3-4 | havola uchun tg chat id / username | qabul | `sources.tg_chat_id`, `tg_username`; view'larda tayyor `tg_link` (`core.tg_link`) |
| W3-5 | `analyses.needs_reply` | qabul | `analyses.needs_reply`, feed va kunlik sanoqda |
| W3-6 | episode↔xabar va qatnashchilar | qabul | `core.episode_messages`; `api.v_episode_messages`, `api.v_episode_participants` |
| W3-7 | `v_messages_public` da transkript va media yo'li | qabul | `transcript` (asosiy, `is_primary`), `media_path` (paths.media ga nisbatan), `media_kind/status/duration` |
| W3-8 | `v_daily_summary` | qabul | `core.daily_summaries` (W2-09), `api.v_daily_summary` |
| W3-9 | `v_review_queue` + W3 javob yo'li | qabul | `api.v_review_queue`; javob faqat `core.review_decide(...)` orqali (V011, §4d; V010 gacha ustunlarni to'g'ridan-to'g'ri UPDATE edi), `status` generated |
| W3-10 | `v_pipeline_status` / `v_system_health` | qabul | ikkalasi + `api.v_heartbeats`, `api.v_partition_health`; jobs jadvaliga to'g'ridan-to'g'ri SELECT ham bor (w3 jobs yozadi) |
| W3-11 | Hisobot yetkazish jurnali | qabul | `core.report_deliveries` (W3 yozadi; §5 kengaytmasi) |
| W3-12 | `v_presence_sessions`, `v_camera_coverage` | qabul | ikkalasi + `api.v_camera_events` (`inferred` belgilari bilan) |
| W3-13 | `dashboard_users` (rollar) | qabul | `core.dashboard_users` (faqat parol hash; hashlashni W3 qiladi) |
| W4-02a | `paths.camera_video` = `E:\data\video` | qabul | `config.toml` o'zgardi; bo'sh `E:\data\camera` o'chirildi |
| W4-02b | `video_segments` importi manifest v1 dan | qabul | `core.import_manifest_record(jsonb, video_root)` — segment/gap, idempotent (haqiqiy manifest ikki marta import qilindi). NSSM servisi W0-07 da |
| W3-03 | Odam tuzatishi funksiyasi, audit bilan | qabul | V011 `core.review_decide(bigint, text, text, jsonb)` (§4d): `confirm` / `reject` / `correct`, `audit_log` shu tranzaksiyada; W3 ning to'g'ridan-to'g'ri `review_queue` UPDATE va `audit_log` INSERT huquqlari olindi; `reviewed_by` vaqtincha `dashboard-local` |

Rad etilgan so'rov yo'q. MAPPING.md §4 (namoz, ish jadvali, stol, AI chat/agentlar) — D-16, D-17 bo'yicha
eski tizimda qoladi, sxemaga kirmaydi.

## 6. Saqlash muddati (P-05, D-27 tasdiqlangan)

- `episodes.relevance` (`yes|no|unsure`, NULL = hali yo'q) va `episodes.retain_until`.
- Trigger: `relevance='no'` → `retain_until = ended_at + config retention.unrelated_text_hours` (=72); boshqa
  holatda NULL (muddatsiz saqlanadi). Muddat `core.config` da, o'zgartirsa yangi/yangilangan episodlarga ta'sir qiladi.
- `core.purge_unrelated_text(now())` (W0-08, `w0_admin`): muddati o'tgan episodlarning xabar matni/raw,
  transkript matni, tahlil xulosasi va dalil iqtiboslarini NULL qiladi; metadata (vaqt, kim, kategoriya) qoladi;
  `text_purged_at` belgilanadi. D-27: tasdiqlangan (72 soat, sozlamada). D-33: aloqasiz xabar statistikaga kirmaydi.

## 7. Kamera (D-11, D-17, D-20, D-24, D-29, D-31, D-32)

- `cameras`: oqim (`stream_channel` 102, hevc 640x360 20 fps — kamera sozlamasi o'zgarmaydi), yozuv oynasi
  `record_start/record_end` (07:00–22:00), `analysis_fps` (sozlamada), eshik zonasi `door_zone`/`zone_file`
  (`provisional`). RTSP manzil bazada yo'q: `rtsp_secret` = `secrets/` dagi fayl nomi.
- `video_segments` ← manifest `type=segment`: `start_utc/end_utc` → `started_at/ended_at`, `closed` → `closed_reason`,
  `break_before`/`gap_before_s`/`end_source='mtime'` — uzilish belgilari; `codec`, `width`, `height`,
  `measured_fps` manifest v1 da yo'q (W4 probe to'ldiradi, NULL bo'lishi mumkin). `video_gaps` ← `type=gap`.
- `camera_events.inferred`, `method` (D-24: zonada paydo bo'lish/yo'qolish, yo'nalish), `analysis_run` (qayta hisoblash idempotent).
- `daily_presence`: `office_minutes` (ishxonada: birinchi kirish → oxirgi chiqish) va `room_minutes` (xonada) alohida,
  `away_minutes` = farq. D-32: ishda = birinchi kirish → oxirgi chiqish; xonada = shundan xonadan chiqib turgan
  har qanday oraliq ayrilgan; xona ichidagi harakat (namoz va h.k.) xonada hisoblanadi.
- `anon_persons.embedding` (bytea) / `embedding_f4` (real[]); pgvector yo'q (P-06), serverdan chiqmaydi.
- V008 (D-31): `anon_persons.staff_no` — 4 ta doimiy asosiy hodim "Hodim 1..4" (migratsiya yaratadi, ism yo'q).
  Mehmonlar uchun qator yozilmaydi va ular hisobga olinmaydi; `staff_no` faqat `scope='permanent'` da.
- V008 (D-31): `camera_event_frames` — har kirdi/chiqdi hodisasiga vaqt belgili kadr (W4-05 yozadi); fayl D-29 bo'yicha
  `E:\data\camera_events\YYYY\MM\DD\<kamera>\`. W3 faqat `api.v_staff_day` orqali ko'radi (W3-05: ruxsatli rollar).
- V008: `api.v_staff_day` (W3-04) — kun × asosiy hodim: `first_in_at` (keldi), `last_out_at` (ketdi),
  `office_minutes` (ishda), `room_minutes` (xonada), `exits` (chiqish oraliqlari: from/to/minutes, ketma-ket
  sessiyalar orasidagi bo'shliq), `first_in_frame`, `last_out_frame`, `frames` (hamma kadrlar). Bir `analysis_run` ichida.
- D-29 indekslari: `messages (source_id, sent_at)`, `camera_events (camera_id, at)`, `video_segments (camera_id, started_at)`
  — V002/V003 da bor; `messages`/`transcripts` oylik partition.

## 7a. Telegram bo'limlari (V008, D-33)

- `core.categories` — 7 bo'lim (nomlar aynan foydalanuvchiniki), "Boshqa" yo'q: Dastavka va Logistika, Mahsulot Sifati
  va Nuqsonlar, Komplektatsiya va Hajm, Xodimlar va Xizmat Ko'rsatish, Narx va To'lovlar, Qaytarish va Kafolat,
  Ijobiy Fikrlar. W2 kodlarni (`delivery`, ...) `analyses.categories` ga yozadi; W3 `api.v_categories` ni o'qiydi.
- Faqat aniq kompaniya haqidagi gap hisobga olinadi; ishonch past bo'lsa `review_queue`. Eslatma: `genesis_test`
  sun'iy tahlillari hali eski reja tasnifida (shikoyat/maqtov/...), W2/W3 qadamlarida D-33 ga o'tkaziladi.

## 8. Migratsiyalar

```
cd "D:\Demos\odoo telegram integration\apps\w0-core\pg"
python -E migrate.py status
python -E migrate.py up            # hammasi (yoki --to N)
python -E migrate.py down --steps 1   # oxirgisini qaytarish (yoki --to N)
python -E gen_schema_doc.py        # shu hujjatni yangilash
```
Qoidalar: fayl `apps/w0-core/migrations/V<NNN>__<nom>.up.sql` + `.down.sql`; har biri bitta tranzaksiyada,
advisory lock bilan; qo'llangan faylni o'zgartirib bo'lmaydi (checksum); yangi o'zgarish = yangi versiya.
Sinov: bo'sh bazada up → down (0 obyekt) → up, `pg_dump --schema-only` farqi yo'q; `schema_test.py`, `role_test.py`.

## 8a. Sinov bazasi `genesis_test` va sun'iy ma'lumot (W3 uchun, W0-09 ning oldindan olingan qismi)

- Baza `genesis_test` (o'sha server, 5433), migratsiyalar va rollar `genesis` bilan bir xil. Haqiqiy xabar, odam yoki kadr yo'q.
- Bitta buyruq (ildizdan): `python -E shared/fixtures/load_fixtures.py [--db genesis_test] [--anchor YYYY-MM-DD]`.
  Baza bo'lmasa yaratadi, migratsiyalarni qo'llaydi, ma'lumot jadvallarini `TRUNCATE ... RESTART IDENTITY` qilib qayta yuklaydi
  (bitta tranzaksiya). Bir xil `anchor` da natija aynan bir xil (id lar ham; barmoq izi chiqariladi). `genesis` ga yuklashni rad etadi.
- Kunlar: `anchor-1` va `anchor` (standart: bugun, Toshkent), shuning uchun `v_attendance_today` ham to'la.
- Tarkib: 3 guruh (`TEST · ...`), 3 hodim + 8 mijoz, ~90 xabar (2 ta voice + transkript), 40 episode (1 tasi `no`, 1 tasi `unsure`,
  1 tasi hali aniqlanmagan), 36 tahlil: `categories` = reja tasnifi (shikoyat/maqtov/savol/buyurtma/boshqa), `entities.topic` =
  prototipning 8 bo'limidan biri (yakuniy ro'yxat W2-01 da); 28 `response_pairs` (answered/missed + kechikkan javob/pending),
  5 `review_queue` (1 tasi yopilgan), ochiq masalalar (`chat_context.open_issues`, `needs_reply`), `daily_stats`, `daily_summaries`;
  kamera: 4 "Hodim", 2 kun, `camera_events` (bitta `inferred`), `presence_sessions`, `daily_presence` (ishxonada va xonada alohida,
  bitta `uncertain` — oxirgi chiqish yo'q), 07:00–22:00 video bo'laklari va bitta uzilish; jobs, heartbeats, report_deliveries.
- W3 `genesis_test` ga `w3_dashboard` roli bilan ulanadi (parol o'sha `postgres.env` dan) va faqat `api` view'larini o'qiydi:
  19/19 view to'la, `core` dagi ma'lumot jadvallariga to'g'ridan-to'g'ri SELECT rad etiladi (16/16 tekshirildi).

## 9. Jadvallar (`core`)

### `core.alerts`

W0-08 signallari (scheduler har daqiqa yozadi/yopadi). Telegramga W3-07 reporter yuboradi va notified_at ni belgilaydi.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `key` | text | yo'q |  | Bir xil muammo uchun bitta ochiq qator (masalan disk:E:, ingest_silence:w1_ingest/main) |
| `kind` | text | yo'q |  |  |
| `severity` | text | yo'q |  |  |
| `message` | text | yo'q |  |  |
| `details` | jsonb | yo'q | '{}'::jsonb |  |
| `first_seen_at` | timestamp with time zone | yo'q | now() |  |
| `last_seen_at` | timestamp with time zone | yo'q | now() |  |
| `resolved_at` | timestamp with time zone | ha |  |  |
| `notified_at` | timestamp with time zone | ha |  | W3-07: reporter yuborgan vaqt (W3 faqat shu ikki ustunni yangilaydi) |
| `notified_by` | text | ha |  |  |

Cheklovlar: `alerts_kind_check: CHECK ((kind = ANY (ARRAY['ingest_silence'::text, 'no_messages'::text, 'queue_backlog'::text, 'dead_jobs'::text, 'gpu_gate'::text, 'disk_space'::text, 'recorder_heartbeat'::text, 'scheduler'::text])))`; `alerts_severity_check: CHECK ((severity = ANY (ARRAY['warning'::text, 'critical'::text])))`; `alerts_pkey: PRIMARY KEY (id)`

Indekslar: `alerts_one_open`, `alerts_recent`

### `core.analyses`

Tasnif natijasi (W2). Ko'rsatishda "AI taxmini" + dalil + ishonchlilik (P-08).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `episode_id` | bigint | yo'q |  |  |
| `categories` | text[] | yo'q | '{}'::text[] |  |
| `primary_category` | text | ha |  |  |
| `sentiment` | text | ha |  |  |
| `severity` | smallint | ha |  |  |
| `summary` | text | ha |  |  |
| `evidence` | jsonb | yo'q | '[]'::jsonb | [{"tg_message_id": 123, "quote": "..."}] |
| `entities` | jsonb | yo'q | '{}'::jsonb | {"products": [], "orders": [], "managers": []} |
| `needs_reply` | boolean | ha |  | Javob kerakmi (W3 [CONTRACT] 5) |
| `confidence` | real | ha |  |  |
| `model_id` | text | yo'q |  |  |
| `prompt_version` | text | yo'q |  |  |
| `is_current` | boolean | yo'q | true | Qayta tahlil bo'lsa eskisi false (W2 boshqaradi); view'lar faqat true ni oladi |
| `created_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `analyses_sentiment_check: CHECK ((sentiment = ANY (ARRAY['negative'::text, 'neutral'::text, 'positive'::text, 'mixed'::text])))`; `analyses_severity_check: CHECK (((severity >= 0) AND (severity <= 3)))`; `analyses_episode_id_fkey: FOREIGN KEY (episode_id) REFERENCES core.episodes(id) ON DELETE CASCADE`; `analyses_pkey: PRIMARY KEY (id)`; `analyses_episode_id_model_id_prompt_version_key: UNIQUE (episode_id, model_id, prompt_version)`

Indekslar: `analyses_categories`, `analyses_created_at`, `analyses_one_current`

### `core.anon_persons`

"Hodim N" (D-06): ismsiz. Embedding serverdan chiqmaydi (qoida 6), pgvector yo'q (P-06).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `label` | text | yo'q |  |  |
| `scope` | text | yo'q | 'permanent'::text |  |
| `valid_day` | date | ha |  |  |
| `embedding` | bytea | ha |  | Ko'rinish embeddingi, xom bayt (float32 LE) |
| `embedding_f4` | real[] | ha |  | Xuddi shu embedding real[] ko'rinishida (ixtiyoriy) |
| `embedding_model` | text | ha |  |  |
| `first_seen_at` | timestamp with time zone | ha |  |  |
| `last_seen_at` | timestamp with time zone | ha |  |  |
| `note` | text | ha |  |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |
| `staff_no` | smallint | ha |  | D-31: asosiy hodim raqami ("Hodim N", doimiy, ismsiz). NULL = asosiy hodim emas; mehmonlar hisobga olinmaydi |

Cheklovlar: `anon_persons_check: CHECK (((scope = 'daily'::text) = (valid_day IS NOT NULL)))`; `anon_persons_scope_check: CHECK ((scope = ANY (ARRAY['daily'::text, 'permanent'::text])))`; `anon_persons_staff_no_check: CHECK (((staff_no >= 1) AND (staff_no <= 999)))`; `anon_persons_staff_permanent: CHECK (((staff_no IS NULL) OR (scope = 'permanent'::text)))`; `anon_persons_pkey: PRIMARY KEY (id)`; `anon_persons_label_key: UNIQUE (label)`; `anon_persons_staff_no_key: UNIQUE (staff_no)`

### `core.archive_log`

Oylik arxiv jurnali (D-13): partition -> siqilgan fayl (G:) -> o'chirish.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `parent` | text | yo'q |  |  |
| `partition` | text | yo'q |  |  |
| `month` | date | yo'q |  |  |
| `row_count` | bigint | yo'q |  |  |
| `file_path` | text | yo'q |  |  |
| `file_bytes` | bigint | yo'q |  |  |
| `sha256` | text | yo'q |  |  |
| `verified` | boolean | yo'q | false |  |
| `dropped_at` | timestamp with time zone | ha |  |  |
| `archived_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `archive_log_pkey: PRIMARY KEY (id)`; `archive_log_parent_month_key: UNIQUE (parent, month)`

### `core.audit_log`

Kim nimani ko'rdi/o'zgartirdi. W3 faqat qo'shadi (INSERT).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `at` | timestamp with time zone | yo'q | now() |  |
| `actor` | text | yo'q |  |  |
| `action` | text | yo'q |  |  |
| `object_type` | text | ha |  |  |
| `object_id` | text | ha |  |  |
| `details` | jsonb | yo'q | '{}'::jsonb |  |
| `client_addr` | inet | ha |  |  |

Cheklovlar: `audit_log_pkey: PRIMARY KEY (id)`

Indekslar: `audit_log_at`

### `core.camera_event_frames`

D-31: kirgan/chiqqan paytdagi kadr (W4-05 yozadi). Dashboardda faqat ruxsatli rollar ko'radi (W3-05). Rasm serverdan chiqmaydi.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `event_id` | bigint | yo'q |  |  |
| `kind` | text | yo'q |  | kirdi = in, chiqdi = out (camera_events.direction bilan bir xil) |
| `frame_at` | timestamp with time zone | yo'q |  | Kadrning o'z vaqti (UTC); hodisa vaqtidan bir necha soniya farq qilishi mumkin |
| `local_day` | date | ha | generated: core.tashkent_day(frame_at) |  |
| `file_path` | text | yo'q |  | D-29: E:\data\camera_events\YYYY\MM\DD\<kamera>\... (to'liq yo'l) |
| `width` | integer | ha |  |  |
| `height` | integer | ha |  |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `camera_event_frames_kind_check: CHECK ((kind = ANY (ARRAY['in'::text, 'out'::text])))`; `camera_event_frames_event_id_fkey: FOREIGN KEY (event_id) REFERENCES core.camera_events(id) ON DELETE CASCADE`; `camera_event_frames_pkey: PRIMARY KEY (id)`; `camera_event_frames_event_id_frame_at_key: UNIQUE (event_id, frame_at)`

Indekslar: `camera_event_frames_day`

### `core.camera_events`

Kirdi/chiqdi (W4). D-24: ostona ko'rinmaydi, hodisa eshik zonasida trek paydo bo'lishi/yo'qolishi va yo'nalishidan.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `camera_id` | text | yo'q |  |  |
| `at` | timestamp with time zone | yo'q |  |  |
| `local_day` | date | ha | generated: core.tashkent_day(at) |  |
| `direction` | text | yo'q |  |  |
| `method` | text | yo'q |  |  |
| `track_id` | text | ha |  |  |
| `anon_person_id` | bigint | ha |  |  |
| `confidence` | real | ha |  |  |
| `inferred` | boolean | yo'q | false | Tiklangan (ko'rilmagan, mantiqan chiqarilgan) hodisa |
| `video_segment_id` | bigint | ha |  |  |
| `segment_offset_s` | numeric(10,3) | ha |  |  |
| `analysis_run` | text | yo'q |  | Tahlil yurishi id (qayta hisoblashda idempotent) |
| `created_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `camera_events_direction_check: CHECK ((direction = ANY (ARRAY['in'::text, 'out'::text])))`; `camera_events_method_check: CHECK ((method = ANY (ARRAY['zone_appear'::text, 'zone_disappear'::text, 'direction'::text, 'manual'::text, 'recovered'::text])))`; `camera_events_anon_person_id_fkey: FOREIGN KEY (anon_person_id) REFERENCES core.anon_persons(id)`; `camera_events_camera_id_fkey: FOREIGN KEY (camera_id) REFERENCES core.cameras(id)`; `camera_events_video_segment_id_fkey: FOREIGN KEY (video_segment_id) REFERENCES core.video_segments(id)`; `camera_events_pkey: PRIMARY KEY (id)`; `camera_events_analysis_run_camera_id_track_id_at_direction_key: UNIQUE NULLS NOT DISTINCT (analysis_run, camera_id, track_id, at, direction)`

Indekslar: `camera_events_time`

### `core.cameras`

Kamera reestri (W0 yozadi, W4 o'qiydi). Sozlama kamerada o'zgartirilmaydi (D-20).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | text | yo'q |  |  |
| `name` | text | yo'q |  |  |
| `location` | text | ha |  |  |
| `enabled` | boolean | yo'q | true |  |
| `rtsp_secret` | text | yo'q |  | secrets/ ichidagi fayl nomi. RTSP manzil/parol bazaga yozilmaydi |
| `stream_channel` | integer | yo'q |  | Yoziladigan oqim (D-20: 102, o'z holida) |
| `codec` | text | ha |  |  |
| `width` | integer | ha |  |  |
| `height` | integer | ha |  |  |
| `stream_fps` | numeric(6,2) | ha |  |  |
| `record_start` | time without time zone | yo'q | '07:00:00'::time without time zone | Yozuv oynasi, mahalliy vaqt (D-24: 07:00-22:00) |
| `record_end` | time without time zone | yo'q | '22:00:00'::time without time zone |  |
| `timezone` | text | yo'q | 'Asia/Tashkent'::text |  |
| `analysis_fps` | numeric(4,2) | yo'q | 1 | Tahlil fps: yozuvdan siyraklashtiriladi (D-20: 1/2/3/6, W4-04 tanlaydi) |
| `door_zone` | jsonb | ha |  | Eshik zonasi (zone_config.json mazmuni, D-24). NULL = zone_file dan |
| `zone_file` | text | ha |  |  |
| `zone_status` | text | yo'q | 'provisional'::text |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |
| `updated_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `cameras_analysis_fps_check: CHECK ((analysis_fps > (0)::numeric))`; `cameras_zone_status_check: CHECK ((zone_status = ANY (ARRAY['provisional'::text, 'confirmed'::text])))`; `cameras_pkey: PRIMARY KEY (id)`

### `core.categories`

D-33: Telegram tasnifining 7 bo'limi ("Boshqa" yo'q). Aloqasiz xabar hech bir bo'limga kirmaydi; ishonchsiz -> review_queue.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `code` | text | yo'q |  | analyses.categories / primary_category / daily_stats.category shu kodlarni yozadi (W2) |
| `name` | text | yo'q |  | Foydalanuvchi bergan nom, aynan shunday ko'rsatiladi |
| `sort` | smallint | yo'q |  |  |
| `active` | boolean | yo'q | true |  |
| `description` | text | ha |  |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `categories_code_check: CHECK ((code ~ '^[a-z_]+$'::text))`; `categories_pkey: PRIMARY KEY (code)`; `categories_name_key: UNIQUE (name)`; `categories_sort_key: UNIQUE (sort)`

### `core.chat_context`

24 soatlik yig'ma xulosa va ochiq masalalar (W2-07).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `source_id` | bigint | yo'q |  |  |
| `summary` | text | ha |  |  |
| `open_issues` | jsonb | yo'q | '[]'::jsonb |  |
| `window_start` | timestamp with time zone | ha |  |  |
| `window_end` | timestamp with time zone | ha |  |  |
| `model_id` | text | ha |  |  |
| `prompt_version` | text | ha |  |  |
| `updated_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `chat_context_source_id_fkey: FOREIGN KEY (source_id) REFERENCES core.sources(id)`; `chat_context_pkey: PRIMARY KEY (source_id)`

### `core.config`

Bazadagi ish sozlamalari (W0 yozadi). Fayl sozlamalari config.toml da.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `key` | text | yo'q |  |  |
| `value` | jsonb | yo'q |  |  |
| `description` | text | ha |  |  |
| `updated_at` | timestamp with time zone | yo'q | now() |  |
| `updated_by` | text | yo'q | CURRENT_USER |  |

Cheklovlar: `config_pkey: PRIMARY KEY (key)`

### `core.daily_presence`

Kunlik jami (D-11): ishxonada (birinchi kirish -> oxirgi chiqish) va xonada alohida.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `local_day` | date | yo'q |  |  |
| `anon_person_id` | bigint | yo'q |  |  |
| `camera_id` | text | yo'q |  |  |
| `first_in_at` | timestamp with time zone | ha |  |  |
| `last_out_at` | timestamp with time zone | ha |  |  |
| `office_minutes` | numeric | ha |  | Ishxonada: first_in_at -> last_out_at |
| `room_minutes` | numeric | ha |  | Xonada: presence_sessions yig'indisi |
| `away_minutes` | numeric | ha |  | Xonadan tashqarida (hojatxona, ko'cha): office - room |
| `session_count` | integer | yo'q | 0 |  |
| `uncertain` | boolean | yo'q | false |  |
| `uncertainty_note` | text | ha |  |  |
| `analysis_run` | text | yo'q |  |  |
| `computed_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `daily_presence_anon_person_id_fkey: FOREIGN KEY (anon_person_id) REFERENCES core.anon_persons(id)`; `daily_presence_camera_id_fkey: FOREIGN KEY (camera_id) REFERENCES core.cameras(id)`; `daily_presence_pkey: PRIMARY KEY (local_day, anon_person_id, camera_id)`

### `core.daily_stats`

Kun yopilgandagi qotirilgan agregat (W2-09). Jonli raqamlar api view'larida.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `local_day` | date | yo'q |  |  |
| `source_id` | bigint | yo'q |  |  |
| `category` | text | yo'q |  |  |
| `episode_count` | integer | yo'q | 0 |  |
| `negative_count` | integer | yo'q | 0 |  |
| `needs_reply_count` | integer | yo'q | 0 |  |
| `computed_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `daily_stats_source_id_fkey: FOREIGN KEY (source_id) REFERENCES core.sources(id)`; `daily_stats_pkey: PRIMARY KEY (local_day, source_id, category)`

### `core.daily_summaries`

Kunlik AI xulosa (W2-09; W3 [CONTRACT] 8).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `local_day` | date | yo'q |  |  |
| `scope` | text | yo'q |  |  |
| `scope_key` | text | yo'q | ''::text |  |
| `summary` | text | yo'q |  |  |
| `model_id` | text | ha |  |  |
| `prompt_version` | text | ha |  |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `daily_summaries_scope_check: CHECK ((scope = ANY (ARRAY['all'::text, 'category'::text, 'source'::text])))`; `daily_summaries_pkey: PRIMARY KEY (local_day, scope, scope_key)`

### `core.dashboard_users`

Dashboard kirishi va rollar (W3-05; W3 [CONTRACT] 13). Faqat hash saqlanadi (argon2/bcrypt, W3).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `login` | text | yo'q |  |  |
| `password_hash` | text | yo'q |  |  |
| `role` | text | yo'q | 'viewer'::text |  |
| `camera_access` | boolean | yo'q | false |  |
| `is_active` | boolean | yo'q | true |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |
| `last_login_at` | timestamp with time zone | ha |  |  |

Cheklovlar: `dashboard_users_role_check: CHECK ((role = ANY (ARRAY['admin'::text, 'manager'::text, 'viewer'::text])))`; `dashboard_users_pkey: PRIMARY KEY (id)`; `dashboard_users_login_key: UNIQUE (login)`

### `core.episode_messages`

Episode <-> xabar (W3 [CONTRACT] 6). messages ga FK yo'q (partition), kalit bilan bog'lanadi.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `episode_id` | bigint | yo'q |  |  |
| `source_id` | bigint | yo'q |  |  |
| `tg_message_id` | bigint | yo'q |  |  |
| `sent_at` | timestamp with time zone | yo'q |  |  |

Cheklovlar: `episode_messages_episode_id_fkey: FOREIGN KEY (episode_id) REFERENCES core.episodes(id) ON DELETE CASCADE`; `episode_messages_pkey: PRIMARY KEY (episode_id, source_id, tg_message_id)`

Indekslar: `episode_messages_message`

### `core.episodes`

Suhbat bo'lagi (W2). relevance NULL = hali aniqlanmagan.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `source_id` | bigint | yo'q |  |  |
| `started_at` | timestamp with time zone | yo'q |  |  |
| `ended_at` | timestamp with time zone | yo'q |  |  |
| `local_day` | date | ha | generated: core.tashkent_day(started_at) |  |
| `first_tg_message_id` | bigint | yo'q |  |  |
| `last_tg_message_id` | bigint | yo'q |  |  |
| `message_count` | integer | yo'q | 0 |  |
| `participant_tg_ids` | bigint[] | yo'q | '{}'::bigint[] |  |
| `relevance` | text | ha |  | yes \| no \| unsure (A bosqich, W2-05) |
| `relevance_confidence` | real | ha |  |  |
| `retain_until` | timestamp with time zone | ha |  | relevance=no bo'lsa: ended_at + config retention.unrelated_text_hours (trigger). Boshqa holatda NULL (saqlanadi) |
| `text_purged_at` | timestamp with time zone | ha |  |  |
| `segmenter_version` | text | yo'q | 'v1'::text |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |
| `updated_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `episodes_check: CHECK ((ended_at >= started_at))`; `episodes_relevance_check: CHECK ((relevance = ANY (ARRAY['yes'::text, 'no'::text, 'unsure'::text])))`; `episodes_source_id_fkey: FOREIGN KEY (source_id) REFERENCES core.sources(id)`; `episodes_pkey: PRIMARY KEY (id)`; `episodes_source_id_first_tg_message_id_segmenter_version_key: UNIQUE (source_id, first_tg_message_id, segmenter_version)`

Indekslar: `episodes_local_day`, `episodes_purge_due`, `episodes_source_time`

### `core.gpu_gate_log`

Gate jurnali: rejim/oyna almashishi, model yuklash/bo'shatish (VRAM oldin/keyin), override, begona GPU jarayonlari.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `at` | timestamp with time zone | yo'q | now() |  |
| `sim_at` | timestamp with time zone | ha |  | Tezlatilgan soat bilan sinovda simulyatsiya vaqti (oddiy ishda NULL) |
| `event` | text | yo'q |  |  |
| `details` | jsonb | yo'q | '{}'::jsonb |  |

Cheklovlar: `gpu_gate_log_pkey: PRIMARY KEY (id)`

Indekslar: `gpu_gate_log_at`

### `core.gpu_gate_state`

Gate e'loni (bitta qator): hozir qaysi gpu_class olinadi, qaysi model yuklangan, VRAM.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | smallint | yo'q | 1 |  |
| `mode` | text | yo'q |  |  |
| `phase` | text | yo'q |  | steady \| guard (almashish oldidan) \| draining (ishlayotganlar kutilmoqda) \| loading \| over_vram_limit \| override |
| `allowed_classes` | text[] | yo'q |  |  |
| `valid_until` | timestamp with time zone | yo'q |  |  |
| `loaded_models` | jsonb | yo'q | '[]'::jsonb |  |
| `vram_used_mib` | integer | ha |  |  |
| `vram_total_mib` | integer | ha |  |  |
| `note` | text | ha |  |  |
| `updated_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `gpu_gate_state_id_check: CHECK ((id = 1))`; `gpu_gate_state_pkey: PRIMARY KEY (id)`

### `core.gpu_overrides`

Qo'lda override jurnali (genesis_gpu override). Faol = starts_at <= hozir < ends_at va bekor qilinmagan; eng yangisi amal qiladi.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `mode` | text | ha |  |  |
| `allowed_classes` | text[] | ha |  |  |
| `starts_at` | timestamp with time zone | yo'q | now() |  |
| `ends_at` | timestamp with time zone | yo'q |  |  |
| `reason` | text | yo'q |  |  |
| `created_by` | text | yo'q | CURRENT_USER |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |
| `cancelled_at` | timestamp with time zone | ha |  |  |
| `cancelled_by` | text | ha |  |  |

Cheklovlar: `gpu_overrides_check: CHECK (((mode IS NOT NULL) OR (allowed_classes IS NOT NULL)))`; `gpu_overrides_check1: CHECK ((ends_at > starts_at))`; `gpu_overrides_mode_check: CHECK ((mode = ANY (ARRAY['coresident'::text, 'timeslice'::text, 'dedicated'::text])))`; `gpu_overrides_pkey: PRIMARY KEY (id)`

### `core.heartbeats`

Jonlilik: har servis o'z qatorini yangilaydi (W0-08 signali).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `component` | text | yo'q |  |  |
| `instance` | text | yo'q | 'main'::text |  |
| `last_beat_at` | timestamp with time zone | yo'q | now() |  |
| `started_at` | timestamp with time zone | ha |  |  |
| `status` | text | yo'q | 'ok'::text |  |
| `version` | text | ha |  |  |
| `details` | jsonb | yo'q | '{}'::jsonb |  |

Cheklovlar: `heartbeats_status_check: CHECK ((status = ANY (ARRAY['ok'::text, 'degraded'::text, 'error'::text, 'stopped'::text])))`; `heartbeats_pkey: PRIMARY KEY (component, instance)`

### `core.job_types`

Job turlari (CONTRACTS §2): kim yaratadi, kim bajaradi. RLS shu jadvalga tayanadi.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `type` | text | yo'q |  |  |
| `gpu_class` | text | yo'q |  |  |
| `creators` | text[] | yo'q |  | INSERT qila oladigan DB rollari (w0_admin doim) |
| `executors` | text[] | yo'q |  | Oladigan (claim) va yakunlaydigan DB rollari |
| `lease_seconds` | integer | yo'q | 300 |  |
| `max_attempts` | integer | yo'q | 5 |  |
| `description` | text | ha |  |  |

Cheklovlar: `job_types_gpu_class_check: CHECK ((gpu_class = ANY (ARRAY['none'::text, 'llm'::text, 'vision'::text])))`; `job_types_lease_seconds_check: CHECK ((lease_seconds > 0))`; `job_types_max_attempts_check: CHECK ((max_attempts > 0))`; `job_types_pkey: PRIMARY KEY (type)`

### `core.jobs`

Ish navbati (CONTRACTS §2). SKIP LOCKED mantiqi W0-05 da (shared/queue).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `type` | text | yo'q |  |  |
| `payload` | jsonb | yo'q | '{}'::jsonb |  |
| `status` | text | yo'q | 'queued'::text | queued \| running \| done \| failed (run_after da qayta urinadi) \| dead (max_attempts dan keyin, qo'lda qayta navbat) |
| `gpu_class` | text | yo'q | 'none'::text |  |
| `priority` | smallint | yo'q | 0 |  |
| `attempts` | integer | yo'q | 0 |  |
| `max_attempts` | integer | yo'q | 5 |  |
| `run_after` | timestamp with time zone | yo'q | now() |  |
| `idempotency_key` | text | ha |  |  |
| `locked_by` | text | ha |  |  |
| `locked_at` | timestamp with time zone | ha |  |  |
| `heartbeat_at` | timestamp with time zone | ha |  |  |
| `last_error` | text | ha |  |  |
| `created_by` | text | yo'q | CURRENT_USER |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |
| `updated_at` | timestamp with time zone | yo'q | now() |  |
| `finished_at` | timestamp with time zone | ha |  |  |
| `lease_until` | timestamp with time zone | ha |  | running job egasining muddati; o'tsa job qaytadi (ishchi o'lgan) yoki dead |
| `target_role` | text | ha |  | Faqat shu rol olsin (masalan daily_close: w2_analyst yoki w3_dashboard). NULL = turning har qanday bajaruvchisi |
| `result` | jsonb | ha |  |  |

Cheklovlar: `jobs_gpu_class_check: CHECK ((gpu_class = ANY (ARRAY['none'::text, 'llm'::text, 'vision'::text])))`; `jobs_status_check: CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'done'::text, 'failed'::text, 'dead'::text])))`; `jobs_type_fkey: FOREIGN KEY (type) REFERENCES core.job_types(type)`; `jobs_pkey: PRIMARY KEY (id)`

Indekslar: `jobs_idempotency_key`, `jobs_lease`, `jobs_ready`, `jobs_type_status`

### `core.media`

Media fayl (W1). Bitta xabar = bitta media; albom elementlari alohida xabarlar.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `source_id` | bigint | yo'q |  |  |
| `tg_message_id` | bigint | yo'q |  |  |
| `message_sent_at` | timestamp with time zone | yo'q |  | messages.sent_at: xabar partitionini topish uchun |
| `kind` | text | yo'q |  |  |
| `status` | text | yo'q | 'pending'::text |  |
| `path` | text | ha |  | paths.media ga nisbatan: yil/oy/guruh/fayl (D-13) |
| `bytes` | bigint | ha |  |  |
| `sha256` | text | ha |  |  |
| `mime` | text | ha |  |  |
| `duration_s` | numeric(10,3) | ha |  |  |
| `width` | integer | ha |  |  |
| `height` | integer | ha |  |  |
| `tg_file_unique_id` | text | ha |  |  |
| `error` | text | ha |  |  |
| `downloaded_at` | timestamp with time zone | ha |  |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `media_kind_check: CHECK ((kind = ANY (ARRAY['voice'::text, 'video'::text, 'video_note'::text, 'photo'::text, 'document'::text, 'audio'::text, 'sticker'::text, 'other'::text])))`; `media_status_check: CHECK ((status = ANY (ARRAY['pending'::text, 'downloaded'::text, 'too_large'::text, 'failed'::text, 'skipped'::text])))`; `media_source_id_fkey: FOREIGN KEY (source_id) REFERENCES core.sources(id)`; `media_pkey: PRIMARY KEY (id)`; `media_source_id_tg_message_id_key: UNIQUE (source_id, tg_message_id)`

Indekslar: `media_status`

### `core.messages`

Xom xabar (W1). Oylik partition, kalit (source_id, tg_message_id, sent_at) idempotent.

Partition: `RANGE (sent_at)` (oylik, §4).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `source_id` | bigint | yo'q |  |  |
| `tg_message_id` | bigint | yo'q |  |  |
| `sent_at` | timestamp with time zone | yo'q |  | Telegram date (UTC). Xabar uchun o'zgarmaydi: partition kaliti |
| `local_day` | date | ha | generated: core.tashkent_day(sent_at) | Hisobot kuni (Asia/Tashkent) |
| `sender_tg_id` | bigint | ha |  |  |
| `sender_name` | text | ha |  |  |
| `sender_is_bot` | boolean | yo'q | false |  |
| `kind` | text | yo'q | 'text'::text |  |
| `text` | text | ha |  | Matn yoki caption. relevance=no episode muddati o'tsa NULL qilinadi (P-05) |
| `reply_to_tg_message_id` | bigint | ha |  |  |
| `thread_id` | bigint | ha |  |  |
| `forward_from` | text | ha |  |  |
| `edited_at` | timestamp with time zone | ha |  |  |
| `edit_count` | integer | yo'q | 0 |  |
| `deleted_at` | timestamp with time zone | ha |  |  |
| `text_purged_at` | timestamp with time zone | ha |  | Matn qachon o'chirilgan (purge_unrelated_text) |
| `raw` | jsonb | ha |  | Ixtiyoriy: Telegram obyektidan kerakli qism (sirsiz) |
| `ingested_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `messages_kind_check: CHECK ((kind = ANY (ARRAY['text'::text, 'voice'::text, 'video'::text, 'video_note'::text, 'photo'::text, 'document'::text, 'audio'::text, 'sticker'::text, 'service'::text, 'other'::text])))`; `messages_source_id_fkey: FOREIGN KEY (source_id) REFERENCES core.sources(id)`; `messages_pkey: PRIMARY KEY (source_id, tg_message_id, sent_at)`

Indekslar: `messages_local_day`, `messages_sender_time`, `messages_sent_at`, `messages_source_time`

### `core.partitioned_tables`

Oylik partitionlanadigan jadvallar ro'yxati (ensure_month_partitions uchun).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `parent` | text | yo'q |  |  |
| `key_col` | text | yo'q |  |  |

Cheklovlar: `partitioned_tables_pkey: PRIMARY KEY (parent)`

### `core.people`

Odamlar reestri; hodimlar qo'lda to'ldiriladi (Q5). W1/W2 faqat o'qiydi.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `tg_user_id` | bigint | ha |  | Telegram user id; messages.sender_tg_id bilan bog'lanadi (FK yo'q) |
| `username` | text | ha |  |  |
| `display_name` | text | ha |  |  |
| `role` | text | yo'q | 'unknown'::text |  |
| `is_manager` | boolean | yo'q | false |  |
| `note` | text | ha |  |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |
| `updated_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `people_role_check: CHECK ((role = ANY (ARRAY['employee'::text, 'client'::text, 'unknown'::text])))`; `people_pkey: PRIMARY KEY (id)`; `people_tg_user_id_key: UNIQUE (tg_user_id)`

### `core.presence_sessions`

Xonada bo'lish oralig'i (kirdi -> chiqdi). D-24: xona ichidagi chiqishlar hozircha "xonada" (W4-07 ochiq).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `camera_id` | text | yo'q |  |  |
| `anon_person_id` | bigint | yo'q |  |  |
| `local_day` | date | yo'q |  |  |
| `kind` | text | yo'q | 'room'::text |  |
| `started_at` | timestamp with time zone | yo'q |  |  |
| `ended_at` | timestamp with time zone | ha |  | NULL = kun oxirigacha chiqish ko'rilmagan |
| `minutes` | numeric | ha | generated: (date_part('epoch'::text, (ended_at - started_at)) / (60)::double precision) |  |
| `enter_event_id` | bigint | ha |  |  |
| `exit_event_id` | bigint | ha |  |  |
| `inferred` | boolean | yo'q | false |  |
| `confidence` | real | ha |  |  |
| `analysis_run` | text | yo'q |  |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `presence_sessions_kind_check: CHECK ((kind = 'room'::text))`; `presence_sessions_anon_person_id_fkey: FOREIGN KEY (anon_person_id) REFERENCES core.anon_persons(id)`; `presence_sessions_camera_id_fkey: FOREIGN KEY (camera_id) REFERENCES core.cameras(id)`; `presence_sessions_enter_event_id_fkey: FOREIGN KEY (enter_event_id) REFERENCES core.camera_events(id)`; `presence_sessions_exit_event_id_fkey: FOREIGN KEY (exit_event_id) REFERENCES core.camera_events(id)`; `presence_sessions_pkey: PRIMARY KEY (id)`; `presence_sessions_analysis_run_anon_person_id_started_at_key: UNIQUE (analysis_run, anon_person_id, started_at)`

### `core.report_deliveries`

Hisobot yetkazish jurnali (W3-07 yozadi; W3 [CONTRACT] 11).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `report_type` | text | yo'q |  |  |
| `local_day` | date | yo'q |  |  |
| `target_chat` | text | yo'q |  |  |
| `status` | text | yo'q | 'pending'::text |  |
| `attempts` | integer | yo'q | 0 |  |
| `file_path` | text | ha |  |  |
| `tg_message_id` | bigint | ha |  |  |
| `error` | text | ha |  |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |
| `sent_at` | timestamp with time zone | ha |  |  |

Cheklovlar: `report_deliveries_report_type_check: CHECK ((report_type = ANY (ARRAY['tg_daily_excel'::text, 'camera_daily'::text])))`; `report_deliveries_status_check: CHECK ((status = ANY (ARRAY['pending'::text, 'sent'::text, 'failed'::text])))`; `report_deliveries_pkey: PRIMARY KEY (id)`; `report_deliveries_report_type_local_day_target_chat_key: UNIQUE (report_type, local_day, target_chat)`

### `core.response_pairs`

So'rov -> javob (D-19): W2-09 qoida asosida to'ldiradi; W3 v_response_times orqali o'qiydi.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `source_id` | bigint | yo'q |  |  |
| `request_tg_message_id` | bigint | yo'q |  |  |
| `requested_at` | timestamp with time zone | yo'q |  |  |
| `local_day` | date | ha | generated: core.tashkent_day(requested_at) |  |
| `requester_tg_id` | bigint | ha |  |  |
| `episode_id` | bigint | ha |  |  |
| `deadline_at` | timestamp with time zone | yo'q |  |  |
| `status` | text | yo'q | 'pending'::text | answered = deadline gacha javob; missed = deadline o'tdi (keyin javob bo'lsa late_answer_at) |
| `answer_tg_message_id` | bigint | ha |  |  |
| `answered_at` | timestamp with time zone | ha |  |  |
| `response_s` | numeric | ha | generated: date_part('epoch'::text, (answered_at - requested_at)) | answered_at - requested_at, soniya (generated) |
| `responder_tg_id` | bigint | ha |  |  |
| `responder_person_id` | bigint | ha |  |  |
| `responder_role` | text | ha |  |  |
| `late_answer_at` | timestamp with time zone | ha |  |  |
| `rule_version` | text | yo'q | 'v1'::text |  |
| `computed_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `response_pairs_responder_role_check: CHECK ((responder_role = ANY (ARRAY['employee'::text, 'client'::text, 'unknown'::text])))`; `response_pairs_status_check: CHECK ((status = ANY (ARRAY['pending'::text, 'answered'::text, 'missed'::text])))`; `response_pairs_episode_id_fkey: FOREIGN KEY (episode_id) REFERENCES core.episodes(id) ON DELETE SET NULL`; `response_pairs_responder_person_id_fkey: FOREIGN KEY (responder_person_id) REFERENCES core.people(id)`; `response_pairs_source_id_fkey: FOREIGN KEY (source_id) REFERENCES core.sources(id)`; `response_pairs_pkey: PRIMARY KEY (id)`; `response_pairs_source_id_request_tg_message_id_rule_version_key: UNIQUE (source_id, request_tg_message_id, rule_version)`

Indekslar: `response_pairs_day`, `response_pairs_pending`

### `core.review_queue`

Odam tekshiruvi (P-08). W2 qo'shadi; W3 faqat human_decision/reviewed_by/reviewed_at ni yozadi.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `item_type` | text | yo'q |  |  |
| `item_id` | bigint | yo'q |  |  |
| `reason` | text | yo'q |  |  |
| `confidence` | real | ha |  |  |
| `details` | jsonb | yo'q | '{}'::jsonb |  |
| `human_decision` | text | ha |  |  |
| `reviewed_by` | text | ha |  |  |
| `reviewed_at` | timestamp with time zone | ha |  |  |
| `status` | text | ha | generated: CASE WHEN (reviewed_at IS NULL) THEN 'open'::text ELSE 'resolved'::text END |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `review_queue_item_type_check: CHECK ((item_type = ANY (ARRAY['analysis'::text, 'relevance'::text, 'transcript'::text, 'response_pair'::text, 'camera_event'::text])))`; `review_queue_pkey: PRIMARY KEY (id)`; `review_queue_item_type_item_id_reason_key: UNIQUE (item_type, item_id, reason)`

Indekslar: `review_queue_open`

### `core.scheduler_runs`

W0-08 scheduler: har vazifa kuniga bir marta (run_key = kun); o'tkazib yuborilgani keyingi ishga tushishda bajariladi.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `task` | text | yo'q |  |  |
| `run_key` | text | yo'q |  |  |
| `started_at` | timestamp with time zone | yo'q | now() |  |
| `finished_at` | timestamp with time zone | ha |  |  |
| `ok` | boolean | ha |  |  |
| `result` | jsonb | yo'q | '{}'::jsonb |  |

Cheklovlar: `scheduler_runs_pkey: PRIMARY KEY (task, run_key)`

### `core.schema_migrations`

Qo'llangan migratsiyalar (migrate.py yozadi).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `version` | integer | yo'q |  |  |
| `name` | text | yo'q |  |  |
| `checksum` | text | yo'q |  |  |
| `applied_at` | timestamp with time zone | yo'q | now() |  |
| `applied_by` | text | yo'q | CURRENT_USER |  |
| `duration_ms` | integer | ha |  |  |

Cheklovlar: `schema_migrations_pkey: PRIMARY KEY (version)`

### `core.source_members`

Guruh menejeri/adminlari (W3 [CONTRACT] 3). Adminlarni W1 aniqlaydi, menejer qo'lda.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `source_id` | bigint | yo'q |  |  |
| `person_id` | bigint | yo'q |  |  |
| `member_role` | text | yo'q |  |  |
| `origin` | text | yo'q | 'telegram'::text |  |
| `updated_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `source_members_member_role_check: CHECK ((member_role = ANY (ARRAY['manager'::text, 'admin'::text])))`; `source_members_origin_check: CHECK ((origin = ANY (ARRAY['telegram'::text, 'manual'::text])))`; `source_members_person_id_fkey: FOREIGN KEY (person_id) REFERENCES core.people(id) ON DELETE CASCADE`; `source_members_source_id_fkey: FOREIGN KEY (source_id) REFERENCES core.sources(id) ON DELETE CASCADE`; `source_members_pkey: PRIMARY KEY (source_id, person_id, member_role)`

### `core.sources`

Manba: guruh (keyin DM, W5). W1 yozadi.

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `kind` | text | yo'q |  |  |
| `tg_chat_id` | bigint | yo'q |  | Telegram chat id (supergroup: -100...). Asl xabarga havola uchun |
| `tg_username` | text | ha |  | Public username (bo'lsa): havola t.me/<username>/<msg_id> |
| `title` | text | yo'q |  |  |
| `enabled` | boolean | yo'q | true |  |
| `reader` | text | ha |  | Qaysi akkaunt/bot o'qiydi (W1-06 reestri) |
| `is_present` | boolean | yo'q | true | O'quvchi akkaunt guruhda bormi |
| `admins_checked_at` | timestamp with time zone | ha |  |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |
| `updated_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `sources_kind_check: CHECK ((kind = ANY (ARRAY['group'::text, 'supergroup'::text, 'channel'::text, 'dm'::text])))`; `sources_pkey: PRIMARY KEY (id)`; `sources_tg_chat_id_key: UNIQUE (tg_chat_id)`

### `core.transcripts`

Media matni (W2). Oylik partition xabar vaqti bo'yicha, xabar bilan birga arxivlanadi.

Partition: `RANGE (message_sent_at)` (oylik, §4).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `media_id` | bigint | yo'q |  |  |
| `provider` | text | yo'q |  |  |
| `model` | text | yo'q | ''::text |  |
| `message_sent_at` | timestamp with time zone | yo'q |  |  |
| `source_id` | bigint | yo'q |  |  |
| `tg_message_id` | bigint | yo'q |  |  |
| `language` | text | ha |  |  |
| `text` | text | ha |  |  |
| `confidence` | real | ha |  |  |
| `duration_s` | numeric(10,3) | ha |  |  |
| `cost_usd` | numeric(12,6) | ha |  |  |
| `is_primary` | boolean | yo'q | true | Dashboard ko'rsatadigan asosiy transkript (bake-off da bir nechta provayder) |
| `text_purged_at` | timestamp with time zone | ha |  |  |
| `created_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `transcripts_media_id_fkey: FOREIGN KEY (media_id) REFERENCES core.media(id)`; `transcripts_pkey: PRIMARY KEY (media_id, provider, model, message_sent_at)`

Indekslar: `transcripts_message`

### `core.video_gaps`

Yozuv oynasi ichidagi uzilishlar (manifest type=gap).

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `camera_id` | text | yo'q |  |  |
| `local_day` | date | yo'q |  |  |
| `from_at` | timestamp with time zone | yo'q |  |  |
| `to_at` | timestamp with time zone | yo'q |  |  |
| `duration_s` | numeric(10,3) | yo'q |  |  |
| `reason` | text | ha |  |  |

Cheklovlar: `video_gaps_camera_id_fkey: FOREIGN KEY (camera_id) REFERENCES core.cameras(id)`; `video_gaps_pkey: PRIMARY KEY (id)`; `video_gaps_camera_id_from_at_key: UNIQUE (camera_id, from_at)`

### `core.video_segments`

Yozilgan video bo'laklari (W4). Manba: <video_root>\<kamera>\<kun>\manifest.jsonl (v1), core.import_manifest_record().

| Ustun | Tur | NULL | Standart | Izoh |
|---|---|---|---|---|
| `id` | bigint | yo'q | identity |  |
| `camera_id` | text | yo'q |  |  |
| `local_day` | date | yo'q |  | Manifest "day" (mahalliy kun papkasi) |
| `file` | text | yo'q |  |  |
| `path` | text | ha |  |  |
| `started_at` | timestamp with time zone | yo'q |  |  |
| `ended_at` | timestamp with time zone | yo'q |  |  |
| `duration_s` | numeric(10,3) | yo'q |  |  |
| `bytes` | bigint | ha |  |  |
| `session` | text | ha |  |  |
| `seq` | integer | ha |  |  |
| `break_before` | boolean | yo'q | false | Uzilish belgisi: oldingi bo'lak bilan orada uzilish bor |
| `gap_before_s` | numeric(10,3) | ha |  |  |
| `end_source` | text | ha |  | list = ffmpeg ro'yxatidan aniq; mtime = bo'lak yopilmagan (uzilish) |
| `closed_reason` | text | ha |  |  |
| `codec` | text | ha |  |  |
| `width` | integer | ha |  |  |
| `height` | integer | ha |  |  |
| `measured_fps` | numeric(6,2) | ha |  | O'lchangan fps (W4 probe); manifest v1 da yo'q, NULL bo'lishi mumkin |
| `manifest_v` | smallint | ha |  |  |
| `is_keep` | boolean | yo'q | false |  |
| `deleted_at` | timestamp with time zone | ha |  | Aylanma bufer faylni o'chirgan vaqt (qator qoladi) |
| `imported_at` | timestamp with time zone | yo'q | now() |  |

Cheklovlar: `video_segments_closed_reason_check: CHECK ((closed_reason = ANY (ARRAY['segment'::text, 'stop'::text, 'stall'::text, 'ffmpeg_exit'::text, 'recovered'::text])))`; `video_segments_end_source_check: CHECK ((end_source = ANY (ARRAY['list'::text, 'mtime'::text])))`; `video_segments_camera_id_fkey: FOREIGN KEY (camera_id) REFERENCES core.cameras(id)`; `video_segments_pkey: PRIMARY KEY (id)`; `video_segments_camera_id_file_key: UNIQUE (camera_id, file)`

Indekslar: `video_segments_time`

## 10. View'lar (`api`, W3 faqat shularni o'qiydi)

### `api.v_alerts`

| Ustun | Tur |
|---|---|
| `alert_id` | bigint |
| `key` | text |
| `kind` | text |
| `severity` | text |
| `message` | text |
| `details` | jsonb |
| `first_seen_at` | timestamp with time zone |
| `last_seen_at` | timestamp with time zone |
| `resolved_at` | timestamp with time zone |
| `is_open` | boolean |
| `notified_at` | timestamp with time zone |
| `notified_by` | text |

### `api.v_analysis_feed`

| Ustun | Tur |
|---|---|
| `analysis_id` | bigint |
| `episode_id` | bigint |
| `source_id` | bigint |
| `source_title` | text |
| `tg_chat_id` | bigint |
| `tg_username` | text |
| `started_at` | timestamp with time zone |
| `ended_at` | timestamp with time zone |
| `local_day` | date |
| `message_count` | integer |
| `relevance` | text |
| `categories` | text[] |
| `primary_category` | text |
| `sentiment` | text |
| `severity` | smallint |
| `summary` | text |
| `evidence` | jsonb |
| `entities` | jsonb |
| `needs_reply` | boolean |
| `confidence` | real |
| `model_id` | text |
| `prompt_version` | text |
| `ai_label` | text |
| `in_review` | boolean |
| `created_at` | timestamp with time zone |

### `api.v_attendance_daily`

| Ustun | Tur |
|---|---|
| `local_day` | date |
| `anon_person_id` | bigint |
| `person_label` | text |
| `camera_id` | text |
| `camera_name` | text |
| `first_in_at` | timestamp with time zone |
| `last_out_at` | timestamp with time zone |
| `office_minutes` | numeric |
| `room_minutes` | numeric |
| `away_minutes` | numeric |
| `session_count` | integer |
| `uncertain` | boolean |
| `uncertainty_note` | text |
| `ai_label` | text |
| `analysis_run` | text |
| `computed_at` | timestamp with time zone |

### `api.v_attendance_today`

| Ustun | Tur |
|---|---|
| `local_day` | date |
| `anon_person_id` | bigint |
| `person_label` | text |
| `camera_id` | text |
| `camera_name` | text |
| `first_in_at` | timestamp with time zone |
| `last_out_at` | timestamp with time zone |
| `office_minutes` | numeric |
| `room_minutes` | numeric |
| `away_minutes` | numeric |
| `session_count` | integer |
| `uncertain` | boolean |
| `uncertainty_note` | text |
| `ai_label` | text |
| `analysis_run` | text |
| `computed_at` | timestamp with time zone |

### `api.v_camera_coverage`

| Ustun | Tur |
|---|---|
| `camera_id` | text |
| `camera_name` | text |
| `local_day` | date |
| `segments` | bigint |
| `recorded_s` | numeric |
| `gaps` | bigint |
| `gap_s` | numeric |
| `breaks` | bigint |
| `unclosed_segments` | bigint |
| `deleted_segments` | bigint |
| `first_start_at` | timestamp with time zone |
| `last_end_at` | timestamp with time zone |
| `window_s` | double precision |
| `coverage_pct` | numeric |

### `api.v_camera_events`

| Ustun | Tur |
|---|---|
| `event_id` | bigint |
| `camera_id` | text |
| `at` | timestamp with time zone |
| `local_day` | date |
| `direction` | text |
| `method` | text |
| `inferred` | boolean |
| `confidence` | real |
| `anon_person_id` | bigint |
| `person_label` | text |
| `track_id` | text |
| `video_segment_id` | bigint |
| `segment_file` | text |
| `segment_offset_s` | numeric(10,3) |
| `analysis_run` | text |

### `api.v_categories`

| Ustun | Tur |
|---|---|
| `code` | text |
| `name` | text |
| `sort` | smallint |
| `active` | boolean |
| `description` | text |

### `api.v_daily_category_counts`

| Ustun | Tur |
|---|---|
| `local_day` | date |
| `source_id` | bigint |
| `source_title` | text |
| `category` | text |
| `episode_count` | bigint |
| `negative_count` | bigint |
| `needs_reply_count` | bigint |

### `api.v_daily_source_counts`

| Ustun | Tur |
|---|---|
| `local_day` | date |
| `source_id` | bigint |
| `source_title` | text |
| `messages` | bigint |
| `client_messages` | bigint |
| `related_episodes` | bigint |
| `filtered_episodes` | bigint |
| `unsure_episodes` | bigint |
| `pending_episodes` | bigint |

### `api.v_daily_summary`

| Ustun | Tur |
|---|---|
| `local_day` | date |
| `scope` | text |
| `scope_key` | text |
| `summary` | text |
| `model_id` | text |
| `prompt_version` | text |
| `ai_label` | text |
| `created_at` | timestamp with time zone |

### `api.v_episode_messages`

| Ustun | Tur |
|---|---|
| `episode_id` | bigint |
| `source_id` | bigint |
| `source_title` | text |
| `tg_chat_id` | bigint |
| `tg_username` | text |
| `tg_message_id` | bigint |
| `sent_at` | timestamp with time zone |
| `local_day` | date |
| `sender_tg_id` | bigint |
| `sender_name` | text |
| `sender_role` | text |
| `sender_is_bot` | boolean |
| `kind` | text |
| `text` | text |
| `reply_to_tg_message_id` | bigint |
| `thread_id` | bigint |
| `forward_from` | text |
| `edited_at` | timestamp with time zone |
| `edit_count` | integer |
| `deleted_at` | timestamp with time zone |
| `text_purged` | boolean |
| `media_id` | bigint |
| `media_kind` | text |
| `media_path` | text |
| `media_status` | text |
| `media_duration_s` | numeric(10,3) |
| `transcript` | text |
| `transcript_provider` | text |
| `transcript_language` | text |
| `tg_link` | text |

### `api.v_episode_participants`

| Ustun | Tur |
|---|---|
| `episode_id` | bigint |
| `sender_tg_id` | bigint |
| `name` | text |
| `role` | text |
| `message_count` | bigint |
| `first_at` | timestamp with time zone |
| `last_at` | timestamp with time zone |

### `api.v_gpu_gate`

| Ustun | Tur |
|---|---|
| `mode` | text |
| `phase` | text |
| `allowed_classes` | text[] |
| `valid_until` | timestamp with time zone |
| `fresh` | boolean |
| `loaded_models` | jsonb |
| `vram_used_mib` | integer |
| `vram_total_mib` | integer |
| `note` | text |
| `updated_at` | timestamp with time zone |
| `active_override` | jsonb |

### `api.v_heartbeats`

| Ustun | Tur |
|---|---|
| `component` | text |
| `instance` | text |
| `status` | text |
| `last_beat_at` | timestamp with time zone |
| `age_s` | double precision |
| `started_at` | timestamp with time zone |
| `version` | text |
| `details` | jsonb |

### `api.v_messages_public`

| Ustun | Tur |
|---|---|
| `source_id` | bigint |
| `source_title` | text |
| `tg_chat_id` | bigint |
| `tg_username` | text |
| `tg_message_id` | bigint |
| `sent_at` | timestamp with time zone |
| `local_day` | date |
| `sender_tg_id` | bigint |
| `sender_name` | text |
| `sender_role` | text |
| `sender_is_bot` | boolean |
| `kind` | text |
| `text` | text |
| `reply_to_tg_message_id` | bigint |
| `thread_id` | bigint |
| `forward_from` | text |
| `edited_at` | timestamp with time zone |
| `edit_count` | integer |
| `deleted_at` | timestamp with time zone |
| `text_purged` | boolean |
| `media_id` | bigint |
| `media_kind` | text |
| `media_path` | text |
| `media_status` | text |
| `media_duration_s` | numeric(10,3) |
| `transcript` | text |
| `transcript_provider` | text |
| `transcript_language` | text |
| `tg_link` | text |

### `api.v_partition_health`

| Ustun | Tur |
|---|---|
| `parent` | name |
| `partition` | name |
| `bounds` | text |
| `is_default` | boolean |
| `est_rows` | bigint |

### `api.v_pipeline_status`

| Ustun | Tur |
|---|---|
| `type` | text |
| `gpu_class` | text |
| `status` | text |
| `jobs` | bigint |
| `oldest_queued_at` | timestamp with time zone |
| `last_change_at` | timestamp with time zone |
| `max_attempts_seen` | integer |

### `api.v_presence_sessions`

| Ustun | Tur |
|---|---|
| `session_id` | bigint |
| `local_day` | date |
| `camera_id` | text |
| `anon_person_id` | bigint |
| `person_label` | text |
| `kind` | text |
| `started_at` | timestamp with time zone |
| `ended_at` | timestamp with time zone |
| `minutes` | numeric |
| `inferred` | boolean |
| `enter_inferred` | boolean |
| `exit_inferred` | boolean |
| `confidence` | real |
| `analysis_run` | text |

### `api.v_response_times`

| Ustun | Tur |
|---|---|
| `response_pair_id` | bigint |
| `source_id` | bigint |
| `source_title` | text |
| `local_day` | date |
| `request_tg_message_id` | bigint |
| `requested_at` | timestamp with time zone |
| `requester_tg_id` | bigint |
| `deadline_at` | timestamp with time zone |
| `sla_s` | double precision |
| `status` | text |
| `within_sla` | boolean |
| `answered_at` | timestamp with time zone |
| `response_s` | numeric |
| `answer_tg_message_id` | bigint |
| `responder_tg_id` | bigint |
| `responder_person_id` | bigint |
| `responder_name` | text |
| `responder_role` | text |
| `late_answer_at` | timestamp with time zone |
| `episode_id` | bigint |
| `rule_version` | text |
| `computed_at` | timestamp with time zone |
| `request_link` | text |
| `answer_link` | text |

### `api.v_review_queue`

| Ustun | Tur |
|---|---|
| `review_id` | bigint |
| `item_type` | text |
| `item_id` | bigint |
| `reason` | text |
| `confidence` | real |
| `details` | jsonb |
| `status` | text |
| `human_decision` | text |
| `reviewed_by` | text |
| `reviewed_at` | timestamp with time zone |
| `created_at` | timestamp with time zone |
| `episode_id` | bigint |
| `source_id` | bigint |
| `source_title` | text |
| `summary` | text |
| `categories` | text[] |
| `sentiment` | text |

### `api.v_sources`

| Ustun | Tur |
|---|---|
| `source_id` | bigint |
| `kind` | text |
| `title` | text |
| `tg_chat_id` | bigint |
| `tg_username` | text |
| `enabled` | boolean |
| `is_present` | boolean |
| `admins_checked_at` | timestamp with time zone |
| `reader` | text |
| `managers` | jsonb |
| `admins` | jsonb |

### `api.v_staff_day`

| Ustun | Tur |
|---|---|
| `local_day` | date |
| `anon_person_id` | bigint |
| `staff_no` | smallint |
| `person_label` | text |
| `camera_id` | text |
| `camera_name` | text |
| `first_in_at` | timestamp with time zone |
| `last_out_at` | timestamp with time zone |
| `office_minutes` | numeric |
| `room_minutes` | numeric |
| `away_minutes` | numeric |
| `exit_count` | bigint |
| `exits` | jsonb |
| `first_in_frame` | text |
| `last_out_frame` | text |
| `frames` | jsonb |
| `session_count` | integer |
| `uncertain` | boolean |
| `uncertainty_note` | text |
| `ai_label` | text |
| `analysis_run` | text |
| `computed_at` | timestamp with time zone |

### `api.v_system_health`

| Ustun | Tur |
|---|---|
| `checked_at` | timestamp with time zone |
| `last_message_at` | timestamp with time zone |
| `last_analysis_at` | timestamp with time zone |
| `queued_jobs` | bigint |
| `running_jobs` | bigint |
| `dead_jobs` | bigint |
| `untranscribed_media` | bigint |
| `open_reviews` | bigint |
| `default_partition_rows` | bigint |
| `oldest_heartbeat_at` | timestamp with time zone |
| `db_bytes` | bigint |
| `open_alerts` | bigint |
| `critical_alerts` | bigint |
| `scheduler_beat_at` | timestamp with time zone |

## 11. Funksiyalar (`core`)

| Funksiya | Qaytaradi | Xavfsizlik | Kim chaqira oladi | Izoh |
|---|---|---|---|---|
| `core.create_month_partition(text, date)` | bigint | DEFINER | faqat egasi (ichki / trigger) |  |
| `core.default_months(text, text)` | TABLE(mon date) | DEFINER | faqat egasi (ichki / trigger) |  |
| `core.ensure_month_partitions(integer)` | jsonb | DEFINER | w0_admin | Joriy + keyingi N oy (config partitions.months_ahead) partitionlarini yaratadi va DEFAULT dagi qatorlarni ko'chiradi. Idempotent. W0-08 kunlik chaqiradi |
| `core.episodes_set_retention()` | trigger | DEFINER | faqat egasi (ichki / trigger) |  |
| `core.gpu_allowed_classes()` | text[] | DEFINER | w0_admin, w1_ingest, w2_analyst, w3_dashboard, w4_camera | Hozir olinishi mumkin bo'lgan gpu_class lar (gate e'loni; eskirgan bo'lsa faqat none) |
| `core.gpu_device_for(text)` | integer | DEFINER | w0_admin, w1_ingest, w2_analyst, w3_dashboard, w4_camera | D-30: shu gpu_class ishlatadigan GPU raqami (gpu.devices; yo'q bo'lsa 0). W2/W4 ishchilari modelni shu qurilmaga yuklaydi |
| `core.import_manifest_record(jsonb, text)` | text | INVOKER | w4_camera, w0_admin | W4 manifest.jsonl (v1) bitta qatorini video_segments/video_gaps ga idempotent yozadi |
| `core.month_bounds(date)` | record | INVOKER | faqat egasi (ichki / trigger) |  |
| `core.partition_name(text, date)` | text | INVOKER | faqat egasi (ichki / trigger) |  |
| `core.purge_unrelated_text(timestamp with time zone)` | jsonb | INVOKER | w0_admin | P-05: muddati o'tgan relevance=no episode matnini o'chiradi, metadata qoladi (W0-08 chaqiradi) |
| `core.review_decide(bigint, text, text, jsonb)` | jsonb | DEFINER | w3_dashboard, w0_admin | W3-03: odam qarori (confirm \| reject \| correct) faqat ochiq review_queue qatoriga; shu tranzaksiyada audit_log. correct: tuzatilgan qiymatlar details da (review_queue.details.human_correction ga ham yoziladi). reviewed_by vaqtincha dashboard-local (W3-05 gacha) |
| `core.tashkent_day(timestamp with time zone)` | date | INVOKER | w0_admin, w1_ingest, w2_analyst, w3_dashboard, w4_camera | Hisobot kuni: UTC vaqtdan Asia/Tashkent bo'yicha sana (kun chegarasi 00:00, P-07) |
| `core.tg_link(bigint, text, bigint)` | text | INVOKER | w0_admin, w1_ingest, w2_analyst, w3_dashboard, w4_camera | Asl xabarga havola: public username yoki t.me/c/<id>/<msg> (a'zolar uchun) |
| `core.touch_updated_at()` | trigger | INVOKER | faqat egasi (ichki / trigger) |  |

## 12. Huquqlar matritsasi (`core` jadvallari)

Belgilar: **rw** = SELECT+INSERT+UPDATE, **r** = SELECT, **i** = faqat INSERT, **r+u\*** = SELECT + UPDATE faqat `human_decision, reviewed_by, reviewed_at` (V011 dan oldin), **all** = + DELETE/TRUNCATE. V011 dan w3_dashboard `review_queue` va `audit_log` ga faqat `core.review_decide` orqali yozadi. `genesis_migrator` hamma narsa egasi. `api` view'lari: w3_dashboard va w0_admin SELECT.

| Jadval | w0_admin | w1_ingest | w2_analyst | w3_dashboard | w4_camera |
|---|---|---|---|---|---|
| `alerts` | all | — | — | — | — |
| `analyses` | all | — | rw | — | — |
| `anon_persons` | all | — | — | — | rw |
| `archive_log` | all | — | — | — | — |
| `audit_log` | all | — | — | — | — |
| `camera_event_frames` | all | — | — | — | rw |
| `camera_events` | all | — | — | — | rw |
| `cameras` | all | — | — | — | r |
| `categories` | all | — | r | — | — |
| `chat_context` | all | — | rw | — | — |
| `config` | all | r | — | — | r |
| `daily_presence` | all | — | — | — | rw |
| `daily_stats` | all | — | rw | — | — |
| `daily_summaries` | all | — | rw | — | — |
| `dashboard_users` | all | — | — | rw | — |
| `episode_messages` | all | — | rw | — | — |
| `episodes` | all | — | rw | — | — |
| `gpu_gate_log` | all | — | — | — | — |
| `gpu_gate_state` | all | — | — | — | — |
| `gpu_overrides` | all | — | — | — | — |
| `heartbeats` | all | rw | rw | rw | rw |
| `job_types` | all | r | r | r | r |
| `jobs` | all | rw | rw | rw | rw |
| `media` | all | rw | r | — | — |
| `messages` | all | rw | r | — | — |
| `partitioned_tables` | all | — | — | — | — |
| `people` | all | r | r | — | — |
| `presence_sessions` | all | — | — | — | rw |
| `report_deliveries` | all | — | — | rw | — |
| `response_pairs` | all | — | rw | — | — |
| `review_queue` | all | — | rw | r | — |
| `scheduler_runs` | all | — | — | — | — |
| `schema_migrations` | r | — | — | — | — |
| `source_members` | all | rw | — | — | — |
| `sources` | all | rw | — | — | — |
| `transcripts` | all | — | rw | — | — |
| `video_gaps` | all | — | — | — | rw |
| `video_segments` | all | — | — | — | rw |
