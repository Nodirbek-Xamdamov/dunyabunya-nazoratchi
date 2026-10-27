# Kamera loyihasi — holat

Vazifa: bitta kamera orqali har bir hodimning stol oldida o'tirgan vaqti (keldi, ketdi, necha marta turdi).
Natija mavjud CEO panelidagi yangi "Yangi kamera" bo'limida ko'rinadi.
Suhbat uzilsa, ish shu fayldan davom ettiriladi.

**2026-10-22 18:03 dan loyiha shu yerda: `D:\Demos\odoo telegram integration\camera_service`.**
Pastdagi tarixiy qatorlardagi `D:\Demos\Kamera\camera_service` yo'llari endi shu papkani bildiradi.
Kamera paroli `camera_service\.env` da (botning `.env` iga qo'shilmagan). Eski `D:\Demos\Kamera` arxiv (test videolar, review, git tarixi).

Oxirgi yangilanish: 2026-10-22 18:10 (Toshkent)

## Bosqichlar

| # | Bosqich | Holat |
|---|---------|-------|
| 0 | Tayyorgarlik | tugadi. ★ javoblar olindi (pastda). Test video 17:03–18:03 yozildi (101: 625 MB, 102: 19.8 MB) |
| 1 | Muhit va oqim | tugadi (2026-10-20 18:10), natijalar pastda |
| 2 | Odamni aniqlash va kuzatish | tugadi. ★ belgilangan videoga javob hali yo'q (foydalanuvchi "dashboardga chiqar" dedi) |
| 3 | Baza, stol zonalari, "Yangi kamera" skeleti | qurildi va sinaldi, ishlab turgan panelga HALI O'RNATILMAGAN (2026-10-21: "bugun ishlatma"). ★ zonalarni foydalanuvchi chizadi |
| 4 | Vaqt hisoblash mantig'i | modul va testlar tayyor. Test video taxminiy zonalar bilan quvurdan o'tdi. Tekshiruv varag'i haqiqiy zonalardan keyin |
| 5 | "Yangi kamera" bo'limi to'liq | 4 sahifa qurilgan. 2026-10-22 dan panelda faqat **Jonli** ko'rinadi (`views\kamera.py` `SHOWN`) |
| 6 | Doimiy ishlash | xizmat 2026-10-22 15:37 qo'lda ishga tushirildi. Avtomatik qayta ishga tushish, zaxira hali yo'q |
| 7 | Bir kunlik haqiqiy test | boshlanmagan |

## Foydalanuvchi javoblari (2026-10-20 ~17:01)

- Versiyalar: "ishlaydigan versiyani ishlataver" → torch 2.14.1+cu132.
- "Yangi kamera" CEO paneliga qo'shiladi. Bu prototip, shuning uchun panelga parol qo'yilmaydi: LAN orqali ochiqligi foydalanuvchi qarori.
- Kamera ma'lumotlari alohida saqlanadi (tasdiqlandi). "4 TB xotiraga saqlasang ham bo'ladi" deyildi. Serverda 4 TB disk yo'q.
  Eng katta bo'sh disk E: (2 TB HDD, bo'sh). Asosiy baza D: (NVMe SSD) da qoladi, kundalik zaxiralar E: ga yoziladi (6-bosqich, boshqa fizik disk).
- Ish vaqti har hodimga alohida: ayollar 09:00–18:00, erkaklar 09:00–19:00, ba'zilariniki farq qiladi. Ishda 10 soat bo'lishi kerak.
  Dam olish kunlari aytilmadi, standart olinadi: Dushanba–Shanba ish, Yakshanba dam.
- Stollar: eng uzoqdagi = **Nodir**, o'rtadagi = **Husniddin**, kameraga eng yaqini = **Muhammadumar**. Uchalasi erkak, ya'ni 09:00–19:00.
- "10 minut": "turdi" chegarasi `away_grace` = 10 daqiqa deb tushunildi. Hisobotda har soat va butun kun bo'yicha jami o'tirgan vaqt bo'ladi.
- Namoz vaqti ish vaqtiga qo'shiladi. Reja (4-bosqich): Toshkent namoz vaqtlari lokal hisoblanadi (internetsiz, Hanafiy asr).
  Shu oynalarda stoldan yo'q bo'lgan vaqt "namoz" deb belgilanadi va jami ish vaqtiga qo'shiladi. Bitta namoz uchun cheklov sozlanadi.
  Hisoblangan vaqtlar "Sozlash" sahifasida ko'rinadi, rasmiy taqvim bilan solishtirish va surish (offset) mumkin.

## 2026-10-21: "telegram odoo integration proektidagi dashboardga chiqar lekin bugun ishlatma"

Tushunilgan ma'no: bo'lim aynan shu panelga (`odoo telegram integration\dashboard`) qo'shiladi, lekin bugun hech narsa ishga tushirilmaydi.
- Ishlab turgan panel to'xtatilmadi va uning fayllari o'zgartirilmadi.
- Kamera xizmati jonli ishga tushirilmadi.
- O'rnatish ertaga (yoki ruxsat berilgan kuni) `dashboard_addon\deploy_live.ps1` bilan qilinadi.

Nima qurildi:
- `dashboard_addon\` (bu repoda): `camera\` (store, report, prayer, zone_editor, excel, ui, texts), `views\kamera*.py` (4 sahifa), `tests\test_camera.py` (15 test).
  `install.py` fayllarni panelga ko'chiradi va `app.py` ga 4 qator qo'shadi (aniq joylarni topib; topilmasa hech narsa o'zgarmaydi; `--check`, `--uninstall` bor).
  `deploy_live.ps1` ketma-ketligi: tekshiradi → zip zaxira oladi → panelni to'xtatadi → o'rnatadi → yoqadi → health tekshiradi.
  DIQQAT: `run_dashboard.ps1 -Stop` agentlarning Telegram botlarini ham to'xtatadi, ~15 s.
- Sahifalar panelning layout/AI-chat tizimidan tashqarida: chat ularni yashira yoki o'zgartira olmaydi. Matnlar `camera\texts.py` da (panelning `i18n.py` siga tegilmadi).
- Panelning Python muhitiga hech narsa o'rnatilmadi. Zona chizish faqat mavjud Streamlit 1.66 + Plotly bilan ishlaydi:
  burchaklarni bosish, yoki asboblar panelidagi Lasso bilan aylana chizish.
  (Streamlit `dragmode=lasso` bo'lsa bosishni tanlovga aylantirmaydi, shuning uchun standart rejim pan, o'qlar qotirilgan.)
- Kamera bazasi `camera_service\data\camera.db` yaratildi: 3 stol (Uzoq – Nodir, O'rta – Husniddin, Yaqin – Muhammadumar, 09:00–19:00, 10 soat, Du–Sha), zonalar bo'sh.
  `data\live\latest_raw.jpg` da test videodan (17:05:14) kadr bor. Xizmat ishga tushgach u jonli kadr bilan almashadi.
- Hisobot qoidalari (`camera\report.py`):
  - O'tirgan = intervallar birlashmasi.
  - Tanaffus = intervallar orasi minus "ma'lumot yo'q" vaqti (kamera uzilishi tanaffus emas).
  - Namoz = [namoz vaqti, +30 daq] oynasiga tushgan tanaffus qismi, bitta namozga ko'pi bilan 20 daq, ish vaqtiga qo'shiladi.
  - Ma'lumot yo'q = ish vaqtidagi kadrlarning yarmidan kami tahlil qilingan daqiqalar.
  - "Kunlar" sahifasida tizim umuman ishlamagan kunlar hisobga olinmaydi.
- Namoz vaqtlari Toshkent uchun astronomik formula bilan hisoblanadi: asr Hanafiy, bomdod va xufton 15°.
  Rasmiy taqvimdan bir necha daqiqa farq qilishi mumkin, "Sozlash"da surish (offset) bor.
- Kamera xizmatiga qo'shilganlar:
  - `CAMERA_DB_PATH` / `CAMERA_LIVE_DIR` env bilan boshqa bazaga yozish mumkin;
  - `latest.json` (kadr vaqti);
  - ROI tepaga kamida kadrning 15% i kengaytiriladi;
  - ROI yuqori chetida kesilgan ramka (tik turgan odam) stol uchun hisobga olinmaydi.

Sinovlar:
- Panel ishchi nusxasi (`dev\dashboard`, gitignore): to'liq to'plam 219 o'tdi, 4 o'tkazildi (eski 208 + 15 yangi).
- Kamera xizmati: 17 test.
- Brauzer sinovi (headless Chrome, CDP, `dev\ui_check.py`): 4 sahifa xatosiz. 3 marta bosish 3 nuqta berdi, Lasso 10 burchakli zona berdi.
  Skrinshotlar: `review\ui_*.png`.
- Test video (60 daq) taxminiy zonalar va `anchor=upper` bilan `dev\camera_test.db` ga o'tkazildi: 17 989 kadr, 318 s (11.3x).
  Nodir 17:03–18:03, Husniddin 17:03–18:03, Muhammadumar 17:03–17:06:44 va 17:26:19–17:56:35. Ko'z bilan ko'rilgan kadrlarga mos.
- Ishchi nusxa 127.0.0.1:8510 da faqat sinov paytida ishladi va to'xtatildi. Parol skaneri: 171 fayl, 0 topildi.

## Server tekshiruvi (2026-10-20 16:44)

- GPU: RTX 5060 Ti 16 GB, compute capability 12.0, drayver 616.56 (CUDA 13.4 gacha). Band 532 MiB (faqat ekran), bo'sh 15 521 MiB.
  GPU'da hisoblash jarayoni yo'q. STT ishchisi (`Speach to text rubaiSTT`, Celery) ishlamayapti. Ollama modellari faqat kerak bo'lganda yuklanadi (gemma4:12b ~9.1 GB).
  Bo'sh VRAM ≥ 6 GB, shuning uchun hech narsa to'xtatilmadi, `STT_QAYTA_YOQISH.md` kerak emas.
- Python: faqat 3.12.7 (`C:\Program Files\Python312`).
- ffmpeg 9.0.1 (`C:\ffmpeg`). RTSP parametrlari: `-rtsp_transport tcp`, `-timeout` mikrosekundda. VLC: `C:\Program Files\VideoLAN\VLC`.
- Disk: D: 237.9 GB bo'sh, C: 99.4 GB.
- Soat: zona West Asia Standard Time (UTC+05:00, Toshkent), NTP (time.windows.com) bilan sinxron, oxirgi sinxronlash 16:31.
- Internet: pypi.org, download.pytorch.org (cu132), github.com, docs.ultralytics.com — ochiq.

## Rasmiy hujjatdan tekshirilgan versiyalar (2026-10-20)

- PyTorch barqaror versiyasi: 2.14.1. pytorch.org'dagi Windows buyrug'i:
  `pip3 install torch torchvision --index-url https://download.pytorch.org/whl/cu132`.
  Barqaror variantlar faqat cu126 va cu132. cu126 bu GPU'ni (sm_120) qo'llamaydi, shuning uchun cu132 tanlandi.
  `torch-2.14.1+cu132-cp312-cp312-win_amd64.whl` va `torchvision-0.29.1+cu132` mavjud.
- NVIDIA: CUDA 13.x uchun drayver ≥ 580 kerak (CUDA Toolkit release notes). Bizda 616.56: drayver yangilanmaydi, CUDA Toolkit o'rnatilmaydi.
- Ultralytics: PyPI'dagi oxirgi versiya 8.4.183 (2026-10-20). YOLO26 vaznlari: `yolo26n.pt`, `yolo26s.pt`, `yolo26m.pt`, `yolo26l.pt`, `yolo26x.pt`. Pose: `yolo26n-pose.pt` … `yolo26x-pose.pt`.
- Tracker: default `tracktrack.yaml`. Biz aniq `tracker="bytetrack.yaml"` beramiz. `persist=True` faqat bitta oqimning ketma-ket kadrlari uchun.
- Ultralytics default holatda anonim statistika yuboradi. O'rnatgandan keyin, birinchi ishga tushirishdan oldin `settings.update({"sync": False})` qilinadi (5-qoida).

## 1-bosqich natijalari (2026-10-20)

- Muhit: `camera_service\.venv` (Python 3.12.7). torch 2.14.1+cu132, torchvision 0.29.1+cu132, ultralytics 8.4.183, opencv-python 5.0.0.93 (FFmpeg 7.1), lap 0.5.13, tzdata.
  To'liq ro'yxat `camera_service\requirements.txt` da, torch alohida buyruq bilan o'rnatiladi (fayl boshida yozilgan).
- GPU sinovi (`tools\gpu_check.py --yolo models\yolo26m.pt`): CUDA 13.2, capability (12, 0), sm_120 bor, matmul 15 TFLOPS.
  yolo26m bus.jpg da: 4 odam, 10.1 ms (FP16), 14.0 ms (FP32), qurilma cuda:0.
- Vaznlar `camera_service\models\` da: yolo26n/s/m/l/x.pt, GitHub v8.4.0, SHA256 tekshirilgan.
  GitHub va PyPI bitta ulanishda ~55 KB/s. `tools\fetch_parallel.py` 8–16 ulanish bilan ~0.85 MB/s.
- 10 daqiqalik jonli sinov (101, yolo26m, 5 kadr/s): uzilish 0, ulanish 1.2 s.
  Kamera o'rtacha 21.0 kadr/s (nominal 22), ishlov 4.7 kadr/s. Kadr yoshi: o'rtacha 40 ms, p95 140 ms, max 204 ms.
  CPU butun mashinaning 1.3% i (28 mantiqiy yadro, 1 yadroning ~37%). GPU o'rtacha 10% (boshqa jarayonlar bilan).
  torch VRAM 124 MB, GPU'da jami band 1.1 GB (shundan 0.53 GB ekranniki). Model 23.4 ms (p95 33.6).
  Yetishmagan kadr ~4.6%, deyarli hammasi 17:56:34–17:57:34 dagi bir daqiqada. Yozuv faylida ham shu daqiqada 2–3 s tanaffuslar bor,
  ya'ni sabab kamera yoki tarmoq tomonida (o'sha payt kamerada 4 ulanish edi: VLC, 2 ta yozish, sinov).
- 101 va 102 CPU bo'yicha (modelsiz, 2 daq): 101 1.1% (1 yadroning ~31%), 102 0.1%. Ikkalasi ham arzon, oqim aniqlik bo'yicha tanlanadi.
  Yangi kadrni kutish qo'shilgach ishlov 4.8–4.9 kadr/s.
- Qayta ulanish sinovi: noto'g'ri port 30 s (urinishlar 1, 2, 4, 8 s), to'g'ri manzildan keyin kadr 6.3 s da keldi.
  Uzilish va 15 s noto'g'ri manzildan keyin kadr 3.3 s da, oddiy uzilishda 2.4 s da keldi.
- Kechikish: kameraning o'z soati serverdan 1.0 s orqada (ISAPI orqali faqat o'qildi, `timeMode manual`, ya'ni NTP yo'q).
  Kadrdagi soat server vaqtidan 1.77 s orqada, demak video kechikishi ~0.8 s. Ulanishning birinchi soniyalarida 3–4 s gacha.
- Test kliplar: boshlanish vaqti `start_utc` = birinchi kadrdagi kamera soati + 1.0 s (±0.5 s).
  `first_bytes_utc` past bitreytli 102 oqimida 61 s kech yozilgan, ishlatilmaydi.

## 2-bosqich natijalari (2026-10-20)

Taqqoslash (`tools\compare_models.py`): 300 kadr, 17:08–17:28, har 4 s da bitta. Etalon `yolo26x`, 1280 o'lcham, conf ≥ 0.5.
Faqat stollar maydonidagi odamlar hisoblanadi, variantlarda conf ≥ 0.35. ms = bitta kadr (oldin va keyingi ishlov bilan), FP16.

| variant | ms | VRAM MB | recall | aniqlik | soni mos | odam/kadr |
|---|---|---|---|---|---|---|
| n 640 | 12.4 | 66 | 0.789 | 0.858 | 0.60 | 2.12 |
| s 640 | 11.3 | 74 | 0.941 | 0.926 | 0.77 | 2.34 |
| m 640 | 13.5 | 124 | 0.952 | 0.949 | 0.85 | 2.31 |
| l 640 | 17.7 | 152 | 0.961 | 0.961 | 0.90 | 2.30 |
| s 960 | 13.7 | 94 | 0.962 | 0.930 | 0.82 | 2.38 |
| m 960 | 13.7 | 244 | 0.981 | 0.963 | 0.89 | 2.35 |
| s 640, 102-oqim | 12.2 | 74 | 0.915 | 0.814 | 0.64 | 2.59 |
| m 640, 102-oqim | 11.9 | 124 | 0.932 | 0.885 | 0.78 | 2.43 |
| n 640, stollar atrofi | 12.1 | 66 | 0.944 | 0.873 | 0.72 | 2.49 |
| s 640, stollar atrofi | 12.1 | 74 | 0.974 | 0.902 | 0.77 | 2.49 |
| **m 640, stollar atrofi** | 13.5 | 124 | **0.984** | 0.926 | 0.82 | 2.45 |
| s 960, stollar atrofi | 13.5 | 94 | 0.948 | 0.912 | 0.78 | 2.39 |
| m 960, stollar atrofi | 15.3 | 244 | 0.973 | 0.911 | 0.81 | 2.46 |

- Tanlov: `yolo26m.pt`, imgsz 640, FP16, 101-oqim. Model stol zonalari atrofini (+25%) ko'radi, zona bo'lmasa butun kadrni.
- Kelishmovchilik kadrlarini ko'z bilan tekshirildi (`review\compare\sheet_*.jpg`). "Ortiqcha" topilmalar asosan haqiqiy odamlar:
  tik turgan mehmon, yurib o'tayotganlar, etalon ularni hisobga olmagan. O'tkazib yuborishlar asosan mehmon to'sib qo'ygan o'rta stolda.
- Belgilangan video: `D:\Demos\Kamera\review\belgilangan_17-18_yolo26m.mp4` (17:18:40–17:21:40, 900 kadr, model 13.9 ms, 31 track ID).
- Kuzatuvlar:
  1. Kamera yo'lakda. Odamlar ob'ektiv oldidan o'tib, stollarni bir necha soniya to'sadi (52 kadr = 5.8% da odam yo'q).
  2. Kameraga yaqin stol (Muhammadumar) kadr chetida.
  3. Mehmon o'rta stol yonida uzoq turadi: o'tirganni to'sadi, ID almashadi.
  4. Shisha eshikda aks yoki narigi xonadagi odam ko'rinadi (conf 0.18–0.49), stol zonasidan tashqarida.
  5. "koridor_2" yozuvi (OSD) o'rta stol ustida.
- Tik turgan mehmonni o'tirgan deb hisoblamaslik rejasi: zonani o'tirgan odamning bosh-yelkasi atrofiga chizish va `anchor = upper`.
  Bu yetmasa 4-bosqichda pose varianti sinab ko'riladi.

## Qabul qilingan qarorlar

1. Mavjud dastur: CEO paneli `D:\Demos\odoo telegram integration\dashboard`. Streamlit 1.66, o'z venv'i `dashboard\.venv`, 127.0.0.1:8502. Foydalanuvchi tasdig'i kutilmoqda.
   Sahifalar `views\*.py` da, menyu va bloklar `ui\layout.py`, `ui\nav.py` da, o'zbekcha yozuvlar `ui\i18n.py` da.
2. Baza: alohida SQLite `D:\Demos\Kamera\camera_service\data\camera.db` (WAL), jadvallar `cam_` prefiksi bilan.
   Sabab: panelning ikki bazasi bor. `data\monitor.db` botga tegishli, unga faqat botning bitta moduli yozadi, panel uni faqat o'qiydi. `dashboard\data\state.db` esa panel ko'rinishi va chat tarixi.
   Kamerani alohida faylda saqlash mavjud ma'lumotni buzish xavfini yo'qotadi, zaxirasi ham alohida bo'ladi.
3. AI qismi alohida fon xizmati `camera_service` bo'ladi, o'z venv'i bilan (`camera_service\.venv`). Panel faqat bazani va oxirgi kadr faylini o'qiydi. RTSP paroli panelga umuman bermaydi.
4. Parol faqat `D:\Demos\Kamera\.env` da (`.gitignore`da). Kod `camsvc\config.py` URL'ni o'zi yig'adi (maxsus belgilar URL-encode qilinadi) va har qanday chiqishni `mask()` orqali `rtsp://***:***@IP` qiladi. ffmpeg chiqishi ham maskalanadi.
5. Test videolar: `test_clips\` (git'ga tushmaydi), faqat video, ovozsiz, `-c copy` (qayta kodlashsiz). 101 va 102 bir vaqtda yoziladi, keyin ikkalasini solishtirish uchun.
   Har klip yonida `.json` bor: yozish boshlangan UTC vaqt (±2 s). Sinov tugagach foydalanuvchidan so'rab o'chiriladi.
6. Panelni jonli holatda tahrirlamaslik kerak (Streamlit eski modullar bilan yangi `app.py`ni ishga tushirib yuborishi mumkin). Nusxada ishlab, panel to'xtatilgan paytda ko'chiriladi. To'xtatish uchun foydalanuvchi ruxsati kerak.
7. Git: panel loyihasida o'zgartirishlar 3-bosqichda yangi `kamera` branch'ida qilinadi. Hozir `main`da 2026-10-20 dagi agent-builder ishi commit qilinmagan. U zip zaxiraga kirgan, unga tegilmaydi.

## Zaxira

- `D:\Demos\odoo telegram integration\backups\dashboard_before_kamera_2026-10-20.zip`: 100 fayl, 13.5 MB, `.venv`siz. Panelning bugungi to'liq holati.

## Diqqat

- Panel LAN'da ochiq: `netsh portproxy` 192.168.87.2:8502 → 127.0.0.1:8502 (oldindan bor edi, men qilmaganman). Panelda parol (`DASHBOARD_PASSWORD`) yo'q.
  Kamera bo'limi tarmoqdagi istalgan kompyuterdan ko'rinadi. Foydalanuvchi buni qabul qildi ("prototype"), parol qo'yilmaydi.
- Kamera: 192.168.0.250:554. 101 = HEVC 1920×1080, 22 kadr/s. 102 = HEVC 640×360, 20 kadr/s. Ulanish 1.2–2.5 s.
- Internet: download.pytorch.org tez (~6.25 MB/s), PyPI (files.pythonhosted.org) sekin (~59 KB/s). Katta PyPI paketlari uzoq yuklanadi.
- Kamerada noto'g'ri parol bilan sinov QILINMAYDI: Hikvision bir necha xato kirishdan keyin hisobni bloklashi mumkin. Qayta ulanish sinovi noto'g'ri port bilan qilinadi.

## Buyruqlar

- "Yangi kamera"ni ishlab turgan panelga o'rnatish (FAQAT ruxsat bilan; panel va agent botlari ~15 s to'xtaydi):
  `powershell -ExecutionPolicy Bypass -File D:\Demos\Kamera\dashboard_addon\deploy_live.ps1 -Check` (tekshirish), keyin `-Check` siz.
  O'rnatgandan keyin dashboard reposida `kamera` branch ochib, faqat kamera fayllari va `app.py` commit qilinadi
  (`main` dagi commit qilinmagan agent-builder ishiga tegilmaydi).
- Qaytarish: `python D:\Demos\Kamera\dashboard_addon\install.py "D:\Demos\odoo telegram integration\dashboard" --uninstall` (panel to'xtatilgan holda) yoki zip zaxira.
- Panel ishchi nusxasi (sinov uchun): `D:\Demos\Kamera\dev\run_dev_dashboard.ps1` (127.0.0.1:8510), `-Stop` bilan to'xtatiladi.
  Nusxani yangilash: `python D:\Demos\Kamera\dashboard_addon\install.py D:\Demos\Kamera\dev\dashboard`.
  Testlar: `cd D:\Demos\Kamera\dev\dashboard; & "D:\Demos\odoo telegram integration\dashboard\.venv\Scripts\python.exe" -m pytest -q`.
- Kamera xizmati (2026-10-22 15:37 dan fon rejimida ishlayapti): `cd D:\Demos\Kamera\camera_service; Start-Process .venv\Scripts\python.exe "-m camsvc.service" -WindowStyle Hidden`.
  To'xtatish: `camera_service\data\STOP` faylini yaratish (≤10 s ichida to'xtaydi, keyingi startda fayl o'chiriladi). Log: `logs\service.log`.
- Video yozish (fon rejimida): `cd camera_service; Start-Process .venv\Scripts\python.exe "-m camsvc.recorder" -WindowStyle Hidden`.
  To'xtatish: `camera_service\data\STOP_REC` fayli (oxirgi faylni to'g'ri yopadi). Log: `logs\recorder.log`.
  Videolar (2026-10-26 dan): `E:\Kamera raw data\<yil>\<oy>\Koridor 2\<sana>\<soat>.mkv`, faqat 07:00–23:00.
- Test videoni quvurdan o'tkazish: `$env:CAMERA_DB_PATH=...; .venv\Scripts\python.exe tools\replay_clip.py ..\test_clips\kamera101_....mkv`.

- Kamera oqimini tekshirish (`.env` to'ldirilgach):
  `python D:\Demos\Kamera\camera_service\tools\record_clip.py --probe`
- Test video yozish (101 va 102, 60 daqiqa, fon rejimida):
  `python D:\Demos\Kamera\camera_service\tools\record_clip.py --minutes 60 --channels 101 102`
  Log: `camera_service\logs\record_*.log`, video: `test_clips\kamera101_YYYY-MM-DD_HH-MM-SS.mkv`.

## Sozlamalar (boshlang'ich, keyin "Sozlash" sahifasidan o'zgaradi)

| Nom | Qiymat |
|-----|--------|
| target_fps | 22 (2026-10-22 17:27 dan, kameraning maksimumi; 50 kamera bo'lganda 3 qilinadi) |
| sit_window | 10 s, kamida 70% zonada → "O'TIRDI" |
| away_grace | 600 s (10 daqiqa, foydalanuvchi) → "TURDI" |
| ishonch chegarasi (stol mantig'i) | 0.35 |
| tracker | bytetrack.yaml, `model.track()` conf 0.1 |
| model | birinchi urinish yolo26m, 2-bosqichda tanlanadi |
| ish kuni | har hodimga alohida. Erkaklar 09:00–19:00, ayollar 09:00–18:00, talab 10 soat. Du–Sha ish, Ya dam |
| namoz | yo'q bo'lgan vaqt namoz oynasiga to'g'ri kelsa, ish vaqtiga qo'shiladi (4-bosqich) |
| vaqt | bazada UTC, ko'rsatish Asia/Tashkent |

## O'zgargan fayllar ro'yxati

- Yangi: hammasi `D:\Demos\Kamera\` ichida: `camera_service\`, `dashboard_addon\`, `test_clips\`, `review\`, `dev\` (oxirgi uchtasi git'ga tushmaydi).
- Mavjud panelda (`odoo telegram integration\dashboard`) hali hech narsa o'zgarmagan, faqat zaxira olindi:
  `backups\dashboard_before_kamera_2026-10-20.zip`. `install.py --check` faqat o'qidi.
- Yon ta'sir (2026-10-20 17:46, mening xatoim): `C:\Users\Администратор\AppData\Roaming\Ultralytics\settings.json` yaratilgan, `sync=false` qilingan.
  O'chirish uchun foydalanuvchi ruxsati kerak.

## 2026-10-22: "dashboardga bo'lim ochish kerak"

- Panel 10-22 da ancha o'zgargan (dizayn agenti olib tashlangan, rasmlar va h.k.). Uning yangi nusxasiga addon o'rnatildi:
  anchorlar topildi, to'liq to'plam 210 o'tdi, 4 o'tkazildi. Addon o'zgartirilmadi.
- 14:47 da O'RNATILDI (`deploy_live.ps1`): zaxira `backups\dashboard_before_kamera_install_2026-10-22_14-47.zip` (101 fayl, 13.5 MB),
  14 fayl ko'chirildi, `app.py` ga 4 qator qo'shildi, panel "ishlayapti", agent botlari qayta yoqildi, `dashboard.err.log` toza.
- Kamera xizmati ishga tushirilmadi (foydalanuvchi: "hozir shunchaki panel qo'shilsa yetadi kamera ishlatilmasa ham"). Git commit qilinmadi.

## 2026-10-22: "hozircha ichidagi malumotlarni tozala"

- `tools\clear_desks.py` bilan `camera.db` dan 3 stol va 3 hodim (Nodir, Husniddin, Muhammadumar) o'chirildi.
  Zaxira: `camera_service\data\camera_before_clear_2026-10-22.db` (qaytarish uchun shu faylni `camera.db` o'rniga qo'yish kifoya).
- Qoldi: `cam_settings` (17 standart sozlama), `cam_meta`, test videodan kadr `data\live\latest*.jpg` (zona chizish uchun kerak).
- Panel ishlayapti (health "ok"). Kamera xizmati hali ishga tushirilmagan.

## 2026-10-22: "hozircha hech qanday malumot stol ismlar kerakmas chiqarib tashla faqat live bo'p tursin"

- Bo'limda faqat **Jonli** qoldi: `views\kamera.py` da `ALL_PAGES` (4 sahifa saqlangan) va `SHOWN = ("kamera_jonli",)`.
  Bugun, Kunlar, Sozlash qaytarish uchun ularning kalitlarini `SHOWN` ga qo'shib, qayta o'rnatish kifoya.
- Jonli sahifasi soddalashtirildi: faqat toza kadr (`latest_raw.jpg`: ramka, zona, ism yo'q), har soniyada yangilanadi,
  ustida bitta holat qatori (Ishlayapti / Javob bermayapti / Hali ishga tushmagan), kadr 30 s dan eski bo'lsa ostida izoh.
  Xizmat tafsilotlari, stollar holati va hodisalar jadvali endi ko'rsatilmaydi.
- Sinov: panel nusxasida to'liq to'plam o'tdi (210 o'tdi, 4 o'tkazildi), brauzerda faqat Jonli ko'rindi.
- 15:37 kamera xizmati qo'lda ishga tushirildi: 1.5 s da ulandi (kamera 22 kadr/s), GPU oldin 456 MiB band edi.
  Stol yo'q, shuning uchun model butun kadrni ko'radi. Interval yozilmaydi, faqat har daqiqalik qamrov va holat yoziladi.
  Server qayta yoqilsa xizmat O'ZI YOQILMAYDI (6-bosqich qilinmagan).
- 15:38 panel yangilandi (`deploy_live.ps1`): zaxira `backups\dashboard_before_kamera_install_2026-10-22_15-38.zip` (115 fayl, 13.6 MB).
  Ishlab turgan panelda Jonli: holat "Ishlayapti", kadr 15:38:43, xato yo'q.
- `tools\clear_desks.py` endi yozuvlarni O'CHIRMAYDI, faqat `active = 0` qiladi (panelning "Stolni o'chirish" kabi).
  15:30 dagi to'liq o'chirish undan oldingi versiya bilan qilingan. Eski versiyani qaytarishni Claude Code tekshiruvi to'xtatdi.
- Addon o'zgarishlari Kamera repo'sida commit qilinmagan.

## 2026-10-22 ~17:00: "kameralar ko'payib ketsa qotib boshlaydi, kamerani ustiga bosganda live bo'lsin"

- Jonli sahifasi endi kamera kartochkalari: nom, holat nuqtasi, kichik qotgan kadr (480 px, yangilanmaydi), "Jonli ko'rish →".
  Kartochka bosilganda (`?kamera=<id>`) faqat o'sha kamera jonli ochiladi (har soniyada), "Barcha kameralar" bilan ortga.
  Kameralar ro'yxati `views\kamera_jonli.py` `_cameras()` da: hozircha bitta, "Koridor 2" (`cam.name.1`).
- Sinov: yangi test (`test_live_page_goes_live_only_for_the_clicked_camera`), panel nusxasida to'liq to'plam 211 o'tdi, 4 o'tkazildi.
  Brauzerda: kartochka o'zi yangilanmaydi, bosilganda kadr yangilanadi, ortga qaytish ishlaydi.
- Ishlab turgan panelga O'RNATILMAGAN: `deploy_live.ps1` ni Claude Code tekshiruvi ("Production Deploy") to'xtatdi.
  O'rnatish: `powershell -ExecutionPolicy Bypass -File D:\Demos\Kamera\dashboard_addon\deploy_live.ps1`.
- Kamera xizmati 16:57 da ham ishlab turgan edi (kadr vaqti joriy).
- ~17:15, foydalanuvchi: "dashboarda live turmasin server qotib boshlaydi, faqatgina live bosganda ko'rsatib boshlasin".
  Qo'shildi: ochiq qolgan jonli ko'rinish 5 daqiqadan keyin to'xtaydi (`LIVE_LIMIT_S = 300`), "Davom ettirish" tugmasi bilan qayta yoqiladi.
  Kamera xizmatiga (20 kadr/s tahlil, boshqa sessiya ishi) tegilmadi: server o'sha payt CPU ~10%, GPU 8%, RAM 50 GB bo'sh edi.
  Sinov: to'liq to'plam 211 o'tdi, brauzerda bosish va yangilanish tekshirildi. O'rnatish yana to'xtatildi ("Production Deploy").
  Keyingi o'rnatish boshqa sessiyaning `kamera_sozlash.py` (slayder 1–20) o'zgarishini ham olib ketadi.

## 2026-10-22 ~17:05: "5060ti necha fps ko'tara oladi, maksimal bosim beraver, 20 bo'ladimi 15mi"

- GPU imkoniyati (test video, 1080p butun kadr, yolo26m 640 FP16): bitta kadr tahlili o'rtacha 15 ms (p95 21 ms) → ~65 kadr/s.
  Decode 4.4 ms, chizish+JPEG yozish soniyasiga bir marta ~16 ms. Ya'ni cheklov GPU emas, kamera: 101-oqim o'rtacha 21.8–22.6 kadr/s.
- `target_fps` = **20** (camera.db `cam_settings`, 17:00 da). GPU 8–24% band, ~25–30 W, VRAM 124 MB (torch).
- Topilgan va tuzatilgan muammolar:
  1. Xizmat taymeri qat'iy 1/fps qadam bilan ishlardi: kadr qadamdan biroz kech kelsa, o'sha qadam bo'sh ketardi (20 o'rniga ~15).
     Endi "kredit" usuli (`service.py`, `BURST = 3`): kadr kelishi bilan olinadi, o'rtacha tezlik baribir `target_fps` dan oshmaydi.
  2. Kamera kadrlarni notekis yuboradi (20 s o'lchov: 10% oraliq < 10 ms, 28% < 30 ms, 20% > 60 ms). Reader faqat oxirgi kadrni saqlardi,
     tahlil paytida 2 kadr kelsa biri yo'qolardi. Endi oxirgi 3 kadr saqlanadi, 0.25 s dan eski bo'lmasa navbat bilan olinadi (`stream.py`, `MAX_LAG`).
  3. ByteTrack `track_buffer` kadrda hisoblanadi (150 = 5 kadr/s da 30 s, lekin 20 kadr/s da atigi 7.5 s).
     Endi `pipeline.tracker_config()` uni `target_fps` ga qarab qayta yozadi (20 kadr/s → 600), fayl `data\bytetrack_desk_<N>.yaml`.
  4. Panelning "Sozlash" slayderi 1–10 edi → 1–20 (`views\kamera_sozlash.py`). Sozlash hozir yashirin, keyingi o'rnatishda o'tadi.
- Natija (jonli): 15.4 → 18.1 → 18.6–18.9 kadr/s (to'liq daqiqalar 17:05, 17:06; GPU 9%, 45 °C, 25 W). Kamera ~22 beradi, 20 dan tepasi uchun kamera yetmaydi.
- Testlar: kamera xizmati 20 o'tdi (yangi `test_pipeline.py`, `test_stream.py`), panel nusxasida `test_camera.py` o'tdi.
- Xizmat 3 marta qayta yoqildi (STOP fayli bilan, har biri ~2 s uzilish), oxirgisi 17:04:45.

## 2026-10-22 ~17:10: "keyin videolar yozilib boraverishi kerak"

- Boshlang'ich qoidadagi "doimiy video yozilmaydi" shu so'rov bilan bekor bo'ldi: endi 24/7 yoziladi.
- Yangi alohida jarayon `camsvc\recorder.py` (tahlil xizmatidan mustaqil, kameraga 2-ulanish). ffmpeg 101-oqimni `-c copy` bilan
  (qayta kodlashsiz, faqat video) 10 daqiqalik fayllarga yozadi, soat bo'yicha kesiladi (:00, :10, :20 ...).
  Papka: `E:\KameraVideo\koridor_2\YYYY-MM-DD\HH-MM-SS.mkv` (E: 2 TB HDD, 1863 GB bo'sh). Bugungi va ertangi papka oldindan yaratiladi.
- Oqim uzilsa yoki fayl 60 s o'smasa ffmpeg qayta yoqiladi (1, 2, 4 ... 30 s). E: da 50 GB dan kam joy qolsa yozish to'xtaydi,
  HECH NARSA O'CHIRILMAYDI. Hajm: test videoda ~650 MB/soat, ya'ni ~15.7 GB/kun, disk ~4 oyga yetadi.
  Eski videolarni avtomatik o'chirish (qancha kun saqlash) foydalanuvchidan so'raladi.
- 17:10:41 ishga tushdi: 40 s da 8.9 MB, HEVC 1920×1080, 22 kadr/s. Tahlil xizmati shu payt 19.9 kadr/s, uzilish yo'q.
  Boshida ffmpeg bir marta "Timestamps are unset / Non-monotonic DTS" ogohlantirishini berdi (RTSP boshida odatiy).
- 17:20 kesimi tekshirildi: `17-10-42.mkv` 559 s, 96 MB (~617 MB/soat) yopildi, `17-20-00.mkv` o'z vaqtida boshlandi, yangi ogohlantirish yo'q.
- Testlar: `tests\test_recorder.py` (3), jami 23 o'tdi. Server qayta yoqilsa recorder ham o'zi yoqilmaydi (6-bosqich).

## 2026-10-22 ~17:20: "50 ta kameraga ulasak fps necha bo'lsa bemalol ishlata olamiz?"

O'lchovlar (test video, jonli xizmat va yozish parallel ishlab turgan holda). Server: i7-14700K (8P+12E, 28 oqim), 64 GB RAM, RTX 5060 Ti 16 GB, tarmoq 1 Gbit/s.
- GPU, odam aniqlash (1080p kadr, imgsz 640, FP16, jami kadr/s):

  | model | batch 1 | batch 8 | batch 16 | batch 32 |
  |---|---|---|---|---|
  | yolo26m (hozirgi) | 125 | 204 | 178 | 170 |
  | yolo26s | 93 | 262 | 256 | 309 |
  | yolo26n | 99 | 290 | 436 | 401 |

- Video ochish (HEVC 1080p): CPU 1 oqimda 357 kadr/s (BGR ga o'tkazishsiz), NVDEC 825 kadr/s (CPU xotirasiga ko'chirish bilan).
  Hozirgi OpenCV reader har kadrni BGR ga o'tkazadi: 1 kamera 22 kadr/s da 1 yadroning ~31% i (1-bosqich o'lchovi).
- Xulosa, 50 kamera:
  - yolo26m bilan jami ~200 kadr/s → kameraga ko'pi bilan 4, **bemalol 2 kadr/s**. yolo26s bilan bemalol 3. Stol vaqti uchun 2 kadr/s yetarli
    (10 s oynada 20 ta kadr). Hozirgi 20 kadr/s faqat ~5 kameragacha bemalol.
  - CPU: hozirgi reader bilan 50 × 31% ≈ 15 yadro, juda og'ir. `grab()` (har kadrni ochadi, BGR ga faqat tahlil qilinadiganini) bilan ~3 yadro.
    NVDEC 50 × 22 = 1100 kadr/s ni ko'tarmaydi (825), kamera fps 15 ga tushirilsa sig'adi.
  - Arxitektura: hozir 1 jarayon = 1 kamera. 50 ta alohida jarayon VRAM ga sig'maydi (har biri CUDA konteksti bilan ~0.5 GB).
    Kerak: bitta jarayon, 50 ta reader oqimi va bitta umumiy GPU navbati (batch 8).
  - Tarmoq: 50 × ~1.5 Mbit/s × 2 ulanish (tahlil + yozish) ≈ 150 Mbit/s, 1 Gbit/s ga sig'adi.
  - Disk: 101-oqimni 24/7 yozish 50 × 15.7 GB = ~785 GB/kun, E: (1.86 TB) ~2.4 kunga yetadi. Bu eng katta cheklov.
    Variantlar: 102-oqimni yozish (~21 GB/kun, ~3 oy), faqat ish vaqtida yozish yoki ko'proq disk (30 kun uchun ~24 TB).
- Foydalanuvchi qarori (17:27): hozir 1 kamera uchun **22 kadr/s**, 50 kamera bo'lganda **3 kadr/s** (50 × 3 = 150 kadr/s, yolo26m chegarasining ~75% i).
  `target_fps` 17:27:34 da 22 ga qo'yildi (xizmat qayta yoqilmadi, sozlamani o'zi o'qiydi). Panel slayderi 1–22 (addon, hali o'rnatilmagan).
  Natija: 17:28 daqiqasida kamera 22.0, tahlil 18.6 kadr/s (20 qo'yilganda ham 18.6–18.9 edi), GPU 8%, `track_buffer` 660.
  Ya'ni ~18.7 da ship bor va u GPU emas. Qolgan ~15% kadr qayerda yo'qolishi o'lchanmagan (reader/sikl vaqtini yozib ko'rish kerak).

## 2026-10-22 ~17:30: "fps ni ko'tar" (Jonli ko'rinish, boshqa sessiya emas)

- Jonli ko'rinish 1 kadr/s edi (xizmat kadrni soniyada 1 marta yozardi, sahifa soniyada 1 marta qayta ishlardi).
- Endi: Jonli ochiq turganda panel har 2 s da `data\live\watch.json` yozadi: {"ts", "dir": "<dashboard>\static\kamera"}.
  Shu so'rov 6 s dan yangi bo'lsa, xizmat (`camsvc\live_stream.py`, `service.py` ga 3 joy) tahlil qilingan deyarli har bir
  kadrni 1280 px JPEG qilib `dashboard\static\kamera\stream.jpg` ga yozadi (oraliq ≥ 25 ms, bitta urinish:
  brauzer o'qiyotgan bo'lsa kadr tashlanadi, tahlil kutmaydi). Hech kim ko'rmasa ortiqcha hech narsa yozilmaydi.
- Brauzer `stream.jpg` ni `/app/static/kamera/` dan o'zi qayta-qayta yuklaydi (JS, `st.html(unsafe_allow_javascript=True)`),
  soniyasiga 20 tagacha, sahifa har kadrda qayta ishlamaydi. 5 daqiqalik to'xtash JS da ham bor.
  `install.py` panel `.streamlit\config.toml` [server] ga `enableStaticServing = true` qo'shadi, `static\kamera\` (+ `.gitignore`) yaratadi.
- O'lchov (panel nusxasi, 8512): brauzerda 16.6 yuklash/s, diskda 16.0 yangi kadr/s (oldin 1). Xato yo'q.
- Testlar: kamera xizmati 25 o'tdi (yangi `tests\test_live_stream.py`), panel nusxasida to'liq to'plam o'tdi.
- Kamera xizmati yangi kod bilan 3 marta qayta yoqildi (17:23, 17:26, 17:27; STOP fayli, har biri ~3 s uzilish).
- Ishlab turgan panelga O'RNATILMAGAN: `deploy_live.ps1` ni Claude Code tekshiruvi ("Production Deploy") to'xtatdi.
  O'rnatilganda Jonli kartochkalari, 5 daqiqalik to'xtash va Sozlash slayderi (1–22) ham birga o'tadi.

## 2026-10-22 ~17:40: "gpuda faqat kamera uchun ishlatamiz boshqa narsalarni tozalab tasha"

- Tekshiruv: GPU'da hisoblash jarayoni faqat kamera xizmati edi (8–12%, 0.8/16 GB). Ollama va STT bo'sh turgan, VRAM olmagan.
  Foydalanuvchiga aytildi: tozalash tezlikni (18.6) oshirmaydi, cheklov GPU emas.
- Foydalanuvchi tanlovi bilan TO'XTATILDI: Ollama (`ollama app.exe`, `ollama.exe`; modellar diskda qoldi) va STT sahifasi (rubaiSTT Streamlit, 8501-port).
  Ikkalasida avtomatik yoqilish yo'q, server qayta yoqilsa ham o'chiq qoladi. Qayta yoqish buyruqlari xotira faylida (ai_server_environment).
- Kamera xizmati va video yozish ta'sirlanmadi (tahlil 20.0 kadr/s, uzilish yo'q).

## 2026-10-22 ~18:00: "proektni odoo telegram integration ichiga qo'y, birlashtir; 1 kunlik statistika ertadan; kompyuterda va xonada qancha o'tirdi"

Kamera endi eshik tomonga qaragan. Kadrning pastki o'ng qismida 3 kishi kompyuterda o'tiradi (chapdan o'ngga Joy 1, 2, 3).
Foydalanuvchi javoblari: har bir odam alohida; "Xonada" = keldi → ketdi, uzoq chiqib ketishlar ayirilgan; statistika faqat panelda.

- Joylar (17:56, camera.db; zaxira `data\camera_before_zones_2026-10-22.db`): 3 hodim "Joy 1/2/3" (09:00–19:00, 10 soat, Du–Sha)
  va 3 stol zonasi (kadr ulushida, markaz yakori): Joy 1 x 0.665–0.745, Joy 2 x 0.752–0.840, Joy 3 x 0.885–0.995, hammasi y 0.70–0.97.
  Joy 2 va 3 orasidagi stuldagi sumka (x≈0.86, conf 0.44) zonalarga tushmaydi. Ismlar berilsa `cam_employees.name` almashtiriladi.
- Sozlamalar: `away_grace_s` 600 → **120** (Kompyuterda = haqiqiy o'tirgan vaqt; oldingi "10 daqiqa" qoidasi o'rniga),
  yangi `room_exit_min` = 30, `report_start` = "2026-10-23".
- Hisob (`dashboard\camera\report.py`): Kompyuterda = `sat_s` (intervallar birlashmasi). Xonada = `room_s` = birinchi kelishdan
  oxirgi ketishgacha, ma'lumot bo'lgan qismi `room_exit_min` dan uzun tanaffuslar ayirilgan (kamera o'chiq vaqt chiqib ketish emas).
- Yangi sahifa «Statistika» (`views\kamera_statistika.py`): davr bo'yicha (o'rtacha va jami xonada/kompyuterda, kelgan kunlar)
  va kunlar bo'yicha (Sana, Hodim, Keldi, Ketdi, Xonada, Kompyuterda) + Excel. `report_start` dan oldingi kunlar ko'rsatilmaydi.
  Bo'limda ko'rinadi: Statistika va Jonli (`views\kamera.py` `SHOWN`).
- Birlashtirish: `camera_service` `D:\Demos\odoo telegram integration\camera_service` ga ko'chirildi (kod, .venv 3.5 GB, modellar,
  data, loglar, `.env`). `config.py`: `.env`, test_clips, review endi `camera_service` ichida. Loyiha `.gitignore` ga `*.pt`, `wheels/`,
  `test_clips/`, `camera_service/review/` qo'shildi (`.env`, `.venv`, `data`, `logs` oldindan e'tiborsiz). Git commit qilinmagan.
  Panel `store.py` bazani endi `dashboard\..\camera_service\data` dan o'qiydi.
- 18:02:55 eski jarayonlar to'xtatildi, 18:03 dan uchalasi yangi joydan ishlaydi (tahlil ~50 s to'xtadi: robocopy `/XD data`
  torch'ning `torch\utils\data` papkasini ham tashlab ketgan edi, venv to'liq qayta nusxalandi). 18:04: 20.4 kadr/s, uchala joy "o'tiribdi".
- Sinov: kamera xizmati 26 test, panel to'liq to'plami (yangi nusxada) o'tdi, 4 o'tkazildi. Bugungi haqiqiy ma'lumotda Joy 1–3 hisoblandi.
- PANELGA HALI O'RNATILMAGAN: `deploy_live.ps1` ni Claude Code tekshiruvi ("Production Deploy") to'xtatdi. O'rnatilmaguncha ishlab
  turgan panel bazani ESKI joydan o'qiydi (Jonli kadri 18:02 da qotib qoladi). O'rnatish:
  `powershell -ExecutionPolicy Bypass -File D:\Demos\Kamera\dashboard_addon\deploy_live.ps1`. Undan keyin manba = `dashboard\` papkasi.

## 2026-10-26: E:\ = xom ma'lumotlar ("Kamera raw data", "Telegram raw data"), keyin tozalangani F:\ ga

- Server 2026-10-24 08:22 da qayta yoqilgan: tahlil xizmati, recorder, live_relay va Telegram bot shundan beri
  ishlamagan (avtomatik yoqilish yo'q). 10-26 11:05 da qo'lda yoqildi.
- Foydalanuvchi 10-26 10:40 da `E:\KameraVideo` (10-22..10-24, 6.8 GB) va `E:\Backups` ni Recycle Bin'ga o'chirgan.
- Recorder endi faqat **07:00–23:00** yozadi (foydalanuvchi: "hozircha"; keyin tozalashda birinchi kelgan → oxirgi
  ketgan qoladi). Papka: `E:\Kamera raw data\2026\10-Oktabr\Koridor 2\2026-10-26\HH-MM-SS.mkv` (10 daqiqalik fayllar,
  bir kun = bir papka). Sozlamalar `camera_service\.env` da (ixtiyoriy): `RECORD_ROOT`, `RECORD_CAMERA_NAME`,
  `RECORD_FROM`, `RECORD_TO` (yarim tundan o'tmasin). 23:00 da ffmpeg "q" bilan yopiladi, logga kunlik jami yoziladi.
  11:15:48 dan yozyapti. Testlar 29 o'tdi. E: da `O'QING.txt` (tuzilma tushuntirilgan).
- Telegram tomoni: botning `app/raw_archive.py` (`RAW_TELEGRAM_DIR`), shu loyiha README/xotirasida.

## Keyingi qadamlar

1. Kerak bo'lganda Bugun/Kunlar/Sozlash'ni qaytarish (`SHOWN`), stollar va hodimlarni qayta kiritish.
2. Foydalanuvchi "Sozlash"da stol zonalarini chizadi (★ 3-bosqich).
3. 6-bosqich: Task Scheduler (server qayta yoqilganda xizmat o'zi yoqilsin), loglar, kundalik zaxira E: ga.
4. 4-bosqich ★: haqiqiy zonalar bilan test videoni qayta o'tkazish va tekshiruv varag'i (intervallar + kichik rasmlar) tayyorlash.
