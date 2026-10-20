# Kamera loyihasi — holat

Vazifa: bitta kamera orqali har bir hodimning stol oldida o'tirgan vaqti (keldi, ketdi, necha marta turdi).
Natija mavjud CEO panelidagi yangi "Yangi kamera" bo'limida ko'rinadi.
Suhbat uzilsa, ish shu fayldan davom ettiriladi.

Oxirgi yangilanish: 2026-10-20 17:00 (Toshkent)

## Bosqichlar

| # | Bosqich | Holat |
|---|---------|-------|
| 0 | Tayyorgarlik | Tekshiruvlar va zaxira tayyor. ★ javoblar va `.env` kutilmoqda, keyin test video yoziladi |
| 1 | Muhit va oqim | boshlanmagan |
| 2 | Odamni aniqlash va kuzatish | boshlanmagan |
| 3 | Baza, stol zonalari, "Yangi kamera" skeleti | boshlanmagan |
| 4 | Vaqt hisoblash mantig'i | boshlanmagan |
| 5 | "Yangi kamera" bo'limi to'liq | boshlanmagan |
| 6 | Doimiy ishlash | boshlanmagan |
| 7 | Bir kunlik haqiqiy test | boshlanmagan |

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

## Diqqat (foydalanuvchi qaror qiladi)

- Panel LAN'da ochiq: `netsh portproxy` 192.168.87.2:8502 → 127.0.0.1:8502 (oldindan bor edi, men qilmaganman). Panelda parol (`DASHBOARD_PASSWORD`) qo'yilmagan.
  Kamera bo'limi qo'shilsa, tarmoqdagi istalgan kompyuter hodimlarning jonli kadrini ko'ra oladi. ★ savol sifatida berildi.

## Buyruqlar

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
| away_grace | 180 s → "TURDI" (★ tasdiqlanadi) |
| ishonch chegarasi (stol mantig'i) | 0.35 |
| tracker | bytetrack.yaml, `model.track()` conf 0.1 |
| model | birinchi urinish yolo26m, 2-bosqichda tanlanadi |
| ish kuni | ★ kutilmoqda (taklif 09:00–18:00) |
| vaqt | bazada UTC, ko'rsatish Asia/Tashkent |

## O'zgargan fayllar ro'yxati

- Yangi: `D:\Demos\Kamera\` (`.env.example`, `.env` bo'sh shablon, `.gitignore`, `LOYIHA_HOLATI.md`, `camera_service\camsvc\config.py`, `camera_service\tools\record_clip.py`).
- Mavjud dasturda hali hech narsa o'zgarmagan (faqat zaxira olindi).
