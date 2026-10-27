# PROTOTYPE — prototip uchun qaysi qadamlar shart (D-39)

Maqsad (D-39, D-38 ni almashtiradi): **3 sinov guruhi va 1 kamerada hammasi ishlaydi** — Telegram ham, kamera ham.
200 guruh, 50 kamera, ikkinchi GPU, yuk sinovi va menejerlar akkauntlari (W5) foydalanuvchi "productionga o'tamiz"
demaguncha rejaga kirmaydi.

Ishlaydigan oqim:
- **Telegram (bitta "Telegram" oynasi, D-40: W1 + W2 qadamlari ketma-ket):** 3 sinov guruhidagi xabar va media o'qiladi va
  bazaga yoziladi → voice OpenAI `gpt-transcribe` bilan matnga (D-43) → suhbat bo'laklanadi → A bosqich (kompaniyaga
  tegishlimi) → B bosqich (7 bo'lim, D-33) → kunlik tahlil Gemma oynasida (22:00–07:00): 22:10 da o'sha paytgacha kelgan
  xabarlar, 00:05 da 22:00–24:00 dagi qolganlari (`daily_close`), kun = Toshkent kalendar kuni, natija shundan keyin yakuniy (D-41) →
  dashboardda kategoriya sahifalari va kartalar, har yozuvda asl Telegram xabariga havola (voice uchun ham audio emas, havola).
- **Kamera:** yozuv 07:00–22:00, yopilgan 10 daqiqalik bo'laklar kunduzi ketma-ket tahlil qilinadi (D-41) → eshik hodisalari →
  Hodim 1..4 → keldi, ketdi, xonada bo'lgan vaqt → Kamera sahifasi (8503), faqat ruxsatli rollarga. Prototipda butun video
  saqlanadi (D-42).

Bu fayl rejani o'zgartirmaydi. Qadamlarning mazmuni va qabul mezonlari `windows/*/PLAN.md` da qanday bo'lsa, shunday qoladi.
Hech bir qadam o'chirilmaydi. "Prototipdan keyin" degani "hozir navbatda emas" degani.
Har qadamning belgisi `state.js` da: `"prototip": "ha" | "yo'q"`. Holatlar (`s`) o'zgartirilmadi.
Holatlar 2026-10-27 17:00 dagi `state.js` bo'yicha. Oldingi hisoblar: D-37 — 30 shart, D-38 — 21 shart.

## Qisqa hisob

| | Jami | Shart (ha) | Shundan `done` | Qolgan | Prototipdan keyin (yo'q) |
|---|---|---|---|---|---|
| W0 Core | 10 | 8 | 7 | 1 (W0-07) | 2 |
| Telegram: W1 TG-Ingest | 8 | 5 | 0 | 5 (W1-01, 02, 03, 04, 07) | 3 |
| Telegram: W2 TG-Analyst | 10 | 8 | 1 (W2-04) | 7 (W2-01, 02, 03, 05, 06, 08, 09) | 2 |
| W3 Dashboard | 8 | 5 | 4 | 1 (W3-03) | 3 |
| W4 Camera | 10 | 8 | 1 | 7 (W4-02…W4-08) | 2 |
| W5 DM userbots | 5 | 0 | 0 | 0 | 5 |
| **Hammasi** | **51** | **34** | **13** | **21** | **17** |

Qolgan 21 ta qadam: 4 tasi `doing` (W0-07, W1-01, W2-01, W4-02), 1 tasi `blocked` (W3-03; to'sig'i V011 bilan olingan,
holatni W3 oynasi o'zgartiradi), 16 tasi `todo`.

## 1. Prototip uchun shart bo'lgan qadamlar (34 ta)

### Kamera (qolgan 8 ta)

| Qadam | Holat | Nega shart |
|---|---|---|
| **W0-07 Windows servislar** | doing | GPU gate, scheduler va ishchilar RDP'siz ishlashi kerak. Reboot sinovi foydalanuvchi ruxsatini kutadi. D-41 jadvalini scheduler va gate sozlamasiga qo'llash shu qadam bilan birga (alohida) |
| W4-02 Video yozish | doing | yozuvsiz tahlil yo'q (10-27 kunduzgi yozuv ko'rilgach yopiladi) |
| W4-03 Haqiqat to'plami | todo | W4-04…W4-07 shu to'plamda o'lchanadi; haqiqat kuni 2026-10-27, `E:\data\video_keep` ga (D-42) |
| W4-04 Detektor, tracker, fps eshigi | todo | hodisalar uchun; darvoza o'tgach video rejimi "faqat hodisa kadrlari" ga o'tadi (D-42) |
| W4-05 Eshikdan o'tish hodisalari | todo | keldi/ketdi |
| W4-06 Hodim 1..4 (D-31) | todo | `api.v_staff_day` doimiy hodimlar bo'yicha |
| W4-07 Xonada bo'lish vaqti | todo | M4; D-32 formulalari |
| W4-08 Kunlik batch | todo | M4. D-41: kamera tahlili endi 07:00–22:00 da 10 daqiqalik bo'laklar bo'yicha (qadam nomidagi "22:10 dan" D-24 ga oid, qadam matni o'zgarmadi) |

Kamera uchun bajarilganlari: W4-01 (qabul, zona), W3-04 (Kamera sahifasi), W3-05 (login, rollar, kameraga alohida ruxsat).

### Telegram (qolgan 13 ta, D-40: bitta oynada ketma-ket)

| Qadam | Holat | Nega shart |
|---|---|---|
| W1-01 Ulanish (D-36) | doing | MTProto ulanishi, eski bot buzilmasligi |
| W1-02 Faqat-o'qish tinglovchi | todo | xabarlar `messages` ga tushadi |
| W1-03 Media yuklash va W2 job'lari | todo | voice fayllari va `transcribe` job'lari |
| **W1-04 Tahrir/o'chirish, reply, yuboruvchi roli** | todo | **qo'shildi.** Sabab: W2-04 bo'laklash reply zanjiri va "hodim javobi" (60 daq) qoidasiga tayanadi, A/B bosqich esa mijoz va hodim xabarini ajratishi kerak. Rolsiz hodim xabari mijoz shikoyati bo'lib hisoblanadi (eski botda shu xato bo'lgan, 2026-10-15) |
| W1-07 Ishonchlilik | todo | tinglovchi uzilsa o'zi tiklanadi, bitta nusxa qulfi; prototip kun bo'yi odamsiz ishlashi kerak |
| W2-01 Baholash to'plami | doing | **yengillashtirilgan (prototip):** 100 ta bo'lak, eski AI tasnifi taxminiy belgi sifatida (odam tasdiqlaydi/tuzatadi). Rejadagi 200–300 bo'lak va "har sinfdan 20 ta" mezoni prototipdan keyin |
| W2-02 Ovoz → matn | todo | **faqat OpenAI `gpt-transcribe` adapteri** (D-43); provayderlar solishtirishi prototipda yo'q |
| W2-03 Lokal Gemma runtime | todo | A/B bosqich modeli |
| W2-05 A bosqich | todo | kompaniyaga tegishlimi |
| W2-06 B bosqich | todo | 7 bo'lim (D-33), ishonch past bo'lsa `review_queue` |
| W2-08 Media tahlili | todo | voice → transkript → shu quvur (kamida voice qismi) |
| **W2-09 Kunlik agregatsiya** | todo | **qo'shildi.** Sabab: kunlik tahlil (`daily_close`, D-41 bo'yicha 22:10 dan) va dashboard kartalaridagi raqamlar (`daily_stats` → kategoriya bo'yicha soni, javob vaqtlari) shu qadamda hisoblanadi |
| W3-03 Kategoriya sahifalari | blocked | M2: 7 bo'lim sahifalari, filtrlar, asl xabarga havola, `review_queue`. **Ovoz havolasi** shu qadam ichida: voice yozuvida audio o'rniga Telegram xabariga havola (D-43) |

Telegram uchun bajarilgani: W2-04 (bo'laklash, natijasi saqlanadi; haqiqiy ma'lumotda tekshiruv W2-01 bilan).

### Bajarilgan umumiy asos (13 tadan qolgan 9 ta, `done`)

| Qadam | Nega shart |
|---|---|
| W0-01…W0-06 | muhit, papkalar va sirlar, PostgreSQL, sxema, navbat, GPU gate (M1) |
| W0-08 | scheduler (`daily_close`, kamera batch), signallar, retention (dry-run). D-42: video o'chirish faqat shu orqali |
| W3-01, W3-02 | dashboard ma'lumot qatlami (8503) |

## 2. Bog'liqliklar

```
Kamera:
W4-02 ─> W4-03 ─> W4-04 ─> W4-05 ─> W4-06 ─> W4-07 ─> W4-08 ─┐
                                                              ├─> M4: Kamera sahifasi (8503; W3-04, W3-05 done)
W0-07 (servislar, reboot sinovi, D-41 jadvali) ──────────────┘

Telegram (bitta oyna, ketma-ket):
W1-01 ─> W1-02 ─> W1-03 ─> W1-04 ─> W1-07
                     └─> W2-02 (voice) ─> W2-08
W2-01 (100 bo'lak) ─> W2-03 ─> W2-05 ─> W2-06 ─> W2-09 ─> W3-03 ─> M2
W2-04 (done) ──────────────┘
W0-07 (gate servis, D-41: Gemma 22:00–07:00) ─> W2-03/05/06 tungi ishi
```

- D-41 (tuzatilgan): kamera 07:00–22:00, Gemma 22:00–07:00, ustma-ust emas. 21:50–22:00 kamera bo'lagi qoidasi W0-07 / W4-08 da.
- D-42: W4 yozuvchisining o'z o'chirishi (`apps/w4-camera/app.toml` `retention_days = 14`) hali yoqilgan. U **W4-02 yopishda
  (2026-10-27 22:00 dan keyin)** o'chiriladi; 10-27 kuni `E:\data\video_keep` ga saqlanadi (W4).
- Eski dashboard 8502 va eski bot RDP session 2 da qo'lda ishlayapti, avtostarti yo'q. W0-07 reboot sinovidan keyin ularni
  qo'lda qayta yoqish kerak (foydalanuvchi qarori).

## 3. Tavsiya etilgan bajarilish tartibi

1. **W0-07 reboot sinovi** + D-41 jadvalini scheduler/gate ga qo'llash (foydalanuvchi ruxsati va vaqti bilan).
2. **Kamera:** W4-02 yopish (10-27 22:00 dan keyin) va shu yerda D-42 ni qo'llash (yozuvchining o'z o'chirishi o'chadi, 10-27 → `video_keep`) → W4-03 → W4-04 → W4-05 →
   W4-06 → W4-07 → W4-08 → Kamera sahifasini haqiqiy natija bilan tekshirish → **M4**.
3. **Telegram (parallel, bitta oynada ketma-ket):** W1-01 → W1-02 → W1-03 → W1-04 → W1-07 → W2-02 → W2-01 (100) → W2-03 →
   W2-05 → W2-06 → W2-08 → W2-09 → W3-03 → **M2**.

## 4. Prototipdan keyin (17 qadam; hozir navbatda emas, o'chirilmagan)

| Qadam | Nega keyin |
|---|---|
| W0-09 Soxta ma'lumot va shartnoma testlari | prototipga kerakli qismi (`genesis_test`, fixtures) oldindan qilingan |
| W0-10 Xavfsizlik va tiklash | M5. Eslatma: RDP 3389 hamma manzildan ochiq (SECURITY.md), bu xavf prototip davomida ham bor |
| W1-05 To'xtagandan keyin to'ldirish | M5; prototipda uzilish bo'lsa W1-07 qayta ulanadi, bo'shliq qo'lda ko'riladi |
| W1-06 Ko'p akkaunt reestri | W5 ga tayyorgarlik (D-39) |
| W1-08 48 soatlik sinov | M5, yuk sinovi (D-39) |
| W2-07 24 soatlik kontekst | tasnif bo'lak ichidagi kontekst bilan ishlaydi; kunlararo bog'lash keyin |
| W2-10 Sifat sikli | M5 (to'liq baholash, drift); prototipda odam tuzatishi W3-03 `review_queue` orqali |
| W3-06 Kunlik Excel | M3; prototip natijasi dashboardda ko'rinadi |
| W3-07 Reporter bot | M3 |
| W3-08 Servis va zaxira (dashboard) | prototipda 8503 qo'lda yoqiladi; W0-07 dan keyin oson qo'shiladi |
| W4-09 Sig'im benchmark | M5; 50 kamera (D-39) |
| W4-10 Maxfiylik | PRIVACY.md va rozilik matni keyin; prototipda D-42 va W0 retention |
| W5-01 … W5-05 | D-39: menejerlar akkauntlari "productionga o'tamiz" gacha yo'q |

## 5. Foydalanuvchi qarori kerak bo'lishi mumkin

- **W0-07 reboot sinovi** uchun vaqt va ruxsat; reboot'dan keyin eski 8502 va eski botni kim qayta yoqishi.
- ~~D-41 ustma-ust oyna va Telegram 22:10–00:00 xabarlari~~ — hal qilindi (D-41 tuzatishi, 2026-10-27).
- **Hodimlarga kamera haqida xabar berish va rozilik** (W4-10, W4-06). Prototip haqiqiy hodimlar bilan ishlaydi.
