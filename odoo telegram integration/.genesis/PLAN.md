# PLAN v1.3 (qotirilgan reja; v1.2 dan farq: DECISIONS D-29…D-35; v1.1 dan: D-14…D-24; v1.0 dan: D-11, D-12, P-12)

v1.3 o'zgarishlari: fayl tuzilmasi `E:\data\<tur>\YYYY\MM\DD\<guruh_yoki_kamera>\` (D-29); GPU: timeslice standart, coresident zaxira, dedicated keyin (D-30; §3 oynalari 10:00–22:00 Telegram / 22:00–10:00 kamera); kamerada 4 ta doimiy "Hodim 1..4", mehmon hisobga olinmaydi, kirdi/chiqdi kadrlari saqlanadi (D-31); ishda/xonada formulasi (D-32); Telegram tasnifi faqat 7 bo'lim, "Boshqa" yo'q (D-33, §1 dagi kategoriyalar ro'yxati o'rniga); Odoo ga tegilmaydi (D-34); ustuvorlik: kamera va dashboard kamera sahifasi, Telegram parallel (D-35).

Holat: tasdiqlanguncha DRAFT, foydalanuvchi "tasdiqlandi" desa FROZEN. Keyin faqat `DECISIONS.md` orqali o'zgaradi.

## 1. Maqsad (oxirida nima chiqadi)

1. **Telegram tahlili.** Kompaniya guruhlaridagi (mijoz + hodim) yozishmalar, voice va videolar o'qiladi. Kompaniyaga tegishli bo'lmagan suhbatlar e'tiborsiz qoldiriladi. Qolganlari shikoyat, maqtov, savol, buyurtma va boshqa kategoriyalarga ajratiladi. Bot guruhga hech narsa yozmaydi. 24 soatlik kontekst bazadan olinadi. Natija bazaga saqlanadi.
2. **Dashboard.** Bazadagi ma'lumot kategoriyalar bo'yicha ko'rsatiladi. Har bir yozuv asl xabarga havola beradi va "AI taxmini" deb belgilanadi.
3. **Kunlik hisobot.** Har kuni 00:00 da Telegram bo'yicha 1 kunlik statistika Excel qilinib ichki guruhga yuboriladi. Kamera hisoboti ertalab 09:00 gacha.
4. **Kamera.** Eshikka qaragan kamera hodimlarning nechida kelgani, nechida ketgani va kun davomida xonada qancha bo'lganini hisoblaydi. Hodimlar ismsiz: "Hodim 1, Hodim 2...". Natija dashboardga chiqadi.
5. **Keyingi bosqich.** Menejerlarning shaxsiy (DM) yozishmalari userbot orqali (W5), faqat rozilik va huquqiy tekshiruvdan keyin.

Masshtab: 200+ Telegram guruh, 50+ kamera (kamera ish soatlarida yoziladi, 24/7 emas). Asosiy saqlash 2 TB disk (D-11, D-12, P-12). v1.2: yo'llar D-18 (media/video `E:\data`, baza `E:\pgdata`, zaxira F:, arxiv G:), WAL `D:\pgwal` (D-22), zaxira doirasi D-23, alohida PostgreSQL klaster 5433 (D-21).

## 2. Arxitektura (bir qarashda)

```
Telegram guruhlar ──> [W1 TG-Ingest] ──┐
Menejer DM (W5, keyin) ────────────────┤
Kamera (RTSP) ──> [W4 Camera: video yozish] ──┤
                                         ▼
                          [W0 Core: PostgreSQL + jobs navbati + GPU gate + scheduler]
                                         ▲                    ▲
                    [W2 TG-Analyst] ─────┘                    └──── [W4 Camera: tungi tahlil]
                    (Gemma 4 12B + STT API)                         (YOLO + tracker)
                                         ▼
                          [W3 Dashboard + Excel + Reporter bot]  (faqat bazadan o'qiydi)
```

Qoidalar:
- Oynalar bir-biri bilan faqat **baza va jobs navbati** orqali gaplashadi. Bir-birining kodini chaqirmaydi.
- Yig'ish (W1 va kamera video yozish) hech qachon to'xtamaydi va GPU ishlatmaydi. Tahlil GPU navbatida, orqada ishlaydi.
- W3 modelni chaqirmaydi, faqat bazadan o'qiydi.

## 3. Kunlik jadval (Asia/Tashkent)

| Vaqt | GPU | Nima ishlaydi |
|---|---|---|
| 07:00–22:00 | — | Kamera yozuvi (CPU, `-c copy`; v1.2, D-24) |
| 10:00–22:00 | Gemma 4 12B | Telegram tahlili (W2); v1.3 (D-30): `timeslice` Telegram oynasi (sozlamada) |
| 22:00–10:00 | YOLO | v1.3 (D-30): kamera oynasi (sozlamada). Kunlik kamera tahlili yozuv tugagach (22:10, D-24) boshlanadi, davom ettiriladigan; bir kun orqada qolishi mumkin (foydalanuvchi qabul qilgan). Kamera job'i bo'lmasa GPU Gemma ga qaytariladi, job kelsa yana kameraga |
| 00:00 | — | Kun yopiladi. Telegram Excel bazadan yig'iladi (LLM kerak emas) |
| 00:10 | — | Telegram Excel ichki guruhga (W3 reporter bot) |
| 09:00 | — | Kamera hisoboti (kelish/ketish, xonada bo'lish vaqti) |
| 10:00 | Gemma | Telegram oynasi; kamera oynasida tugamagan vision job keyingi kamera oynasida davom etadi |

`gpu_mode` sozlamasi (v1.3, D-30): `timeslice` — standart (10:00–22:00 Gemma, 22:00–10:00 YOLO, oynalar sozlamada; kamera oynasida kamera job'i bo'lmasa GPU Gemma ga qaytariladi); `coresident` — zaxira (D-14; W0-01 o'lchovi: Gemma + YOLO birga eng ko'pi 10.6 / 16.3 GB VRAM); `dedicated` — ikkinchi GPU kelganda. LLM runtime Ollama. Ko'p kameraga o'tishda rejim W4-09 benchmarkidan keyin qayta tanlanadi (DECISIONS.md ga yoziladi).

## 4. Oynalar va tartib

| Oyna | Papka | Mas'uliyat |
|---|---|---|
| W0 Core | `apps/w0-core` | baza, navbat, GPU gate, scheduler, servislar, shartnomalar |
| W1 TG-Ingest | `apps/w1-tg-ingest` | Telegram guruhlarini faqat-o'qish rejimida yig'ish |
| W2 TG-Analyst | `apps/w2-tg-analyst` | ovoz→matn, tegishlilik filtri, tasnif, kontekst |
| W3 Dashboard | `apps/w3-dashboard` | dashboard, Excel, reporter bot |
| W4 Camera | `apps/w4-camera` | video yozish, detektsiya, kirish/chiqish, xonada bo'lish vaqti |
| W5 DM userbots | `apps/w5-dm-userbots` | menejer DM (qulflangan) |

Boshlash tartibi: **W0-01…W0-06 → (W1, W3-01…03, W4-01…03 parallel) → W2 va W4 qolgani → W3 hisobotlar → W5.**
Oyna W0 ning kerakli qadami `done` bo'lmaguncha `locked`. Har bir `PLAN.md` boshida "Boshlash sharti" yozilgan.

## 5. Bosqichlar (milestone)

- **M1 Asos:** W0-04, W0-05, W0-06 tayyor. Boshqa oynalar to'liq ishlay oladi.
- **M2 Matn yo'li:** W1-03, W2-06, W3-03. Guruhdagi xabar dashboardda tasniflangan holda ko'rinadi.
- **M3 Kechki hisobot:** W2-09, W3-06, W3-07. 00:10 da Excel guruhga tushadi.
- **M4 Kamera davomati:** W4-07, W4-08, W3-04. Hodimlarning xonada bo'lish vaqti dashboardda.
- **M5 Qabul sinovi:** W0-10, W1-08, W2-10, W4-09. 48 soat uzluksiz sinov, aniqlik chegarasi bajarilgan.

## 6. Muvaffaqiyat mezonlari

- Bot guruhga tasodifan ham yozmaydi (kodda yozish funksiyasi yo'q).
- Kompaniyaga tegishli bo'lmagan suhbat matni 72 soatdan keyin saqlanmaydi (P-05).
- Tasnif aniqligi: W2-01 baholash to'plamida har bir asosiy sinf uchun F1 ≥ 0.75 (shikoyat sinfi uchun recall ≥ 0.85). Chegara W2-10 da tekshiriladi.
- Kamera: 1 kunlik qo'lda belgilangan to'plamda kirish/chiqish hodisalari recall ≥ 0.95, har bir hodim uchun kunlik jami vaqt xatosi ≤ 5 daqiqa.
- Tizim 48 soat davomida qo'lda aralashuvsiz ishlaydi, RDP uzilsa ham to'xtamaydi.
