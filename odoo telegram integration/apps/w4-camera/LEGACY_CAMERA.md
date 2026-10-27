# LEGACY_CAMERA — eski jonli kamera servisi (faqat o'qib yozildi, 2026-10-26)

Manba: `camera_service\LOYIHA_HOLATI.md`, `camera_service\camsvc\*.py`, `camera_service\data\camera.db` (faqat o'qish rejimida), `D:\Demos\Kamera\dashboard_addon\camera\report.py`.
W4 bu servisni to'xtatmaydi va unga tegmaydi.

## Nima ishlaydi (2026-10-26 holati)

| Jarayon | Kanal | Vazifa |
|---|---|---|
| `camsvc.service` | 101 (1080p, 22 kadr/s) | YOLO26m (GPU, doimiy) + ByteTrack, stol zonalari bo'yicha "o'tirdi/turdi" intervallari `camera.db` ga |
| `camsvc.recorder` | 101 | ffmpeg `-c copy`, 10 daqiqalik fayllar, 07:00–23:00, `E:\Kamera raw data\<yil>\<oy>\Koridor 2\<sana>\` |
| `camsvc.live_relay` | 101 | faqat panelda "Jonli" ochilganda ulanadi, 15 s dan keyin uziladi |

Hammasi qo'lda ishga tushirilgan, server qayta yoqilsa o'zi turmaydi. To'xtatish fayllari: `data\STOP`, `data\STOP_REC`, `data\STOP_LIVE`.
Kamera paroli `camera_service\.env` da (W4 uni o'qimaydi, W4 o'zining `secrets\camera1.txt` ini ishlatadi).

## Nima hisoblaydi

- **Stol (joy):** 3 ta joy, "Joy 1/2/3" (kadrning pastki o'ng qismi, chapdan o'ngga), har biri ko'pburchak zona. Odam ramkasining markazi zonada va ishonch ≥ 0.35 bo'lsa joy band.
- **O'tirdi:** oxirgi 10 s da kadrlarning ≥ 70% ida joy band. **Turdi:** joy `away_grace_s` = 120 s bo'sh (boshida 10 daqiqa edi). 60 s dan ko'p kadr kelmasa "ma'lumot yo'q", u hisobga kirmaydi.
- **Ismlar:** dastlab Nodir / Husniddin / Muhammadumar edi. 2026-10-22 da foydalanuvchi so'rovi bilan olib tashlangan, hozir hodimlar ham "Joy 1/2/3" deb nomlangan. Yuzni tanish yo'q, odam stol orqali aniqlanadi.
- **Ish vaqti:** har hodimga alohida (erkaklar 09:00–19:00, ayollar 09:00–18:00, talab 10 soat, Du–Sha).
- **Namoz:** Toshkent namoz vaqtlari internetsiz hisoblanadi (asr Hanafiy). Namoz vaqtidan keyingi 30 daqiqa ichidagi tanaffus (bittasiga ko'pi bilan 20 daqiqa) ish vaqtiga qo'shiladi.
- **Kompyuterda** = stolda o'tirgan vaqt. **Xonada** = kelgan vaqtdan ketgan vaqtgacha, 30 daqiqa va undan uzun yo'qliklar ayirilgan (`room_exit_min`). Ya'ni xonadan chiqish eshik orqali emas, stolda yo'qlik bo'yicha taxmin qilinadi.
- Panelda hozir faqat **Jonli** ko'rinadi. "Statistika" sahifasi `D:\Demos\Kamera\dashboard_addon` da qurilgan, lekin ishlab turgan panelga o'rnatilmagan.

## W4 uchun farqlar

- W4 eshikdan o'tishni sanaydi (kirdi/chiqdi). Eski servis esa stolda o'tirishni o'lchaydi, eshikni sanamaydi.
- W4 qoidasi (Q14): eshikdan chiqqandan qaytib kirgunicha = xonada emas, davomiyligi qanchaligi farq qilmaydi. Eski servisda faqat ≥ 30 daqiqalik yo'qlik ayiriladi.
- Eski servis kun bo'yi 22 kadr/s da GPU ni band qiladi. W4 rejasida tahlil tunda (00:00–08:00), video 1–5 kadr/s. GPU masalasi W0-06 da.
- Kameraga eski servis 1 doimiy ulanish (service), recorder 1 ta va panelda Jonli ochilganda yana 1 ta ochadi (101). W4 sinovlari 102 ni ishlatadi.
