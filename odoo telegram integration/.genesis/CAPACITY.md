# CAPACITY — 1 kadr/soniya bilan nechta kamera sig'adi

Hisob taxminiy (RTX 5060 Ti 16 GB uchun o'lchanmagan). Haqiqiy raqam W4-09 da o'lchanadi va shu fayl o'zgarmaydi, natija `logs/W4.md` ga yoziladi.

## Formula

```
kadr_bir_kameraga_kuniga = ish_soatlari × 3600 × fps
mavjud_kadr              = oyna_soniyalari × o'tkazuvchanlik(kadr/s) × zaxira_koeffitsienti
kameralar_soni           = mavjud_kadr / kadr_bir_kameraga_kuniga
```

Qabul qilingan qiymatlar: oyna 8 soat (00:00–08:00) = 28 800 s; zaxira koeffitsienti 0.7 (30% qayta urinish va uzilishlar uchun).

O'tkazuvchanlik = uchidan-uchigacha tezlik: JPEG o'qish, o'lchamni o'zgartirish, detektor, tracker, bazaga yozish.
- Konservativ: 30 kadr/s. Tipik: 60 kadr/s. Optimistik: 120 kadr/s.
- Bir kadr uchun GPU vaqti taxminan 3–15 ms (model hajmi va 640/1280 px ga bog'liq). Qolgan vaqtni JPEG ochish va tracker oladi.

## Natija (oyna 8 soat, 1 kadr/s)

| Kamera yig'ish soati | Konservativ | Tipik | Optimistik |
|---|---|---|---|
| 10 soat/kun (36 000 kadr) | 16 | 33 | 67 |
| 12 soat/kun (43 200 kadr) | 14 | 28 | 56 |
| 24 soat/kun (86 400 kadr) | 7 | 14 | 28 |

Boshqa fps (10 soat/kun):

| fps | Kadr/kamera | Konservativ | Tipik | Optimistik |
|---|---|---|---|---|
| 1 | 36 000 | 16 | 33 | 67 |
| 2 | 72 000 | 8 | 16 | 33 |
| 3 | 108 000 | 5 | 11 | 22 |
| 5 | 180 000 | 3 | 6 | 13 |

Xulosa: hozirgi 1 kamera uchun GPU hech qachon to'siq bo'lmaydi. 1 fps da 10–15 kamera xavfsiz, fps ni 3 gacha oshirsak ham 5 kameragacha sig'adi.

## Hisobga olinmagan to'siqlar

1. **Video dekodlash.** Agar tahlil yozib olingan 25 kadr/s video ustida bo'lsa, hamma kadr ochiladi: 10 soat × 25 = 900 000 kadr/kamera. Taxminan 300–600 kadr/s dekodlashda bu bir kameraga 25–50 daqiqa, ya'ni 8 soatda 10–19 kamera, detektsiyagacha. Shuning uchun reja (D-12): kamerada sub-stream past fps va past o'lchamga sozlanadi, bo'laklar dekodlashsiz yoziladi, tahlilda faqat kerakli kadrlar olinadi (quyidagi 50 kamera bo'limiga qarang).
2. **Diskka yozish (JPEG variant, D-12 da bekor qilingan).** 1080p JPEG taxminan 150–300 KB: bir kameraga kuniga 5–10 GB, 14 kunda 70–140 GB (720p da 3 baravar kam). W0-03 da disk byudjeti belgilanadi.
3. **Tashqi ko'rinish (ReID) embeddingi.** Har bir odam kesmasiga qo'shimcha vaqt ketadi. Kadrda odam ko'p bo'lsa o'tkazuvchanlik konservativ tomonga tushadi.
4. **Bo'sh kadrlar.** Oddiy kadr-farqi bilan odamsiz kadrlar tashlab yuborilsa sig'im 2–5 baravar oshadi. Bu optimizatsiya, hisobga kiritilmagan.

## 1 fps haqida eslatma

Vaqt xatosi (±1 soniya) kunlik jami uchun ahamiyatsiz. Muammo boshqa joyda: eshikdan o'tayotgan odam 1 fps da faqat 2–4 kadrda ko'rinadi, standart tracker (ByteTrack va h.k.) kadrlar orasida kichik siljishni kutadi. Shuning uchun fps sozlanadigan, W4-04 da 1/2/3 fps haqiqat to'plamida solishtiriladi va eng past yetarli qiymat tanlanadi.

## Kelajak: 50 kamera, kunlik tahlil (P-13)

Taxmin: 50 kamera, kuniga 10 soat, bazaviy 1 fps = 1 800 000 kadr/kun. Videoda 5 fps saqlansa 9 000 000 kadr dekodlanadi.

Oyna ichida tugatish uchun kerak bo'lgan uchidan-uchigacha tezlik:

| Oyna | Zaxirasiz | 30% zaxira bilan |
|---|---|---|
| 8 soat | 63 kadr/s | 89 kadr/s |
| 16 soat | 31 kadr/s | 45 kadr/s |
| 24 soat | 21 kadr/s | 30 kadr/s |

Taqqoslash: konservativ 30, tipik 60, optimistik 120 kadr/s. Xulosa: 50 kamera 8 soatga faqat optimistik holda sig'adi. Yo'llar: (1) oynani kengaytirish (W0-06 `coresident` yoki uzunroq oyna, tahlil 1 kun kechiksa mayli), (2) odamsiz kadrlarni tashlab yuborish (2–5 baravar), (3) eshik zonasida adaptiv fps.

Dekodlash: 9 mln kichik kadr (sub-stream) taxminan 500–1000 kadr/s da 2.5–5 soat. Kerak bo'lsa faqat kerakli kadrlar dekodlanadi yoki kamerada fps pasaytiriladi. W4-09 da o'lchanadi.

Saqlash (kuniga, 50 kamera): JPEG 5 fps 0.9–1.8 TB, JPEG 1 fps 180–360 GB, sub-stream video (0.25–0.5 Mbps) 60–110 GB. 3 kunlik bufer taxminan 170–340 GB.

## Hozirgi holat: 1 kamera, 4 hodim, 1 fps (D-11, D-12) — v1.1 hisobi, v1.2 uchun oxirgi bo'limga qarang

Taxminiy hisob, 10 soat/kun yozuv:

| Ko'rsatkich | 1 fps (doimiy) | 5 fps (kalibrovka 2–3 kun) |
|---|---|---|
| Kadr/kun | 36 000 | 180 000 |
| GPU tahlil vaqti (30–60 kadr/s) | 10–20 daqiqa | 50–100 daqiqa |
| Video hajmi (sub-stream, H.265) | taxminan 0.4–1 GB/kun | taxminan 1–2.5 GB/kun |

Xulosa: 00:00–08:00 oynasi bir kamera uchun juda keng zaxira bilan yetadi. Bir kamerada JPEG ham sig'ardi (kuniga 5–10 GB), lekin video bo'laklari ko'p kameraga o'tishda ham ishlaydi, shuning uchun D-12 video bo'laklarini tanlaydi.
Hojatxona va ko'chaga chiqishlar xonada bo'lish vaqtidan ayriladi (D-11): kunlik hisobotda "ishxonada" va "xonada" soatlari alohida ko'rsatiladi.

> v1.4 eslatma (2026-10-27): ikkinchi GPU berilishi mumkin, sanasi noma'lum, rejaga bog'lanmaydi, kelganda D-30 `dedicated` rejimi alohida sinaladi. Barcha hisob bitta RTX 5060 Ti 16 GB uchun; D-30 (timeslice 12/12: kamera oynasi 22:00–10:00) amalda. **v1.7: oyna vaqtlari D-41 bilan almashtirildi (kamera 07:00–22:00, Gemma 22:00–07:00 — tuzatilgan, ustma-ust emas), pastdagi v1.7 bo'limiga qarang.** Ikkinchi GPU (`dedicated`) bu hisobga kiritilmagan. v1.5 (D-37): ko'p kamera hisobi prototipdan keyin alohida ko'riladi.

## v1.2 yangilanishi: 1 kamera, D-20 / D-24 / D-14

Kirish raqamlari (o'lchangan): 102 oqim H.265 640x360, 20 fps, ~35 kbit/s (W4-01); yozuv 07:00–22:00 = 15 soat (D-24);
`coresident` da YOLO-m Gemma bilan birga 48 ms/kadr ≈ 20 kadr/s bitta oqimda, partiyasiz (W0-01). Tahlil oynasi
22:10–00:00 = 110 daqiqa (D-24), tugamagan qismi 00:00–08:00 da davom etadi. *(v1.7: bu oyna D-41 bilan almashtirildi: kamera tahlili kunduzi 07:00–22:00, 10 daqiqalik bo'laklar bo'yicha. Quyidagi jadval kunlik GPU vaqti sifatida o'z kuchida.)*

| Ko'rsatkich | 1 fps | 2 fps | 3 fps | 6 fps |
|---|---|---|---|---|
| Tahlil kadri / kun (15 soat) | 54 000 | 108 000 | 162 000 | 324 000 |
| GPU vaqti, 20 kadr/s (coresident, partiyasiz) | 45 daq | 90 daq | 135 daq | 270 daq |
| GPU vaqti, 60 kadr/s (yolg'iz yoki partiya bilan) | 15 daq | 30 daq | 45 daq | 90 daq |
| 22:10–00:00 ga sig'adimi (20 kadr/s) (v1.7: eskirgan, D-41) | ha | ha | yo'q (00:25 gacha) | yo'q |

- Video hajmi: 35 kbit/s × 15 soat ≈ **0.24 GB/kun** (VBR chegarasi 256 kbit/s da ≈ 1.7 GB/kun).
- Dekodlash: siyraklashtirish uchun H.265 oqim to'liq ochiladi (P-kadrlar oldingisiga bog'liq): 20 × 15 × 3600 = 1 080 000 kichik kadr/kun. 360p da taxminan 1000–3000 kadr/s → 6–18 daqiqa (o'lchanmagan, W4-04/W4-09).
- Xulosa: 1 kamerada 1–2 fps bilan natija 00:00 gacha tayyor bo'ladi. 3 fps va undan yuqorida partiya, odamsiz kadrlarni tashlash yoki 00:00–08:00 davomi kerak. Tanlov W4-04 eshigida.
- 50 kamera (P-13): 20 fps yozuvni to'liq dekodlash 54 mln kadr/kun bo'ladi, bu bir kechaga sig'maydi. D-20 faqat 1 kamera bosqichi uchun; ko'p kamerada kamera fps i yoki boshqa usul W4-09 dan keyin DECISIONS orqali hal qilinadi.

## v1.7 yangilanishi: GPU jadvali D-41 (1 kamera, prototip)

Jadval (D-41; D-30 `timeslice` mexanizmi o'zgarmaydi, faqat vaqtlar):

| Vaqt | GPU egasi | Ish |
|---|---|---|
| 07:00–22:00 | kamera (YOLO) | yopilgan 10 daqiqalik video bo'laklari ketma-ket tahlil qilinadi |
| 22:00–07:00 | Gemma | Telegram kunlik tahlili: 22:10 da o'sha paytgacha kelgan xabarlar, 00:05 da 22:00–24:00 dagi qolgan xabarlar (`daily_close`) |

~~Diqqat: ikki oyna 07:00–10:00 da ustma-ust tushadi. Shu 3 soatda GPU kimga berilishi (D-35 bo'yicha kamera ustuvor yoki boshqa) sozlamaga qo'llash qadamida (W0-07 bilan) foydalanuvchi bilan aniqlanadi (`QUESTIONS.md` `[D-41]`).~~
*Tuzatildi (D-41, 2026-10-27): Gemma oynasi 22:00–07:00, oynalar ustma-ust tushmaydi (jadvalda eski "22:00–10:00" o'rniga 22:00–07:00 yozildi). Telegram kuni = Toshkent kalendar kuni 00:00–24:00 (P-07); kun natijasi 00:05 dagi `daily_close` dan keyin yakuniy.*

Bitta 10 daqiqalik bo'lak (102 oqim, 20 fps → 12 000 kadr dekodlanadi; taxminiy, o'lchanmagan, W4-04 da o'lchanadi):

| Tahlil fps | Tahlil kadri / bo'lak | GPU, 20 kadr/s (coresident) | GPU, 60 kadr/s (yolg'iz) | Dekodlash 1000–3000 kadr/s |
|---|---|---|---|---|
| 1 | 600 | 30 s | 10 s | 4–12 s |
| 2 | 1 200 | 60 s | 20 s | 4–12 s |
| 3 | 1 800 | 90 s | 30 s | 4–12 s |
| 6 | 3 600 | 180 s | 60 s | 4–12 s |

- Xulosa: har qanday tanlangan fps (1–6) da bitta bo'lak 10 daqiqadan ancha tez tahlil qilinadi, ya'ni kamera tahlili yozuvdan ~10–15 daqiqa orqada, kun davomida yetib boradi. Natija kun oxirida (22:00 dan keyin bir necha daqiqada) tayyor, D-05 dagi "kamera hisoboti 09:00 gacha" bemalol bajariladi.
- Yozuv 07:00–22:00 (D-24): birinchi bo'lak 07:10 da yopiladi; oxirgi bo'lak (21:50–22:00) 22:00 da yopiladi, uning tahlili oyna chegarasiga to'g'ri keladi. Uni 22:00 dan keyin tugatish yoki ertasi 07:00 da olish qoidasi W0-07 bilan birga hal qilinadi. *(D-41 tuzatishidan keyin ham ochiq: 22:00 da GPU Gemma ga o'tadi; foydalanuvchi bu bo'lak haqida alohida qaror bermagan. Bitta bo'lak 10–180 s, shuning uchun ertasi 07:00 da olish natijani faqat ertalabgacha kechiktiradi; qoida W0-07 / W4-08 da yoziladi.)*
- Telegram (3 sinov guruhi, 2026-10-14…10-27 da 469 xabar, kuniga ~35–40): 22:10–00:00 = 110 daqiqa bemalol yetadi (W2-03 da o'lchanadi). 00:05 dagi `daily_close` faqat 22:00–24:00 dagi qolgan xabarlarni oladi (bir necha xabar, daqiqalar ichida); Gemma oynasi 07:00 gacha, zaxira yetarli.
- Ko'p kamera (50) hisobi D-39 bo'yicha "productionga o'tamiz" degungacha ko'rilmaydi; yuqoridagi P-13 bo'limi o'zgarmadi.
