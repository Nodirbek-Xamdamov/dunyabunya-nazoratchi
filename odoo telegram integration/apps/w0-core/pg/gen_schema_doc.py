"""Write shared/SCHEMA.md: hand-written rules + tables/columns/views/functions/privileges read from the
live database and roles.py, so the document cannot drift from the schema.

    python -E gen_schema_doc.py [--db genesis]
"""
from __future__ import annotations

import argparse
from datetime import date

from pgtool import PG, ROOT, psql
import roles

OUT = ROOT / "shared" / "SCHEMA.md"
SEP = "\x1f"

HEADER = """# SCHEMA v1.4 — Genesis bazasi (W0-04; v1.1: V008, reja v1.3; v1.2: V009, D-30; v1.3: V010, W0-08; v1.4: V011, W3-03)

Bu hujjatni `apps/w0-core/pg/gen_schema_doc.py` yozadi: 1–8 bo'limlar qo'lda, 9–12 bo'limlar haqiqiy
bazadan (`{db}`) va `roles.py` dan avtomatik. Sxema faqat W0 migratsiyalari bilan o'zgaradi; boshqa oyna
o'zgarish so'rasa `QUESTIONS.md` ga `[CONTRACT]` yozadi, W0 yangi migratsiya qo'shadi va bu versiya oshadi.

Sana: {today} · Migratsiyalar: {migrations} · CONTRACTS v1.0 ni amalga oshiradi (+ D-11…D-33).

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
dsn = f"host={{pg['host']}} port={{pg['port']}} dbname={{pg['database']}} user=w1_ingest password={{pw}}"
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
`G:\\genesis\\archive\\<jadval>\\YYYY-MM.dump` → tekshiruv (pg_restore o'qiydi, dump ichidagi qatorlar soni = jadvaldagi) →
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

O'rnatish (oyna venv'ida): `pip install -e "D:\\Demos\\odoo telegram integration\\shared"` (psycopg 3 ni ham o'rnatadi).
```python
from genesis_queue import connect, JobQueue, run_worker, Permanent
q = JobQueue(connect("w1_ingest"))
q.enqueue("transcribe", {{"media_id": 42}}, key="media:42")          # takror chaqiriq jim e'tiborsiz (created=False)
run_worker(lambda: connect("w2_analyst"), ["transcribe", "analyze_image"], handler, gpu_classes=["none", "llm"])
```
Qo'lda: `jobs = q.claim([...], limit=1)` → `q.heartbeat(id)` (uzoq ishda) → `q.complete(id, result)` yoki `q.fail(id, xato)`;
`raise Permanent(...)` handler ichida → darhol `dead`. Idempotency kaliti bazada `<type>:<key>` bo'lib saqlanadi.
CLI (`w0_admin`): `python -m genesis_queue status | dead | requeue --id N | --type T [--db genesis_test]`.
Testlar (faqat `genesis_test`): `apps\\w0-core\\.venv\\Scripts\\python.exe -E shared\\genesis_queue\\tests\\run_queue_tests.py`.

## 4b. GPU gate (W0-06): `shared/genesis_gpu`, V007 + V009 (D-30)

- Faqat gate (W0, `w0_admin`) Ollama modelini yuklaydi/bo'shatadi (CONTRACTS §3). YOLO ni W4 ishchisi `vision` job ichida yuklaydi;
  gate uni `vision` ga ruxsat berish/bermaslik bilan boshqaradi. Har siklda nvidia-smi o'qiladi; boshqa GPU jarayonlari
  (masalan eski `camsvc.service`) `core.gpu_gate_log` ga yoziladi, ularga tegilmaydi.
- Sozlama `core.config` da, gate har siklda o'qiydi (qayta ishga tushirish kerak emas). V009 (D-30): `gpu.mode`
  (`timeslice` — standart | `coresident` — zaxira, D-14 | `dedicated` — ikkinchi GPU kelganda), `gpu.llm_window` 10:00–22:00
  (Telegram, Gemma), `gpu.vision_window` 22:00–10:00 (kamera, YOLO), `gpu.vision_idle_to_llm` true, `gpu.vision_idle_minutes` 5,
  `gpu.devices` `{{"llm": 0, "vision": 0}}`; V007 dan: `gpu.switch_guard_minutes` 15, `gpu.drain_timeout_minutes` 30,
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
- CLI (`apps\\w0-core\\.venv\\Scripts\\python.exe -m genesis_gpu ...`, `--db`): `run [--interval 15]` (gate sikli; W0-07 dan servis `genesis_gpu_gate`, `shared/SERVICES.md`; xato siklida darhol faqat `none` e'lon qilinadi, `phase = error`),
  `tick`, `state`, `log`, `mode timeslice|coresident|dedicated --reason ...`, `devices llm=N,vision=M --reason ...`,
  `override --allow none,llm,vision | --mode M --minutes N --reason ...`,
  `cancel ID`. Override va rejim o'zgarishlari `core.gpu_overrides` / `core.gpu_gate_log` da jurnallanadi.
- Testlar (faqat `genesis_test`, tezlatilgan soat, haqiqiy Ollama/nvidia-smi): `shared\\genesis_gpu\\tests\\run_gate_tests.py`.

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
  fayli (`var\\w4-camera\\recorder_heartbeat.json`) yozuv oynasida > 5 daqiqa eski (critical; `core.heartbeats` `w4_recorder` ga
  ko'chiriladi); `scheduler` — scheduler vazifasi yoki sikli xato.
- Kunlik vazifalar (`schedule.*`, Asia/Tashkent): `partitions` 01:00 (`ensure_month_partitions`); `daily_close` 00:05 — kechagi
  kun uchun ikki job (`target_role` w2_analyst va w3_dashboard, kalit `daily_close:<kun>:<rol>`); `camera_batch` 22:10 —
  har yoqilgan kamera uchun `camera_day_batch` (kalit `<kamera>:<kun>`; D-30 kamera oynasi ichida, natijada tekshiriladi);
  `retention` 03:30. `schedule.start_day` (2026-10-27) dan oldingi kunlar uchun job qo'yilmaydi.
- Retention (D-27, D-13, D-29): `retention.delete_enabled = false` — **faqat dry-run** (hisobot
  `var\\run\\retention\\retention_<kun>_dry-run.json`); foydalanuvchi ro'yxatni tasdiqlagach true. Video:
  `retention.video_root` (`E:\\data\\video`) ichidagi `<kamera>\\<YYYY-MM-DD>` papkalari, `retention.video_days` (14) dan eski.
  Hech qachon: `retention.protect_paths` (`E:\\Kamera raw data`, `E:\\data\\video_keep`, `F:\\genesis\\keep`) va ichidagilar,
  `KEEP`/`.keep` fayli bor kun papkasi, `video_segments.is_keep` kunlari, havolalar, sana nomli bo'lmagan papkalar; himoyalangan
  papka ichidagi video_root butunlay rad etiladi. O'chirilganda `video_segments.deleted_at`. Matn: `purge_unrelated_text`.
- CLI (`-m genesis_ops`, `--db`): `run`, `tick`, `check`, `retention` (doim dry-run), `alerts`, `runs`.
  Testlar: `shared\\genesis_ops\\tests\\run_ops_tests.py` (faqat `genesis_test`).

## 4d. Odam tekshiruvi javobi (W3-03 [CONTRACT]): `core.review_decide`, V011

```sql
core.review_decide(p_review_id bigint, p_decision text, p_reviewed_by text, p_details jsonb DEFAULT '{{}}') RETURNS jsonb
-- SECURITY DEFINER; EXECUTE: w3_dashboard, w0_admin
-- natija: {{"review_id", "decision", "reviewed_by", "reviewed_at", "audit_id"}}
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
| W4-02a | `paths.camera_video` = `E:\\data\\video` | qabul | `config.toml` o'zgardi; bo'sh `E:\\data\\camera` o'chirildi |
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
  `E:\\data\\camera_events\\YYYY\\MM\\DD\\<kamera>\\`. W3 faqat `api.v_staff_day` orqali ko'radi (W3-05: ruxsatli rollar).
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
cd "D:\\Demos\\odoo telegram integration\\apps\\w0-core\\pg"
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
"""


def rows(sql: str, db: str) -> list[list[str]]:
    out = psql(f"\\pset fieldsep '{SEP}'\n" + sql, db=db).stdout
    return [line.split(SEP) for line in out.splitlines() if line]


def md(text: str | None) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=PG["database"])
    db = ap.parse_args().db
    migs = rows("SELECT 'V' || lpad(version::text, 3, '0') || ' ' || name FROM core.schema_migrations ORDER BY version;", db)
    out = [HEADER.format(db=db, today=date.today().isoformat(), migrations=", ".join(m[0] for m in migs))]

    out.append("## 9. Jadvallar (`core`)\n")
    tables = rows("""SELECT c.relname, c.relkind, coalesce(obj_description(c.oid, 'pg_class'), ''),
                            coalesce(pg_get_partkeydef(c.oid), '')
                     FROM pg_class c WHERE c.relnamespace = 'core'::regnamespace AND c.relkind IN ('r','p')
                       AND NOT c.relispartition ORDER BY c.relname;""", db)
    for name, kind, comment, partkey in tables:
        out.append(f"### `core.{name}`\n")
        if comment:
            out.append(f"{comment}.\n")
        if partkey:
            out.append(f"Partition: `{partkey}` (oylik, §4).\n")
        out.append("| Ustun | Tur | NULL | Standart | Izoh |\n|---|---|---|---|---|")
        for col, typ, notnull, default, gen, cmt in rows(f"""
                SELECT a.attname, format_type(a.atttypid, a.atttypmod), CASE WHEN a.attnotnull THEN 'yo''q' ELSE 'ha' END,
                       regexp_replace(CASE WHEN a.attidentity <> '' THEN 'identity'
                            WHEN a.attgenerated <> '' THEN 'generated: ' || pg_get_expr(d.adbin, d.adrelid)
                            ELSE coalesce(pg_get_expr(d.adbin, d.adrelid), '') END, '[[:space:]]+', ' ', 'g'),
                       a.attgenerated, coalesce(col_description(a.attrelid, a.attnum), '')
                FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                WHERE a.attrelid = 'core.{name}'::regclass AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum;""", db):
            out.append(f"| `{col}` | {md(typ)} | {notnull} | {md(default)} | {md(cmt)} |")
        cons = rows(f"""SELECT regexp_replace(conname || ': ' || pg_get_constraintdef(oid), '[[:space:]]+', ' ', 'g') FROM pg_constraint
                        WHERE conrelid = 'core.{name}'::regclass AND contype IN ('p','u','f','c') ORDER BY contype, conname;""", db)
        idx = rows(f"""SELECT indexname FROM pg_indexes WHERE schemaname = 'core' AND tablename = '{name}'
                       AND indexname NOT IN (SELECT conname FROM pg_constraint WHERE conrelid = 'core.{name}'::regclass)
                       ORDER BY 1;""", db)
        if cons:
            out.append("\nCheklovlar: " + "; ".join(f"`{md(c[0])}`" for c in cons))
        if idx:
            out.append("\nIndekslar: " + ", ".join(f"`{i[0]}`" for i in idx))
        out.append("")

    out.append("## 10. View'lar (`api`, W3 faqat shularni o'qiydi)\n")
    for (view,) in rows("SELECT viewname FROM pg_views WHERE schemaname = 'api' ORDER BY 1;", db):
        cols = rows(f"""SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
                        WHERE a.attrelid = 'api.{view}'::regclass AND a.attnum > 0 ORDER BY a.attnum;""", db)
        out.append(f"### `api.{view}`\n")
        out.append("| Ustun | Tur |\n|---|---|")
        out += [f"| `{c}` | {md(t)} |" for c, t in cols]
        out.append("")

    out.append("## 11. Funksiyalar (`core`)\n")
    out.append("| Funksiya | Qaytaradi | Xavfsizlik | Kim chaqira oladi | Izoh |\n|---|---|---|---|---|")
    for sig, ret, secdef, cmt in rows("""SELECT format('%I.%I(%s)', n.nspname, p.proname, oidvectortypes(p.proargtypes)),
                                               pg_get_function_result(p.oid), CASE WHEN p.prosecdef THEN 'DEFINER' ELSE 'INVOKER' END,
                                               coalesce(obj_description(p.oid, 'pg_proc'), '')
                                        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                                        WHERE n.nspname = 'core' ORDER BY 1;""", db):
        who = ", ".join(roles.FUNCTIONS.get(sig, [])) or "faqat egasi (ichki / trigger)"
        out.append(f"| `{md(sig)}` | {md(ret)} | {secdef} | {who} | {md(cmt)} |")
    out.append("")

    out.append("## 12. Huquqlar matritsasi (`core` jadvallari)\n")
    out.append("Belgilar: **rw** = SELECT+INSERT+UPDATE, **r** = SELECT, **i** = faqat INSERT, **r+u\\*** = SELECT + "
               "UPDATE faqat `human_decision, reviewed_by, reviewed_at` (V011 dan oldin), **all** = + DELETE/TRUNCATE. "
               "V011 dan w3_dashboard `review_queue` va `audit_log` ga faqat `core.review_decide` orqali yozadi. "
               "`genesis_migrator` hamma narsa egasi. `api` view'lari: w3_dashboard va w0_admin SELECT.\n")
    app = roles.APP_ROLES
    review_fn = bool(rows("SELECT 1 FROM pg_proc WHERE oid = to_regprocedure('core.review_decide(bigint, text, text, jsonb)');", db))
    out.append("| Jadval | w0_admin | " + " | ".join(app) + " |\n|---|---|" + "---|" * len(app))
    for name, *_ in tables:
        cells = ["r" if name in roles.ADMIN_READ_ONLY else "all"]
        for r in app:
            if name in roles.WRITES.get(r, []):
                cells.append("rw")
            elif name in roles.READS.get(r, []):
                cells.append("r")
            elif r == roles.W3 and name in roles.W3_APPEND:
                cells.append("—" if review_fn else "i")
            elif r == roles.W3 and name == roles.W3_REVIEW_TABLE:
                cells.append("r" if review_fn else "r+u*")
            else:
                cells.append("—")
        out.append(f"| `{name}` | " + " | ".join(cells) + " |")
    out.append("")
    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {OUT} ({len(tables)} tables)")


if __name__ == "__main__":
    main()
