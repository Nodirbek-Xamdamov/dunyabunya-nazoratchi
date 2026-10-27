# CONTRACTS v1.0 — oynalar orasidagi umumiy shartnomalar (kontur)

Bu yerda faqat **nima bo'lishi kerak** yozilgan. Aniq jadval ustunlari va migratsiya fayllarini W0-04 yozadi (`shared/` va `apps/w0-core/migrations/`). Shartnoma o'zgarsa faqat W0 o'zgartiradi.

## 1. Baza obyektlari (mantiqiy)

Telegram:
- `sources` — manba (guruh, keyin DM). Maydonlar: tur, Telegram id, nom, yoqilgan/o'chirilgan, qaysi akkaunt/bot o'qiydi.
- `people` — odamlar reestri: Telegram id, rol (`employee` | `client` | `unknown`), ko'rinadigan nom. Hodimlar ro'yxati qo'lda to'ldiriladi (Q5).
- `messages` — xom xabar: manba, Telegram message id, yuboruvchi, vaqt (UTC), matn, reply/forward, tahrir/o'chirilgan belgisi. Kalit: (manba, message id) — idempotent. Jadval oylar bo'yicha bo'lingan (partition, D-12).
- `media` — fayl: tur (voice, video, video_note, photo, document), yo'l, hajm, hash, davomiylik. Yo'l: `data/media/yil/oy/guruh/` (D-12).
- `transcripts` — media matni: provayder, til, matn, ishonchlilik, narx.
- `episodes` — suhbat bo'lagi (ketma-ket xabarlar to'plami): manba, boshlanish/tugash, qatnashchilar, `relevance` (yes/no/unsure), matn saqlash muddati.
- `analyses` — tasnif natijasi: episode, kategoriya(lar), kayfiyat, jiddiylik, qisqa xulosa, dalil (xabar id + iqtibos), obyektlar (mahsulot, buyurtma, menejer), ishonchlilik, `prompt_version`, `model_id`.
- `chat_context` — har chat uchun 24 soatlik yig'ma xulosa va ochiq masalalar ro'yxati.
- `daily_stats` — kunlik agregat (kategoriya bo'yicha soni, javob vaqti, ochiq masalalar).
- `review_queue` — ishonchlilik past yoki "aniq emas" natijalar, odam tekshiruvi uchun.

Kamera:
- `cameras` — kamera reestri: nom, RTSP (sir sifatida), eshik zonasi/chizig'i, fps, ishlash soatlari.
- `video_segments` — yozilgan video bo'laklari: kamera, boshlanish/tugash (UTC), yo'l, uzilish belgisi.
- `camera_events` — kirdi/chiqdi hodisalari: kamera, vaqt, yo'nalish, track/shaxs id, ishonchlilik, `inferred` belgisi (tiklangan hodisa).
- `anon_persons` — "Hodim N": kunlik yoki doimiy anonim id, embedding (bytea), birinchi/oxirgi ko'rilgan.
- `presence_sessions` — shaxsning xonada bo'lish oralig'i (kirdi–chiqdi), ishonchlilik.
- `daily_presence` — kunlik jami: birinchi kirish, oxirgi chiqish, xonada jami daqiqa, noaniqlik belgisi.

Umumiy:
- `jobs` — navbat. `heartbeats` — jonlilik. `config` — sozlamalar. `audit_log` — kim nimani ko'rdi/o'zgartirdi.

## 2. Job turlari (navbat)

| `type` | Yaratadi | Bajaradi | Resurs |
|---|---|---|---|
| `transcribe` | W1 | W2 | tashqi API |
| `analyze_image` | W1 | W2 | GPU (Gemma) |
| `analyze_episode` | W2 (segmentator) | W2 | GPU (Gemma) |
| `build_context` | W2 | W2 | GPU (Gemma) |
| `daily_close` | W0 scheduler | W2, W3 | CPU |
| `build_excel_tg` | W0 scheduler | W3 | CPU |
| `camera_day_batch` | W0 scheduler | W4 | GPU (YOLO) |
| `build_report_cam` | W0 scheduler | W3 | CPU |

Har bir job: `id`, `type`, `payload` (JSON), `status` (queued/running/done/failed/dead), `attempts`, `run_after`, `idempotency_key`, `gpu_class` (`none` | `llm` | `vision`). Qayta urinish eksponensial, 5 urinishdan keyin `dead` va admin signali.

## 3. GPU gate qoidasi

- Har bir GPU job `gpu_class` ni ko'rsatadi. Faqat W0 gate modelni yuklaydi/chiqaradi.
- Rejimlar (D-30): `timeslice` — standart (bitta GPU); `coresident` — zaxira (D-14, Gemma va YOLO birga); `dedicated` — ikkinchi GPU kelganda. Rejim va oyna vaqtlari sozlamada, qayta ishga tushirishsiz o'zgaradi.
- `timeslice` oynalari sozlamada (boshlang'ich, Asia/Tashkent): Telegram (Gemma, `llm`) 10:00–22:00, kamera (YOLO, `vision`) 22:00–10:00. Kamera tahlili bir kun orqada qolishi mumkin.
- `timeslice`: oyna almashishidan 15 daqiqa oldin yangi og'ir job olinmaydi, ishlayotganlari tugatiladi, keyin model almashadi.
- Kamera oynasida kamera job'i bo'lmasa GPU Gemma ga qaytariladi; kamera job'i kelsa yana kameraga o'tiladi (oyna tugashiga 15 daqiqadan kam qolganda kameraga o'tilmaydi).
- `dedicated`: `gpu_class` → GPU qurilma raqami sozlamada (`gpu.devices`); ishchi qurilmani `core.gpu_device_for(gpu_class)` dan oladi.
- Kechgacha tugamagan Telegram navbati keyingi oynada davom etadi. Excel faqat tahlil qilinganlarni oladi va "tahlil kutayotgan: N" ni ko'rsatadi.

## 4. Konfiguratsiya va sirlar

- `config.toml` (repoda, sirsiz) + `secrets/` (repodan tashqari). Barcha oynalar sozlamani W0 ning umumiy yuklagichi orqali o'qiydi.
- Vaqt: bazada UTC, biznes kun `Asia/Tashkent`, kun chegarasi 00:00.

## 5. O'qish/yozish huquqlari (DB rollari)

| Rol | Yozadi | O'qiydi |
|---|---|---|
| `w1_ingest` | sources, messages, media, jobs | people, config |
| `w2_analyst` | transcripts, episodes, analyses, chat_context, daily_stats, review_queue, jobs | messages, media, people |
| `w4_camera` | video_segments, camera_events, anon_persons, presence_sessions, daily_presence, jobs | cameras, config |
| `w3_dashboard` | odam javobi va uning audit yozuvi faqat `core.review_decide(...)` orqali (V011; `review_queue` va `audit_log` ga to'g'ridan-to'g'ri yozish yo'q) | hamma narsa (view'lar orqali) |
| `w0_admin` | hamma narsa | hamma narsa |

Odam tekshiruvi javobi (W3-03, V011, tafsilot `shared/SCHEMA.md` §4d):

```sql
core.review_decide(p_review_id bigint, p_decision text, p_reviewed_by text, p_details jsonb DEFAULT '{}') RETURNS jsonb
```

- `p_decision`: `confirm` | `reject` | `correct` (`correct` da tuzatilgan qiymatlar `p_details` da). Faqat ochiq qator; yopilgan yoki yo'q qator → xato.
- `p_reviewed_by`: kim qaror qildi; vaqtincha `dashboard-local` (W3-05 gacha).
- Har qaror shu tranzaksiyada `audit_log` ga yoziladi. EXECUTE: `w3_dashboard`, `w0_admin`.

## 6. Ko'rinishlar (view) — W3 uchun barqaror sirt

W3 jadvallarga emas, W0 yaratgan view'larga ulanadi: `v_messages_public`, `v_analysis_feed`, `v_daily_category_counts`, `v_response_times`, `v_attendance_today`, `v_attendance_daily`. Jadval o'zgarsa view saqlanadi, dashboard buzilmaydi.
