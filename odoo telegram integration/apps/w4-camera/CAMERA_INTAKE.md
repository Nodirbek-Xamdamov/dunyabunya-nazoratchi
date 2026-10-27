# CAMERA_INTAKE — kamera 1 (W4-01)

Holat: **W4-01 bajarildi** (2026-10-26). O'lchovlar, fps sinovi, 30 daqiqalik sinov (o'tdi), vaqt manbai, Q14 qoidasi bajarildi. Zona `zone_config.json` v5 vaqtincha tasdiqlangan (§6.0a), yakuniy tekshiruv W4-03/W4-05 da. Kechki (bugun 18:30–19:30) va ertalabki (10-27 07:30–09:00) namunalar W4-02 boshlanguncha jadval bo'yicha qo'shiladi; W4-02 yozuvchisi ishga tushgach ular bo'laklardan kesiladi (§6a).
Xom natijalar `intake/out/*.json`, kadrlar `intake/out/samples/`. Ular serverdan chiqmaydi va gitga tushmaydi.

## 1. Kamera

| Maydon | Qiymat | Manba |
|---|---|---|
| Kamera | `koridor_2`, Hikvision **DS-2CD1043G0-I**, proshivka V5.7.0 build 210908 | ISAPI deviceInfo |
| Joyi | eshikka qaragan, eshik va xona ichini ko'radi | foydalanuvchi |
| Manzil | `secrets/camera1.txt` (qiymat hech qayerda yozilmaydi) | foydalanuvchi |
| Kamera soati | **noto'g'ri**: `timeMode manual`, kamera 1970-01-02 ni ko'rsatyapti (server 2026-10-26 12:25). NTP yo'q. Foydalanuvchi qarori: kamera soatiga tegilmaydi | ISAPI time |
| **Yozuv vaqti manbai** | **server soati** (UTC), kamera soati va kadrdagi OSD soat ishlatilmaydi. Server: `w32tm` manba `time.windows.com`, stratum 5, oxirgi sinxronlash 2026-10-26 12:23:46, farq +1…+6 ms (`/stripchart`, 3 o'lchov) | `w32tm /query /status`, 12:37 |
| Bir vaqtdagi oqimlar | hozir 2 ochiq (ikkalasi eski servisniki, 192.168.87.2). Limit ISAPI da berilmagan; uchinchi tomon hujjatida "6 tagacha" (rasmiy tasdiq yo'q) | `sessions.py`, datasheet |

## 2. Oqimlar

| Kanal | Kodek | O'lcham | Sozlangan fps | O'lchangan fps | GOP | Bitreyt | Kim ishlatadi |
|---|---|---|---|---|---|---|---|
| 102 (sub) | H.265 Main | 640x360 | 20 | 20.0 (400 kadr / 19.95 s, oraliq 50 ms, eng uzuni 50 ms) | 50 kadr (2.5 s) | 35 kbit/s (VBR, chegara 256) | hech kim (W4 sinovlari) |
| 101 (asosiy) | H.265 | 1920x1080 | 22 | o'lchanmadi (eski servis band qiladi) | 50 kadr | VBR, chegara 8192 kbit/s | eski servis (tahlil, recorder, jonli ko'rinish) |

Kamera ruxsat bergan qiymatlar:
- 102 o'lchami: 640x360, 640x480, 1280x720. Kodek: H.264, H.265, MJPEG.
- fps (ikkala kanal): 25, 22, 20, 18, 16, 15, 12, 10, 8, 6, 4, 2, 1, 0.5, 0.25, 0.12, 0.06.

## 3. fps sinovi (Q17), 102 kanal, ISAPI orqali, foydalanuvchi ruxsati bilan

| Maqsad | Kamera javobi | O'lchangan fps | Kalit kadrlar | Bitreyt | Vaqt |
|---|---|---|---|---|---|
| **5 fps** | **qo'yib bo'lmaydi**: kamera bu qiymatni taklif qilmaydi | — | — | — | 12:26 |
| 1 fps | OK (`maxFrameRate` 100) | 1.0 (90 kadr / 89 s, oraliq 1000 ms) | har ~4 kadrda | 17.8 kbit/s | 12:27 |
| 6 fps | OK (600) | 6.0 (180 kadr / 29.8 s, oraliq 167 ms) | har 50 kadrda (8.3 s) | 10.4 kbit/s | 12:29 |
| 4 fps | OK (400) | 4.0 (120 kadr / 29.8 s, oraliq 250 ms) | har 16 kadrda | 21.1 kbit/s | 12:29 |
| asliga qaytarildi | OK (2000) | 20.0 | 50 | 34 kbit/s | 12:30 |

- Har o'zgarishdan keyin eski servis loglarida (`service.log`, `recorder.log`) yangi qator yo'q, ya'ni uzilish bo'lmagan. Oxirida kamerada yana faqat 2 ta oqim.
- Asl sozlama `intake/out/isapi_102_original.xml` da saqlangan.
- Natija: kamera 1 fps, 4 fps va 6 fps ni qabul qiladi, 5 fps esa yo'q.
- **D-20 (foydalanuvchi, 2026-10-26, W4-01 chatida):** kamera sozlamalari o'zgartirilmaydi, 102 eski servisniki hisoblanadi, 5/6 fps ga o'tilmaydi. Yozuv 102 ni o'z holida (20 fps) `-c copy` bilan oladi. Tahlil fps (1/2/3/5…) yozuvdan siyraklashtirib olinadi. W4-02 va W4-03 uchun amal qiladi. Yuqoridagi sinov faqat imkoniyatni ko'rsatadi, sozlama asliga qaytarilgan. D-20 hali `DECISIONS.md` da yo'q (W0 qo'shadi).
- W4-02 uchun eslatma: GOP 50 kadr = 20 fps da 2.5 s. 10 daqiqalik `-c copy` bo'laklari kalit kadrda kesiladi, chegara ≤ 2.5 s suriladi. 102 hajmi ~35 kbit/s ≈ 0.38 GB/kun (24 soat).

## 4. 30 daqiqalik barqarorlik sinovi

Foydalanuvchi ruxsati bilan o'tkazildi: 102 da 1 ta qo'shimcha oqim, sinov paytida kamerada jami 3 ta (`sessions.py` → `total 3`). Eski servisning `service.log` va `recorder.log` lari kuzatildi, yangi qator chiqsa sinov darhol to'xtashi kerak edi.

| Kanal | Boshlandi | Davomiyligi | Ulanishlar | Uzilishlar | Kadrlar | O'rtacha fps | Bo'shliq (median / p99 / max) | >1 s bo'shliqlar |
|---|---|---|---|---|---|---|---|---|
| 102 | 2026-10-26 12:41:11 | 30.01 daq | 1 | **0** | 35 980 (kutilgan 36 000 ning 99.94%) | 19.98 | 47 / 204 / 672 ms | 0 |

- Har daqiqada 1195–1204 kadr. 0-daqiqada 1173, chunki ulanish vaqti shunga kiradi. Kam kadrli daqiqa yo'q.
- ffmpeg ogohlantirishi 1 ta: ulanish boshida "Timestamps are unset", RTSP uchun odatiy.
- Eski servis: sinov davomida `service.log`/`recorder.log` da yangi qator yo'q (`stopped_by_guard: null`). Sinovdan keyin kamerada yana 2 ta oqim.
- Natija: **o'tdi**. Fayllar: `out/stability_102_2026-10-26_12-41.json`, `..._per_minute.csv`.

## 5. Yorug'lik namunalari

Toshkent, oktabr oxiri: quyosh ~07:10 da chiqadi, ~17:35 da botadi.

| Holat | Qachon olish kerak | Fayl |
|---|---|---|
| Kunduz | 12:00–14:00 | `samples/2026-10-26/12-30_kunduz_ch102.jpg`, `..._ch101.jpg`: **eskirgan** (kamera oldida to'siq bor edi, keyin olib tashlandi). Yangi kunduzgi kadrlar: `13-30_kunduz_ch102.jpg`, `14-00_kunduz_ch102.jpg`; to'siq qachon olinganini foydalanuvchi aytadi |
| Kechqurun (tashqarida qorong'i, xonada chiroq) | 18:30–19:30 | |
| Ertalab (birinchi kelishlar) | 07:30–09:00 | |
| Orqa yorug'lik (eshik yoki deraza ortida quyosh) | deraza qaysi tomonga qaraganiga bog'liq, shuning uchun kun bo'yi har 30 daqiqada olinadi va eng yomoni tanlanadi | jadval bo'yicha, pastda |

Jadval (`intake/sample_plan.py`):
- 12:42 dagi birinchi jadval foydalanuvchi so'raganidan ko'p edi: ertaga 09:30–20:00 kadrlari va 18:30 da 101 dan qo'shimcha kadr ham bor edi. 14:12 da u to'xtatildi (13:30 va 14:00 kadrlari olingan edi).
- Hozirgi jadval (14:12 dan, PID 18716): faqat 102 dan har 30 daqiqada bitta kadr. Bugun 14:30–20:00 (12 ta, kechki 18:30, 19:00, 19:30 ham shu ichida), ertaga (10-27) 07:30–09:00 (4 ta). 101 dan kadr olinmaydi.
- Fayllar `samples/<sana>/<HH-MM>_<ertalab|kunduz|kech>_ch<N>.jpg`, log `out/sample_plan.log`, to'xtatish `out/STOP_SAMPLES`.
- Bu namunalar W4-01 ni to'xtatib turmaydi: W4-02 boshlanguncha qo'shilib boradi.

## 5a. 101 va 102 da odam o'lchami (faqat kadrlar)

- Ko'rish maydoni bir xil: 101 kadri 640x360 ga kichraytirilib 102 bilan solishtirildi (OSD qatori chiqarib tashlangan). 4 juft kadrda moslik `ncc = 0.917`, siljish `(-0.4, 1.5) px`. Demak har qanday odam 102 da aynan **3 marta kichik** (1080/360).
- 12:30–12:39 dagi 5 juft kadr kamera oldida to'siq bor paytda olingan, shuning uchun odam haqidagi natijasi eskirgan. Masshtab (3x) natijasi esa o'z kuchida. O'sha kadrlarda xonada odam yo'q edi (yolo26m, CPU, `conf ≥ 0.1`: faqat 0.1–0.28 ishonchli soxta ramkalar). Eshik yonidagi haqiqiy odam o'lchami hali o'lchanmagan.
- Taqqoslash uchun eski servisning o'lchovi (2026-10-20, stollar maydoni, yolo26m 640): 101 da recall 0.952, aniqlik 0.949; 102 da recall 0.932, aniqlik 0.885 (`camera_service\LOYIHA_HOLATI.md`, 2-bosqich).
- Keyingisi: zona kelgach, odam bor 102 namunalarida eshik zonasidagi odam balandligini o'lchash (101 dagi o'lcham = 3 × shu). 101 dan yana kadr olish uchun avval ruxsat so'raladi.
- Eslatma: foydalanuvchi 101 dan 1 ta kadr so'ragan edi. Aslida 5 ta olindi (12:30 namunasi + 4 juft), har biri bir necha soniyalik ulanish.

## 6. Eshik zonasi va yo'nalish (Q14)

| Maydon | Qiymat |
|---|---|
| Kamera nimani ko'radi | ichkaridan eshikka qaraydi, eshik va xona ichi ko'rinadi (foydalanuvchi, 2026-10-26) |
| Q14 qoidasi | eshikdan o'tish = kirdi yoki chiqdi. Hojatxona va ko'chaga chiqish ham, agar eshikdan o'tilsa, chiqish hisoblanadi. Eshikdan chiqqandan qaytib kirguncha = xonada emas (foydalanuvchi, 2026-10-26) |
| Xona ichidagi harakat | namoz va boshqa ichki harakatlar hozircha **xonada** hisoblanadi; keyin hal qilinadi (QUESTIONS: `[W4-07]`, open) |
| Zona uchun kadr | `samples/2026-10-26/14-30_kunduz_ch102.jpg` (12-30, 13-30, 14-00 to'siq bor paytda olingan, eskirgan) |

### 6.0 Zona vositasi (jonli kadr bilan, 2026-10-26)

Bu vosita avvalgi "overlay v2" usulini almashtiradi. Foydalanuvchi zonani o'zi belgilaydi va koordinatani chatga yozmaydi.

- **Ishga tushirish:** `apps\w4-camera\intake\run_zone_tool.bat` ni serverda (RDP) ikki marta bosing. Brauzerda `http://127.0.0.1:8765/` o'zi ochiladi. To'xtatish: qora oynada Ctrl+C yoki oynani yopish.
- **Jonli kadr olish:** bir bosish = 102 dan bitta kadr. Kadr `intake/out/live/YYYYMMDD-HHMMSS.jpg` ga saqlanadi va ro'yxatga qo'shiladi. Bosishlar orasida kamida 5 soniya bo'lishi server tomonida tekshiriladi. Eshik yopiq va ochiq turgan kadrlar orasida almashish mumkin, zona o'zgarmay qoladi.
- **Belgilash:** ko'pburchak, kamida 4 nuqta.
  - Bosish — nuqta qo'shiladi (3 tadan keyin eng yaqin chiziq ustiga).
  - Sudrash — nuqta suriladi.
  - Ikki marta bosish yoki o'ng tugma — nuqta o'chiriladi.
  - Delete — tanlangan nuqta o'chiriladi; strelkalar — tanlangan nuqta 1 px suriladi.
  - Koordinatalar 0…1 da jonli ko'rinadi. Chiziq va ichkari nuqtasi so'ralmaydi.
- **Saqlash:** `intake/zone_config.json` ga yoziladi: `version`, `camera`, `channel`, `frame`, `image`, `polygon`, `saved_at`. Oldingi versiya `intake/out/zone_config_history/` da qoladi.
- **Xavfsizlik:**
  - Server faqat 127.0.0.1 da ishlaydi, boshqa Host yoki Origin bilan kelgan so'rov rad etiladi.
  - RTSP manzili loyihaning yuklagichi (`shared/genesis_common.read_secret`) orqali o'qiladi. U sahifaga, javoblarga va logga chiqmaydi. Kadr olishda ffmpeg matni ham uzatilmaydi.
  - Log `intake/out/zone_tool.log` da, unda faqat holat yoziladi.
  - Sahifa tashqi resurs yuklamaydi (CSP `default-src 'self'`). `out/` gitga tushmaydi.
- **Sinov (agent, 2026-10-26 15:49):** 22/22 o'tdi (API va headless Chrome).
  - Kameradan **bitta** kadr olindi (`20261026-154911.jpg`, 2.6 s). Ikkinchi bosish 5 s ichida rad etildi (429).
  - Nuqta qo'shish, surish, o'chirish va saqlash ishladi.
  - Parol log, konfiguratsiya va sahifada yo'q. Eski servis loglari o'zgarmadi.
  - Agent kadrni ochib ko'rmadi.

### 6.0a Amaldagi zona: `zone_config.json` versiya 5 (foydalanuvchi, vosita bilan, 2026-10-26 15:56)

| Maydon | Qiymat |
|---|---|
| Kadr | `intake/out/live/20261026-155455.jpg` (102, 640x360). Foydalanuvchi 15:54 da 2 ta kadr olgan: `20261026-155422.jpg`, `20261026-155455.jpg` |
| Eshik zonasi (polygon) | `[[0.6641,0.5215],[0.736,0.577],[0.6566,0.9117],[0.5904,0.8461]]` (o'ng shisha eshik bargi, qiyshiq to'rtburchak, x 0.590–0.736, y 0.522–0.912) |
| Kirdi/chiqdi chizig'i, ichkari nuqtasi | qo'yilmagan. Hodisa zona qoidasi bo'yicha aniqlanadi (§6.1, W4-05) |
| Fayl | `intake/zone_config.json` (`version 5`, `saved_at 2026-10-26T15:56:01+05:00`); v1–v4 `intake/out/zone_config_history/` da |
| Overlay | `intake/out/zone_overlay_20261026-155455_v2.png` |
| Moslik | ko'z bilan berilgan v2 ga yaqin (x 0.60–0.735, y 0.52–0.86) |
| Eski servis | 15:54 dagi 2 kadr paytida `service.log`/`recorder.log` da yangi qator yo'q |
| Foydalanuvchi tasdig'i | **vaqtincha tasdiqlangan** (foydalanuvchi overlayni ko'rib "to'g'ri" dedi, 2026-10-26). Yakuniy tekshiruv W4-03/W4-05 da haqiqiy yozuvdan olingan yo'l xaritasi bilan |

### 6.1 Foydalanuvchining ko'z bilan bergan zonasi: v2, taxminiy (2026-10-26)

Bu bandni §6.0 dagi vosita almashtiradi: yakuniy zona `zone_config.json` dan olinadi.

Eshik = kadrning **o'ng tomonidagi shisha eshik bargi**. Foydalanuvchi buni eshik yopiq va ochiq turgan ikki kadr bilan tekshirgan. Koordinatalarni foydalanuvchi ko'z bilan baholagan, ular taxminiy.

| Maydon | Qiymat |
|---|---|
| Kadr | `samples/2026-10-26/14-30_kunduz_ch102.jpg` (640x360) |
| Eshik zonasi | `[[0.65,0.52],[0.735,0.52],[0.735,0.86],[0.60,0.86]]` |
| Kirdi/chiqdi chizig'i | **qo'yilmaydi** (pastda sababi) |
| Xona ichi nuqtasi | **qo'yilmaydi** (hozircha) |
| Zona fayli | `intake/out/zone_14-30_kunduz_ch102_v2.json` |
| Overlay | `intake/out/zone_overlay_14-30_kunduz_ch102_v2.png` |
| Holati | §6.0 vositasi bilan almashtiriladi. `zone_config.json` dagi zona ko'rib chiqiladi, "bo'ldi" = vaqtincha tasdiq, yakuniy tekshiruv W4-03/W4-05 da yo'l xaritasi bilan |

**Nega chiziq yo'q.** Ostona kadr pastidagi qora kreslo orqasida yashiringan, shuning uchun oyoqning chiziqni kesishini ko'rib bo'lmaydi.

**W4-05 uchun taklif:**
- Kirdi/chiqdi hodisasi chiziq kesishuvidan emas, trekdan olinadi: odam eshik zonasida qachon paydo bo'lgani, qachon yo'qolgani va qaysi tomonga yurgani bo'yicha.
  - Trek eshik zonasida paydo bo'lib, xona ichiga qarab ketsa = **kirdi**.
  - Trek xona ichidan eshik zonasiga kelib, o'sha yerda yo'qolsa = **chiqdi**.
- Shisha eshik orqali ko'rinadigan koridordagi odamlar xonaga o'tmaguncha hisoblanmaydi: trek eshik zonasidan xona tomonga chiqmasa, bu hodisa emas.
- Yo'nalishni aniqlash qoidasi, "xona ichi" tomoni va kerak bo'lsa chiziq yakuniy ravishda W4-05 da hal qilinadi. Buning uchun haqiqiy yozuvdan (W4-03) odamlar yo'lining xaritasi olinadi.

### 6.2 Rad etilgan: agent taklifi v1 (2026-10-26 14:40)

**Rad etilgan** (foydalanuvchi). Zona eshikka emas, tik turgan metall taxta va chap shisha oynaga tushgan. Kesishish chizig'i va ichkari nuqtasi kadr pastidagi qora kreslo ustiga tushgan. Fayllar (o'chirilmagan, faqat tarix uchun): `intake/out/zone_14-30_kunduz_ch102.json`, `zone_overlay_14-30_kunduz_ch102.png`, `zone_proposal_14-30_kunduz_ch102.json`.
Saboq: lokal Gemma 4 (CPU) eshikni ishonch bilan noto'g'ri topdi, qirralarga moslash ham noto'g'ri predmetni "tasdiqladi". Zona uchun bunday avtomatik taklif ishonchli emas.

Quyidagi v1 tafsilotlari tarix uchun qoldirildi.

Zonani agent taklif qildi (foydalanuvchi koordinata yozmaydi). Faqat lokal vositalar ishlatildi: Gemma 4 12B (Ollama, **CPU**, `num_gpu 0`, javobdan keyin xotiradan chiqarildi) + OpenCV. Kadr serverdan chiqmadi, chet el API ga yuborilmadi, agent kadrni o'zi ko'rmadi. Skript `intake/propose_zone.py`, xom javoblar `intake/out/zone_proposal_14-30_kunduz_ch102.json`.

**Kadr tanlovi.** `14-30_kunduz_ch102.jpg` (2026-10-26 14:30, jadval bo'yicha).
- 12:30, 13:30 va 14:00 kadrlari bir-biriga deyarli teng (o'rtacha farq 2.8–3.1). Demak to'siq ularda ham bor edi va ular **eskirgan**.
- 14:30 kadri ulardan katta farq qiladi: o'rtacha farq 35, eski qirralarning 42% i yo'qolgan. Shu bilan birga umumiy siljish 0 px va yorug'lik bir xil (113 va 112). Bu to'siq olingan, kamera esa surilmagan degani.

**v1 taklifi (rad etilgan; 0…1, 640x360 kadr):**

| Maydon | Qiymat | Qanday topildi | Ishonch |
|---|---|---|---|
| Eshik | x 0.452–0.600, y 0.288–0.868; shisha, ochiq, oldida to'siq yo'q | Gemma: D–E kataklar + ramka (ikkalasining IoU 0.49). Yon chetlar eng kuchli vertikal chiziqqa moslandi (o'rtachadan 3.2x va 2.9x) | o'rta |
| Ostona | y = 0.867 | Gemma 0.868. Eshik kengligida gorizontal chiziq o'rtachadan 4.5x kuchli | yuqori |
| Eshik zonasi | `[[0.4516,0.288],[0.6,0.288],[0.63,0.988],[0.4216,0.988]]` | eshik + uning oldidagi pol (ostonadan 0.12 pastga) | o'rta |
| Kirdi/chiqdi chizig'i | `[[0.4316,0.928],[0.62,0.928]]` | ostonadan biroz xona ichkarisida, eshikdan 2% kengroq | o'rta |
| Xona ichi tomoni | `[0.5258,0.98]` (chiziqdan **pastda**, ya'ni kameraga tomon) | Q14: kamera ichkarida turib eshikka qaraydi | yuqori (Q14 bo'yicha) |
| Overlay | `intake/out/zone_overlay_14-30_kunduz_ch102.png` | `draw_zone.py` | — |

**v1 ishonchsiz joylari (tarix):**
1. Gemma ikkinchi savolda `room_side: right` deb javob berdi. Bu Q14 ga zid, shuning uchun hisobga olinmadi: ichkari tomoni Q14 bo'yicha chiziqdan pastda.
2. Gemma F4–F6 kataklarida (x 0.625–0.75, pastki yarmi) "boshqa ochiq joy" borligini aytdi. U ikkinchi eshik yoki shisha devor bo'lishi mumkin. Unda ham o'tish bo'lsa, zona yetmaydi.
3. Eshik shisha va ochiq. Koridordagi odamlar shisha ortida ko'rinadi (eski servis ham aks va narigi xonani qayd qilgan). Ular ostonadan pastga tushmaguncha kirdi deb hisoblanmasligi kerak (W4-05).
4. Eshikning tepa cheti (y 0.288) noaniq: chiziq kuchsiz (1.2x). Lekin kirdi/chiqdi oyoq nuqtasi chiziqni kesganda hisoblanadi, shuning uchun tepa chet bunga ta'sir qilmaydi.
5. Ostona va kadrning past cheti orasi tor: ~47 px (0.867–1.0). Kameraga yaqin odamning oyog'i kadrdan chiqib qolishi mumkin. Chiziq tanlovini W4-05 tekshiradi.
6. Eski servisning Joy 1–3 zonalari (x 0.665–0.995, y 0.70–0.97) to'siq bor paytda chizilgan. Ko'rinish o'zgargani uchun ular endi joyiga mos kelmasligi mumkin. Bu W4 ishi emas, foydalanuvchi bilsin uchun yozildi.


## 6a. W4-02 uchun kelishilgan narsalar (reja)

- Yozuv soatlari: **07:00–22:00**, sozlamada (`cameras` / config), kodda emas (foydalanuvchi, 2026-10-26).
- Yozuv: 102 o'z holida, `-c copy`, vaqt server soatidan (D-20, §1). Joy: `E:\data` (D-18).
- **Bajarildi (W4-02, 2026-10-26 16:19):** namunalar jadvali to'xtatildi. Keyingi kadrlar bo'laklardan kesiladi: `python -m w4cam.frames --at "YYYY-MM-DD HH:MM"` (§8). Avvalgi reja:
- **Namunalar yozuvchi ishga tushgach kameraga ulanmaydi.** W4-02 yozuvchisi ishlay boshlaganda `sample_plan.py` to'xtatiladi (`out/STOP_SAMPLES`). Namuna kadrlar yozilgan 10 daqiqalik bo'laklardan ffmpeg bilan kesiladi (`-ss <ofset> -i <bo'lak> -frames:v 1`, server soati bo'yicha kerakli daqiqa). Shunday qilib kameraga W4 dan faqat bitta doimiy oqim (yozuvchi) qoladi.

## 7. Skriptlar (`intake/`, faqat Python stdlib + `C:\ffmpeg` 9.0.1)

| Fayl | Nima qiladi | Kamerani o'zgartiradimi |
|---|---|---|
| `sessions.py` | model, ochiq oqimlar soni, limitlar. Faqat ISAPI, video oqim ochmaydi | yo'q |
| `probe.py` | kodek, o'lcham, fps, GOP, bitreyt (102), ISAPI sozlamalari (102, 101) | yo'q |
| `stability.py --minutes 30 --guard-logs …` | uzluksiz oqim, uzilishlar, bo'shliqlar. Eski servis logida yangi qator chiqsa darhol to'xtaydi | yo'q |
| `samples.py` | kadr(lar) `out/samples/` ga | yo'q |
| `set_fps.py --fps N --yes` / `--restore --yes` | 102 fps ni o'zgartiradi va o'lchaydi | **ha** |
| `run_zone_tool.bat` → `zone_server.py` + `zone_tool.html` | eshik zonasi vositasi (§6.0): jonli kadr (bir bosish = 1 kadr, ≥ 5 s), ko'pburchak, `zone_config.json` | yo'q |
| `compare_scale.py` | 101+102 dan bir vaqtda kadr, odam o'lchami (CPU, GPU ga tegmaydi) | yo'q |
| `sample_plan.py` | jadval bo'yicha namuna kadrlar (W4-02 gacha) | yo'q |
| `draw_zone.py` | zona/chiziq/ichkari tomonini kadrga chizadi (overlay) | yo'q |
| `propose_zone.py` | zona taklifi: lokal Gemma 4 (CPU) + OpenCV qirralari | yo'q |

- Manzil `secrets/camera1.txt` dan (yoki `CAM1_RTSP` o'zgaruvchisidan) o'qiladi, chiqishda `rtsp://***` bo'ladi.
- ISAPI parolni faqat bir marta yuboradi, rad etilsa qayta urinmaydi (Hikvision bloklashi mumkin). Soxta server bilan sinab ko'rildi: 2 so'rov, ikkinchisida xato.
- Fayllarda parol yo'qligi `grep` bilan tekshirildi (`apps/`, `.genesis/`).

Manbalar: [DS-2CD1043G0-I datasheet (Hikvision)](https://assets.hikvision.com/prd/normal/all/doc/sm000079752/pt-br/DS-2CD1043G0-I-C_Datasheet_20250617.pdf), [uchinchi tomon spetsifikatsiyasi ("6 tagacha")](https://apifiles.telesystem.ro/tuploads/4743/Specificatii%20tehnice%20DS-2CD1043G0-I-2.8mm.pdf).

## 8. W4-02 yozuv xizmati (2026-10-26, holat: doing)

**Nima yozadi.** 102 oqim o'z holida (H.265 640x360 20 fps, `-c copy`, dekodlashsiz, GPU yo'q, D-20). 10 daqiqalik bo'laklar soat bo'yicha kesiladi (:00, :10 …, kalit kadr sabab ≤ 2.5 s kech).
- **Fayllar:** `E:\data\video\camera1\<mahalliy sana>\<UTC>Z.mkv`. Har kunga `manifest.jsonl` (format: `w4cam/manifest.py`; `segment` va `gap` qatorlari; W0-04 `video_segments` ga import qiladi).
- **Vaqt:** server soatidan olinadi (NTP), ffmpeg `TZ=UTC0` bilan ishlaydi. Bitta sessiya ichida bo'lak vaqti media soatidan ±1 s aniqlikda.
- **Oyna:** `07:00-22:00`, `apps/w4-camera/app.toml` `[recorder]` da.
  - Oyna chegarasida bo'lak `q` bilan to'g'ri yopiladi, oyna tashqarisida yozilmaydi.
  - Test uchun vaqtinchalik oyna `var\w4-camera\window_override.json` bilan beriladi, muddati tugagach o'zi bekor bo'ladi. `app.toml` tahrirlanmaydi.
- **Uzilish:**
  - ffmpeg chiqsa yoki bo'lak `stall_seconds` = 60 s o'smasa, ffmpeg o'ldiriladi va qayta ulanadi (1, 2, 4 … 60 s). Manifestga `gap` qatori va keyingi bo'lakka `break_before` yoziladi.
  - Fayl har paketda yoziladi (`flush_packets=1`, 1 s klaster), shuning uchun o'ldirilganda ≤ 1 s yo'qoladi.
  - Recorder o'lsa, ffmpeg ham o'ladi (Windows job object), yetim oqim qolmaydi.
- **Yurak urishi:** `var\w4-camera\recorder_heartbeat.json` (10 s). Log: `var\logs\w4-recorder\w4-recorder.jsonl` (parol `***`).
- **Disk va bufer:**
  - E: da 20 GB dan kam joy qolsa logda ogohlantirish chiqadi, 2 GB dan kam qolsa yozuv to'xtaydi.
  - Aylanma bufer 14 kun: bugun va oldingi 13 kun saqlanadi, eskisi har soatda o'chiriladi.
  - Haqiqat to'plami qirqimlari uchun `E:\data\video_keep\`. Bufer unga tegmaydi.

**Ishga tushirish (vaqtincha, W0-07 da NSSM servisiga o'tadi).** Task Scheduler vazifasi `genesis_w4_recorder` (`scripts\register_recorder_task.ps1`):
- **Hisob va rejim:** `AI-SERVER\Администратор`, S4U ("foydalanuvchi kirgan yoki kirmaganidan qat'i nazar", parol saqlanmaydi), Limited. Jarayon sessiya 0 da ishlaydi, RDP ga bog'liq emas.
- **Triggerlar:** tizim yoqilganda; har kuni 06:55 dan boshlab har 1 daqiqada (24 soat davomida takrorlanadi). `MultipleInstances IgnoreNew`, lock fayl.
- **Asl reja va o'zgarish:** asl rejada "xatoda qayta ishga tushirish" bor edi. O'lchov shuni ko'rsatdi: majburan o'ldirilgan jarayonni Task Scheduler qayta yoqmaydi (4 daqiqa kutildi). Shuning uchun daqiqalik takror qo'shildi.
- **To'xtatish:** butunlay to'xtatish uchun `Disable-ScheduledTask genesis_w4_recorder`, keyin `var\w4-camera\STOP_RECORDER` fayli. Vazifa yoqiq bo'lsa, STOP fayli faqat qayta ishga tushiradi.

**Sinovlar (2026-10-26):**

| Sinov | Natija |
|---|---|
| Unit va sun'iy ffmpeg testlari (`python -m unittest tests.test_recorder`) | 11/11 o'tdi |
| Haqiqiy kamera, tezlashtirilgan oyna (16:22:13–16:23:13, `E:\data\tmp`) | yozuv 16:22:14 da boshlandi, 16:23:13 da to'xtadi, bo'lak list orqali yopildi |
| 2 soat (16:23:51–18:23:51) | 14 bo'lak = kutilgan: 13 kesish + 1 o'ldirish + 1 kamera uzilishi − 18:00 kesishi uzilish ichida. 6914 s yozuv + 286.9 s belgilangan bo'shliq = 7200.9 s. Izohsiz yo'qotish yo'q |
| ffmpeg ni o'ldirish (16:33:04) | 1 s da qayta ulandi; `gap` 4.27 s `ffmpeg_exit`, keyingi bo'lakda `break_before` |
| Kamera uzilishi (17:59:34–18:04:17, bizdan emas: eski servis ham uzildi, kamera qayta yuklanmagan) | `gap` 282.65 s belgilandi, o'zi tiklandi |
| To'g'ri qayta yoqish (20:37:50) | to'xtash 1.3 s; oldingi bo'lak ffprobe 469.5 s = manifest, to'liq dekodlash 0 xato; yangi bo'lak 20:37:54 da (~4 s); `gap` 2.5 s `recorder_restart` |
| Majburan o'ldirish 1 (20:40:19) | **qayta yoqilmadi** (4 daqiqa); ffmpeg birga o'ldi. Tuzatildi: daqiqalik takror |
| Majburan o'ldirish 2 (20:45:19) | 41.5 s da yangi recorder, 44 s da yangi bo'lak; o'ldirilgan bo'lak dekodlanadi (0 xato), `recovered` deb yozildi. Topildi: 1-o'ldirish bo'shlig'i (15:40:18–15:44:56Z, 278 s) yozilmay qolgan. Tuzatildi: > 3 s har qanday teshik `gap` bo'ladi; bugungi qator `"late": true` bilan qo'shildi |
| Eski servis loglari 16:24:51–18:25 | faqat 17:59–18:04 kamera uzilishi (hamma mijozlarda bir xil); W4 sabab bo'lgan qator yo'q |
| RDP | vazifa S4U, jarayonlar sessiya 0 da. Haqiqiy Disconnect sinovi foydalanuvchida |
| Soat oynasi (haqiqiy servis) | 22:00 dan keyin `scripts\window_test.py`: natija `var\w4-camera\window_test_2026-10-26.txt` |

**Topilgan va tuzatilgan xatolar:**
1. Fayl 0 baytda ~7 s turardi (matroska klasteri + 32 KB bufer). Natijada o'ldirilganda ma'lumot yo'qolardi, osilishni tekshirish esa sog' ffmpeg ni ham o'ldirardi. Tuzatish: `flush_packets=1:cluster_time_limit=1000`.
2. O'ldirilgan bo'lak fayl nomidan (butun soniya) boshlanardi, natijada 0.45–0.95 s ustma-ust tushardi. Tuzatish: oldingi bo'lak tugagan media vaqtidan davom etadi.
3. Majburan o'ldirilgan xizmat qayta yoqilmasdi. Tuzatish: har daqiqalik trigger.
4. E'lon qilinmagan bo'shliq (xotirada qolgan) yo'qolardi. Tuzatish: > 3 s teshik doim `gap`.

3-tuzatish 20:44 dan beri ishlaydi. 2 va 4-tuzatishlar xizmatga 22:01 dagi qayta yoqishda yuklanadi (`window_test.py`, yozuv yo'q paytda).

**Bitreyt va joy:** bugun 39.8 kbit/s, ya'ni 15 soatda 0.27 GB/kun, 14 kunlik bufer ~3.8 GB. E: da 1857 GB bo'sh (2026-10-26 20:47).

**Ertaga 07:00 dan keyin tekshirish:**
```powershell
Get-Content 'D:\Demos\odoo telegram integration\var\w4-camera\recorder_heartbeat.json'
Get-ChildItem 'E:\data\video\camera1\2026-10-27\*.mkv' | Sort-Object Name | Select-Object -First 3 Name, Length, LastWriteTime
Get-Content 'E:\data\video\camera1\2026-10-27\manifest.jsonl' -TotalCount 2
Get-Content 'D:\Demos\odoo telegram integration\var\w4-camera\window_test_2026-10-26.txt'
```
Kutilgan natija:
- `state` = `recording`;
- birinchi fayl `20261027T0200xxZ.mkv` (07:00 + ulanish ~3 s);
- 07:10 dan keyin manifestda birinchi `segment` qatori;
- oyna sinovida `RESULT OK`.
