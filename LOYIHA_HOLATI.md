# Kamera loyihasi — holat

Vazifa: bitta kamera orqali har bir hodimning stol oldida o'tirgan vaqti (keldi, ketdi, necha marta turdi).
Natija mavjud CEO panelidagi yangi "Yangi kamera" bo'limida ko'rinadi.
Suhbat uzilsa, ish shu fayldan davom ettiriladi.

Oxirgi yangilanish: 2026-10-29 (Toshkent)

## 2026-10-29: vazifa toraytirildi

Foydalanuvchi: faqat hodim xonaga nechida keldi, nechida ketdi, qancha vaqt tashqarida bo'ldi - jadval ko'rinishida;
har bir chiqish va kirishning 5 soniyalik kadrlari; hodimlar aralashmasin, 1 hodim = doim 1 ID (oldin 100-150 ta "hodim" chiqqan).

Qilindi:
- Hodim faqat o'z stoli orqali aniqlanadi (`cam_desks.employee_id`). ByteTrack ID lari faqat "yurib o'tyaptimi" tekshiruvida
  ishlatiladi, bazaga ham, ekranga ham chiqmaydi. Bir hodim faqat bitta stolga biriktiriladi (`store.save_desk` rad etadi),
  "Hodimlar" jadvalida bir xil ism ikki marta saqlanmaydi.
- `camsvc\clips.py`: har bir kelish (interval boshi, [-3 s, +2 s]) va ketish (interval oxiri "away", [-2 s, +3 s]) uchun
  5 soniyalik H.264 video, `data\clips\<manba>\YYYY-MM-DD\<hodim id>_<HH-MM-SS>_<in|out>.mp4`, jadval `cam_clips`.
  ffmpeg yo'q bo'lsa bitta kadr .jpg. `clip_keep_days` (30) dan eskilari o'chadi.
- "Bugun": Hodim | Keldi | Ketdi | Tashqarida | Necha marta chiqdi (+ Hozir), keyin tanlangan hodimning chiqishlari va videolari.
  "Kunlar": har kun va hodim uchun shu jadval + Excel. Namoz, kerakli soat, soatlar jadvali, kun chizig'i olib tashlandi
  (`camera\prayer.py` o'chirildi, `install.py` uni panel papkasidan ham o'chiradi).
- Tashqarida = ikki interval orasidagi vaqt, kamera ishlamagan daqiqalar ayirib. `away_grace_s` (10 daq) dan qisqa yo'qlik chiqish emas.

## Bosqichlar

| # | Bosqich | Holat |
|---|---------|-------|
| 0 | Tayyorgarlik | tugadi. ★ javoblar olindi (pastda). Test video 17:03–18:03 yozildi (101: 625 MB, 102: 19.8 MB) |
| 1 | Muhit va oqim | tugadi (2026-10-20 18:10), natijalar pastda |
| 2 | Odamni aniqlash va kuzatish | tugadi. ★ belgilangan videoga javob hali yo'q (foydalanuvchi "dashboardga chiqar" dedi) |
| 3 | Baza, stol zonalari, "Yangi kamera" skeleti | qurildi va sinaldi, ishlab turgan panelga HALI O'RNATILMAGAN (2026-10-21: "bugun ishlatma"). ★ zonalarni foydalanuvchi chizadi |
| 4 | Vaqt hisoblash mantig'i | modul va testlar tayyor. Test video taxminiy zonalar bilan quvurdan o'tdi. Tekshiruv varag'i haqiqiy zonalardan keyin |
| 5 | "Yangi kamera" bo'limi to'liq | 4 sahifa qurildi (Bugun, Kunlar, Jonli, Sozlash), ishchi nusxada sinaldi, o'rnatilmagan |
| 6 | Doimiy ishlash | boshlanmagan |
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
- Kamera xizmati (hali ishga tushirilmagan): `cd D:\Demos\Kamera\camera_service; .venv\Scripts\python.exe -m camsvc.service` (to'xtatish: Ctrl+C yoki `data\STOP` fayli).
- Test videoni quvurdan o'tkazish: `$env:CAMERA_DB_PATH=...; .venv\Scripts\python.exe tools\replay_clip.py ..\test_clips\kamera101_....mkv`.

- Kamera oqimini tekshirish (`.env` to'ldirilgach):
  `python D:\Demos\Kamera\camera_service\tools\record_clip.py --probe`
- Test video yozish (101 va 102, 60 daqiqa, fon rejimida):
  `python D:\Demos\Kamera\camera_service\tools\record_clip.py --minutes 60 --channels 101 102`
  Log: `camera_service\logs\record_*.log`, video: `test_clips\kamera101_YYYY-MM-DD_HH-MM-SS.mkv`.

## Sozlamalar (boshlang'ich, keyin "Sozlash" sahifasidan o'zgaradi)

| Nom | Qiymat |
|-----|--------|
| target_fps | 5 (GPU qiynalsa 2–3) |
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
- `deploy_live.ps1` ni ishga tushirishni Claude Code xavfsizlik tekshiruvi ("Production Deploy") to'xtatdi. Panel o'zgarmagan.
  Foydalanuvchi o'zi ishga tushiradi yoki aniq ruxsat beradi.

## Keyingi qadamlar

1. Ruxsat bilan `deploy_live.ps1`: "Yangi kamera" panelda paydo bo'ladi.
2. Foydalanuvchi "Sozlash"da 3 stol zonasini chizadi (★ 3-bosqich). Kamera ko'chirilsa, avval yangi kadr kerak, ya'ni xizmat bir marta ishga tushadi.
3. Kamera xizmatini ishga tushirish (ruxsat bilan), keyin 6-bosqich: Task Scheduler, qayta ishga tushish, loglar, kundalik zaxira E: ga.
4. 4-bosqich ★: haqiqiy zonalar bilan test videoni qayta o'tkazish va tekshiruv varag'i (intervallar + kichik rasmlar) tayyorlash.
