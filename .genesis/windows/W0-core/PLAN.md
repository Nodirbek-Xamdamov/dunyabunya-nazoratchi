# W0 Core — reja (qotirilgan)

Papka: `apps/w0-core` (+ umumiy `shared/`). Boshlash sharti: yo'q, birinchi bo'lib boshlanadi.
Mas'uliyat: baza, navbat, GPU gate, scheduler, servislar, sog'lomlik nazorati, shartnomalar (`CONTRACTS.md` ni amalga oshirish).
Tegmaydi: Telegram kodi, tahlil prompti, dashboard, kamera mantig'i.

## Qadamlar

**W0-01 Muhit auditi va GPU smoke-test**
- Qil: Windows Server versiyasi, CPU/RAM/disk, Python 3.12, ffmpeg, NVIDIA drayver va CUDA (Blackwell uchun CUDA 12.8+) tekshiruvi. Kichik GPU testi, Gemma 4 12B (4-bit) ni llama.cpp va Ollama da yuklab sinash, YOLO ni GPU da sinash.
- Natija: `apps/w0-core/ENV_REPORT.md` (jadval: nima ishladi/ishlamadi, tezlik, VRAM). Runtime tavsiyasi: llama.cpp yoki Ollama.
- Qabul: `nvidia-smi` chiqishi, GPU da matmul testi, Gemma 4 12B 100 token generatsiya qiladi (tezlik yozilgan), rasm kirishi ishlaydi, YOLO bitta rasmni GPU da bajaradi, ikkalasi bir vaqtda sig'ish/sig'maslik o'lchovi (VRAM).
- Bog'liq: Q10.

**W0-02 Papka tuzilmasi, oyna ruxsatlari, sirlar**
- Qil: `apps/*`, `shared/`, `secrets/`, `data/` (media va kamera video bo'laklari, alohida disk bo'lishi mumkin) papkalari. Har oyna uchun `.claude/settings.json`: `additionalDirectories` (`.genesis`, `shared`) va `secrets/`, `.env`, `*.session` ni o'qishga taqiqlash. `.gitignore`. Log va sozlama yuklagich (`config.toml` + sirlar).
- Qabul: har bir oynada agent `secrets/` ni o'qiy olmasligi sinab ko'rilgan; `.genesis` ni o'qiy olishi tasdiqlangan.
- Bog'liq: Q12.

**W0-03 PostgreSQL, rollar, zaxira**
- Qil: PostgreSQL 16 o'rnatish, `Asia/Tashkent` ko'rsatkichlari, `CONTRACTS.md` §5 dagi rollar, kunlik `pg_dump` (boshqa diskka), disk byudjeti hisobi (media + kamera video aylanma buferi, disk turi Q16). Media papkasi `data/media/yil/oy/guruh/`.
- Qabul: har bir rol faqat ruxsat etilganini yoza oladi (salbiy test), zaxira fayli paydo bo'ladi.

**W0-04 DB sxema v1 va migratsiyalar**
- Qil: `CONTRACTS.md` §1 va §6 bo'yicha jadvallar va view'lar (`messages` va `transcripts` oylik partition, D-13), oylik arxiv buyrug'i (partition ajratish → siqilgan fayl → ikkinchi disk → o'chirish), migratsiya tizimi (versiyalangan SQL fayllari), idempotent kalitlar.
- Natija: `apps/w0-core/migrations/`, `shared/` da sxema hujjati (`SCHEMA.md`).
- Qabul: bo'sh bazada hamma migratsiya xatosiz o'tadi, ortga qaytarish ishlaydi, `SCHEMA.md` jadval ustunlari bilan to'liq. **Bu qadam tugagach M1 boshlanadi: boshqa oynalar ochilishi mumkin.**

**W0-05 Ish navbati (jobs)**
- Qil: `SKIP LOCKED` navbat, qayta urinish (eksponensial), dead-letter, idempotency, `gpu_class`. Kichik umumiy kutubxona (`shared/queue`).
- Qabul: 2 ishchi bir job'ni ikki marta olmaydi, ishchi o'lsa job qaytadi, 5 urinishdan keyin `dead`, test natijasi logda.

**W0-06 GPU gate va jadval**
- Qil: model yuklash/chiqarish boshqaruvchisi va rejimlar (v1.3, D-30), rejim sozlamada: `timeslice` — **standart**, 12/12: Telegram (Gemma) va kamera (YOLO) oynalari sozlamada (boshlang'ich Telegram 10:00–22:00, kamera 22:00–10:00, Asia/Tashkent), kamera oynasida kamera job'i bo'lmasa GPU Gemma ga qaytariladi (kamera job'i kelsa yana almashadi); `coresident` — zaxira (D-14: Gemma va YOLO birga); `dedicated` — keyinroq, ikkinchi GPU kelganda (`gpu_class` → qurilma raqami sozlamada). Oyna almashishidan 15 daqiqa oldin yangi og'ir job bermaslik, qo'lda override buyrug'i.
- Qabul: oyna chegaralarida (boshlang'ich 10:00 va 22:00) model almashadi (vaqtni tezlatilgan soat bilan sinov), almashish paytida yo'qolgan job yo'q, VRAM bo'shaydi (`nvidia-smi` isboti); kamera oynasida kamera job'i bo'lmasa GPU Gemma ga qaytadi.
- Bog'liq: W0-01, W0-05.

**W0-07 Windows servislar**
- Qil: NSSM bilan har bir ilova uchun servis, avtostart, avtoqayta ishga tushirish, log fayllari. RDP sessiyasi yopilganda jarayonlar ishlashda qolishi.
- Qabul: RDP uzilgach va serverni qayta ishga tushirgach hamma servis o'zi ko'tariladi.

**W0-08 Sog'lomlik nazorati va tozalash**
- Qil: `heartbeats`, "N daqiqa xabar kelmadi" va "navbat to'planib qoldi" signali (admin guruhiga reporter bot orqali, W3 bilan kelishilgan sirt), retention/tozalash ishlari (P-05, D-12, D-13), kunlik `daily_close` scheduler'i.
- Qabul: ingest sun'iy o'chirilganda 10 daqiqada signal keladi; eski matn va video bo'laklar tozalanadi.

**W0-09 Soxta ma'lumot va shartnoma testlari**
- Qil: sun'iy guruh xabarlari, namunaviy voice/rasm, namunaviy kamera video bo'laklari; har bir oyna Telegram va kamerasiz test qila olishi uchun.
- Qabul: fixtures bazaga bir buyruq bilan yuklanadi; shartnoma testlari (jadval/view/job formati) o'tadi.

**W0-10 Xavfsizlik va tiklash tekshiruvi**
- Qil: RDP ekspozitsiyasi (VPN yoki IP ro'yxati, NLA, MFA tavsiyasi) hisoboti, sirlar ACL, zaxiradan to'liq tiklash sinovi.
- Qabul: bo'sh serverga zaxiradan tiklash yo'riqnomasi bajarilgan va ishlagan; xavfsizlik hisoboti `SECURITY.md` da.

## Chegaralar

- Boshqa oynalar W0 ni o'zgartira olmaydi. Ularga o'zgarish kerak bo'lsa `[CONTRACT]` savoli yoziladi, W0 hal qiladi va `SCHEMA.md` versiyasini oshiradi.
