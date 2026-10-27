# LEGACY — ishlab turgan eski tizim inventari (W0-02)

Sana: 2026-10-26 ~12:25 (Asia/Tashkent). Faqat o'qish buyruqlari bilan yig'ildi: `Get-CimInstance Win32_Process`, `Get-NetTCPConnection -State Listen`, `Get-ScheduledTask`, `Win32_Service`, `nvidia-smi`, `netsh interface portproxy show all`, `du`.
Hech narsa to'xtatilmadi, ko'chirilmadi, sozlanmadi. `.env`, sessiya va SQLite fayllari ochilmadi (faqat hajmi ko'rildi). Buyruq qatorlaridagi parollar `***` bilan yashirildi.

**Qoida:** quyidagilarga Genesis oynalari TEGMAYDI (to'xtatmaydi, ko'chirmaydi, sozlamasini o'zgartirmaydi). Almashtirish rejasi Q19 (bot) va Q20 (jonli kamera) javoblaridan keyin.

## 1. Shu loyiha (`D:\Demos\odoo telegram integration`) ichidagi eski tizim

Windows venv'da har bir jarayon ikkita ko'rinadi: venv ishga tushirgichi + asosiy `C:\Program Files\Python312\python.exe` bola jarayon.

| Komponent | Jarayon (PID, boshlangan) | Port | Qanday ishga tushadi | Papka / baza | GPU / kamera |
|---|---|---|---|---|---|
| Telegram bot `@dunyabunya_assistant_bot` (aiogram, guruh monitoringi, kunlik Excel) | `.venv\Scripts\python.exe -m app.bot` 17916 → 18172, 10-26 11:15, ~267 MB | tinglovchi port yo'q (Telegram polling) | qo'lda; rejalashtirilgan vazifa yo'q, qayta yuklangandan keyin o'zi ko'tarilmaydi | kod `app\`, baza `data\monitor.db` (SQLite, 352 KB), `data\` jami 12 MB (delivery_photos, media, order_media...), zaxira `backups\monitor_YYYY-MM-DD.db` (jami `backups\` 46 MB), loglar `logs\` (9 MB), xom arxiv `E:\Telegram raw data` (54 MB) | GPU ishlatmaydi (loyiha eslatmasiga ko'ra tasnif 2026-10-22 dan OpenAI orqali; tekshirilmadi) |
| FastAPI (`:8000`) va Telethon userbot | hozir ishlamayapti | — | — | `userbot_session.session` (ildizda, ochilmadi) | — |
| CEO dashboard (Streamlit 1.66) | `dashboard\.venv ... streamlit run app.py` 17048 → 15012, 10-24 13:32 | 127.0.0.1:8502; LAN dan `netsh portproxy` 192.168.87.2:8502 → 127.0.0.1:8502 | qo'lda (`run_dashboard.ps1`) | `dashboard\`, `dashboard\data\state.db` (96 KB), `data\monitor.db` ni faqat-o'qish, `dashboard\demo\demo_monitor.db` (52 MB) | GPU yo'q |
| Agent botlari (dashboard ichidan) | `dashboard\.venv ... agent_bots.py` 11356 → 7328, 10-24 13:32 | port yo'q | qo'lda | `dashboard\` (D-16: eski ilovada qoladi) | GPU yo'q |
| Jonli kamera servisi `camsvc.service` (YOLO26 + ByteTrack, stol/joy hisobi) | `camera_service\.venv ... -m camsvc.service` 3248 → 16156, 10-26 11:05, ~1.9 GB RAM | port yo'q | qo'lda (avtostart yo'q) | `camera_service\`, `camera_service\data\camera.db` (SQLite + WAL ~4 MB, jami 4.8 MB), loglar `camera_service\logs` | **GPU** (nvidia-smi ro'yxatida yagona compute jarayon). **Kamera ulanishi #1**: 192.168.0.250 |
| Kamera yozuvchi `camsvc.recorder` | `-m camsvc.recorder` 17264 → 17144, 10-26 11:15 + `ffmpeg.exe` 18260 | port yo'q | qo'lda | `E:\Kamera raw data\2026\10-Oktabr\Koridor 2\<kun>\HH-MM-SS.mkv`, 10 daqiqalik bo'laklar, 07:00–23:00 (hozir 836 MB) | CPU (`-c copy`). **Kamera ulanishi #2**: `rtsp://***@192.168.0.250:554/Streaming/Channels/101` |
| Jonli rele `camsvc.live_relay` | `-m camsvc.live_relay` 13812 → 8704, 10-26 11:05 | tinglovchi port ko'rinmadi | qo'lda | `camera_service\` | kadrlarni `camsvc.service` dan oladi (alohida ulanish ko'rinmadi) |

GPU holati (o'lchov paytida): `nvidia-smi` jami 706 MiB / 16311 MiB, yuklama 9%. Windows (WDDM) jarayon bo'yicha VRAM ko'rsatmaydi; compute jarayon faqat `camsvc.service` (PID 16156). Ollama ishlab turibdi (127.0.0.1:11434, PID 17272), model yuklanmagan.

Kamera: Hikvision 192.168.0.250 ("koridor_2"). Hozir unga eski tizimdan 2 ta ulanish bor (servis + yozuvchi). W4 intake ham ulanadi. Noto'g'ri parol bilan sinash man (Hikvision bloklaydi).

## 2. Serverdagi boshqa loyihalar (resurs ulashiladi, Genesis tegmaydi)

| Loyiha | Jarayon / servis | Port | Ishga tushish |
|---|---|---|---|
| `D:\Demos\dunyabunya ceo agent` (CEO bot) | `-m app.main` 9108 → 10108 | **0.0.0.0:8002** | vazifa `dunyabunya_bot_watchdog` (har daqiqa + boot), `dunyabunya_db_backup` (03:00), `dunyabunya_cloudflare_tunnel` (boot, SYSTEM, oxirgi natija 1) |
| `D:\Production\telegram-odoo-bot` (docbot) | `-m docbot run` 9384 → 3792 | yo'q | vazifa `telegram_odoo_bot_watchdog` |
| `D:\Demos\fleet_bot`, `D:\Production\Hisoblovchi` | `venv\Scripts\python.exe main.py` (7940, 8680) → `main.py` (3268, 9920) | yo'q | vazifalar `fleet_bot_watchdog`, `hisoblovchi_watchdog` |
| PostgreSQL **17** (`postgresql-x64-17`, data `C:\Program Files\PostgreSQL\17\data`) | `postgres.exe` 7080 | **0.0.0.0:5432** | Windows servis, Auto |
| Memurai (Redis) | `memurai.exe` 4440 | 127.0.0.1:6379 | Windows servis, Auto |
| Apache 2.2 | `httpd.exe` 4284 | **0.0.0.0:80** | Windows servis, Auto |
| Ollama | `ollama.exe serve` 17272, `ollama app.exe` | 127.0.0.1:11434 | qo'lda / foydalanuvchi sessiyasi (W0-07 da servis) |
| Tizim | RDP 3389, SMB 445, WinRM 5985, Hyper-V `vmms` 2179, RPC 135 | 0.0.0.0 | Windows |

## 3. Genesis uchun xulosalar (keyingi qadamlarga)

1. **W0-03:** serverda allaqachon PostgreSQL 17 servisi bor (5432, C: da, CEO agent ishlatadi). Hal qilindi (D-21): Genesis uchun shu dastur bilan alohida klaster `E:\pgdata`, servis `genesis-postgresql`, port 5433. Eski servisga tegilmadi.
2. **W0-06:** GPU da `camsvc.service` 24/7 turibdi. `coresident` (D-14) hisobida shu yuk ham bor. Uni o'chirish Q20 ga bog'liq.
3. **W4-02:** kameraga eski tizimdan 2 ulanish bor. Yangi yozuvchi qo'shilsa 3–4 ulanish bo'ladi (kamera chegarasi tekshirilsin). Eski yozuvchi `E:\Kamera raw data` ga, yangisi `E:\data\camera` ga yozadi (D-18).
4. **W1-01:** eski bot guruhlarda admin sifatida o'qiydi; ikkita Telegram o'quvchi bir vaqtda ishlashi mumkin, lekin bitta `.session` ikki joyda ishlatilmaydi (STACK taqiqi). Q19.
5. **W0-07:** eski komponentlarning hech biri avtostartda emas (bot, dashboard, kamera, Ollama).
