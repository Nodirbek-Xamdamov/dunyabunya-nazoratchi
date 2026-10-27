# SERVICES — Genesis dasturlarini servis qilish (W0-07)

Bitta vosita: `shared/genesis_service` (W0 venv'ida o'rnatilgan). Har servis = bitta Task Scheduler vazifasi
`genesis_<nom>` + uni kuzatuvchi "runner". Sozlama TOML faylda: nomi, buyruq, ishchi papka, loglar, avtostart,
qayta ishga tushirish va kutish vaqti.

## 1. Nega NSSM emas, Task Scheduler

- Serverda NSSM yo'q. Uni o'rnatish = internetdan 2014-yilgi, yangilanmaydigan uchinchi tomon binarini olish.
- Task Scheduler Windows'ning o'zida bor. S4U vazifa parol saqlamaydi va shu serverda allaqachon ishlab turibdi
  (`genesis_pg_watchdog`, `genesis_pg_backup`, `genesis_w4_recorder`, eski botlarning watchdog'lari).
- S4U vazifa **session 0** da ishlaydi: RDP sessiyasi uzilsa ham, undan chiqilsa ham jarayonlarga ta'sir qilmaydi.
- Kamchiligi: `services.msc` da ko'rinmaydi (Task Scheduler va `genesis_service status` da ko'rinadi). Runner o'lsa,
  qayta tug'ilishi 1 daqiqagacha (dastur o'lsa — soniyalar).

## 2. Qanday ishlaydi

```
Task Scheduler  genesis_<nom>  (S4U, eng kam huquq, session 0)
  triggerlar: kompyuter yonganda + har daqiqa (runner tirik bo'lsa yangi nusxa ishga tushmaydi: IgnoreNew)
  vaqt chegarasi yo'q, xatoda qayta ishga tushirish
   └─ runner: <W0 venv python> -E -m genesis_service run <nom> --config <toml>
        ├─ dasturni ishga tushiradi; o'lsa restart_delay_s dan keyin qayta (2 baravar o'sadi, max_backoff_s gacha;
        │  reset_after_s dan uzoq ishlagan bo'lsa qaytadan restart_delay_s)
        ├─ stdout/stderr -> var\logs\svc-<nom>\stdout.log, stderr.log (log_max_mb da aylanadi, log_keep ta; sirlar ***)
        ├─ o'z hodisalari -> var\logs\svc-<nom>\svc-<nom>.jsonl
        ├─ holat -> var\run\services\<nom>.json (runner/dastur PID, session, qayta ishga tushishlar, oxirgi chiqish kodi)
        └─ Windows job object: runner o'lsa, dasturi va uning bolalari ham o'ladi (portni egallab qolgan "yetim" bo'lmaydi)
```

To'xtatish: `stop` buyrug'i `var\run\services\<nom>.stop` faylini yaratadi. Dastur `GENESIS_STOP_FILE` muhit
o'zgaruvchisidagi faylni ko'rib, o'zi toza chiqishi mumkin (tavsiya). Chiqmasa, `stop_timeout_s` dan keyin butun jarayon
daraxti o'chiriladi. Stop fayli turguncha servis to'xtagan holda qoladi, kompyuter qayta yonsa ham. `start` faylni o'chiradi.

## 3. Buyruqlar

```powershell
cd "D:\Demos\odoo telegram integration"
$py = "apps\w0-core\.venv\Scripts\python.exe"
& $py -E -m genesis_service list                                   # W0 servislari
& $py -E -m genesis_service status                                 # hammasi (JSON qatorlar)
& $py -E -m genesis_service install <nom>                          # vazifani yaratadi va ishga tushiradi
& $py -E -m genesis_service stop|start|restart <nom>
& $py -E -m genesis_service logs <nom> --stream stderr|stdout|runner --lines 50
& $py -E -m genesis_service uninstall <nom>                        # loglar qoladi
# boshqa oynaning fayli bilan:
& $py -E -m genesis_service status --config apps\w4-camera\service.toml
```
Vazifa yaratish/o'chirish administrator huquqli PowerShell dan qilinadi (S4U vazifa shuni talab qiladi).

## 4. W0 servislari (`shared/genesis_service/services.toml`, o'rnatilgan)

| Servis | Vazifa | Dastur | Izoh |
|---|---|---|---|
| `ollama` | `genesis_ollama` | `ollama.exe serve` | faqat `127.0.0.1:11434`; tray ilova ishlatilmaydi, shuning uchun Ollama o'zini yangilamaydi (0.33.3 qotirilgan; yangilash — W0 qarori bilan). Modellar `C:\Users\Администратор\.ollama\models` (26.2 GB) |
| `gpu_gate` | `genesis_gpu_gate` | `python -m genesis_gpu run` | D-30: timeslice / coresident / dedicated; stop faylini o'zi ko'radi; xato siklida faqat `none` e'lon qiladi |
| `scheduler` | `genesis_scheduler` | `python -m genesis_ops run` | W0-08: signallar (`core.alerts`), `daily_close`, `camera_day_batch` 22:10, partitionlar, retention (hozircha dry-run); stop faylini o'zi ko'radi |

**Ollama ilovasini (Start menyudagi "Ollama") ishga tushirmang:** u ikkinchi serverni ochishga urinadi va yuklab
qo'yilgan yangilanishni o'rnatib yuborishi mumkin. Ollama faqat `genesis_ollama` servisi orqali ishlaydi.

## 5. Boshqa oynalar uchun (W0 ro'yxatdan o'tkazmaydi)

Har oyna o'z papkasida `service.toml` yozadi va o'zi `install` qiladi. Nom butun serverda yagona bo'lsin: oyna
prefiksi bilan. Dastur o'z oynasining venv'i bilan ishga tushadi (P-03); runner esa doim W0 venv'ida.

```toml
# apps/w4-camera/service.toml (namuna, W4 o'zi yozadi)
[services.w4_recorder]
description = "W4-02: kamera 102 -> E:\data\video, 07:00-22:00"
command = ['{root}\apps\w4-camera\.venv\Scripts\python.exe', '-m', 'w4cam.recorder']
cwd = '{root}\apps\w4-camera'
stop_timeout_s = 20          # ffmpeg oxirgi bo'lakni yopishi uchun; dastur GENESIS_STOP_FILE ni ko'rsa yaxshi
restart_delay_s = 5
```

| Oyna | Taxminiy servislar | Eslatma |
|---|---|---|
| W1 | `w1_listener` | bitta poller (bir token bilan ikkita getUpdates to'qnashadi) |
| W2 | `w2_worker_llm`, `w2_worker_stt` ... | ishchilar soni = servislar soni; GPU joblarni gate boshqaradi |
| W3 | `w3_dashboard` (Streamlit 8503), `w3_reporter` | |
| W4 | `w4_recorder`, keyin `w4_batch` | **hozirgi `genesis_w4_recorder` vazifasiga tegilmadi** (10-27 kalibrovka kuni). O'tkazish W4 qarori bilan, yozuv oynasidan tashqarida: avval eski vazifa o'chiriladi, keyin `install`. Eslatma: eski vazifaning kunlik triggerida `StopAtDurationEnd=true` — kuniga bir marta (06:55) ishlab turgan nusxani to'xtatadi |

Tekshiruv ro'yxati (har yangi servis): `install` → `status` (session 0, running) → dasturni o'ldirib qayta tug'ilishini
ko'rish → `stop` (toza chiqdimi, `stop_timeout_s` yetdimi) → `start` → `logs`.

## 6. Eski loyiha jarayonlari (tegilmadi; 2026-10-27 holati, faqat o'qildi)

| Nima | Qanday ishlaydi | Server qayta yonganda |
|---|---|---|
| PostgreSQL 17, port 5432 (`postgresql-x64-17`) | Windows servisi, Auto | o'zi ko'tariladi |
| Memurai (Redis, STT uchun) | Windows servisi, Auto | o'zi ko'tariladi |
| CEO agent (`dunyabunya_bot_watchdog`) | S4U vazifa: yonganda + har daqiqa | o'zi ko'tariladi |
| CEO agent baza zaxirasi (`dunyabunya_db_backup`, 03:00) | S4U vazifa | ishlaydi |
| `dunyabunya_cloudflare_tunnel` | SYSTEM, yonganda | ishga tushadi (natijasi tekshirilmagan) |
| telegram-odoo-bot, fleet_bot, Hisoblovchi | S4U watchdog vazifalari: yonganda + har daqiqa | o'zi ko'tariladi |
| Eski monitoring boti (`odoo telegram integration`, `python -m app.bot`) | qo'lda, RDP sessiyasida (session 2) | **ko'tarilmaydi** |
| Eski kamera servisi (`camsvc.service`, `camsvc.live_relay`, `camsvc.recorder` -> `E:\Kamera raw data`) | qo'lda, session 2 | **ko'tarilmaydi** |
| Eski CEO dashboard (Streamlit 8502) va `agent_bots.py` | qo'lda, session 2 | **ko'tarilmaydi** |
| W3 dashboard (Streamlit 8503, Genesis) | qo'lda, session 2 | **ko'tarilmaydi** (W3-08 da servis bo'ladi) |
| Genesis: `genesis-postgresql` + `genesis_pg_watchdog`, `genesis_pg_backup`, `genesis_w4_recorder` | servis / S4U vazifalar | o'zi ko'tariladi |
| Genesis: `genesis_ollama`, `genesis_gpu_gate`, `genesis_scheduler` (W0-07, W0-08) | S4U vazifa + runner | o'zi ko'tariladi (sinov: 7-band) |

**Diqqat:** session 2 dagi jarayonlar RDP dan **chiqib ketilsa (Sign out)** o'chadi. RDP oynasini shunchaki
yopish (disconnect) ularni o'chirmaydi. Server qayta yonsa, ular qo'lda ishga tushirilishi kerak.

## 7. Server qayta ishga tushirish sinovi (foydalanuvchi ruxsati bilan, hali QILINMAGAN)

Vaqt: kamera yozuv oynasidan tashqarida (22:00 dan keyin yoki 07:00 dan oldin) va W4 bilan kelishib.
1. Oldin: `genesis_service status`, `python -m genesis_gpu state --db genesis`, eski jarayonlar ro'yxati (6-band),
   `Get-ScheduledTask genesis_*` (LastRunTime).
2. `Restart-Computer` (foydalanuvchi bajaradi yoki ruxsat beradi). Qayta yongach **5 daqiqa RDP ga kirmay** turish.
3. Keyin kirib tekshirish:
   - `genesis_ollama`, `genesis_gpu_gate` LastRunTime ≈ yonish vaqti, `status`: running, session 0, `child_started_at`
     birinchi foydalanuvchi kirishidan **oldin** (kirish vaqti: Security log 4624 yoki `explorer.exe` boshlanishi);
   - `127.0.0.1:11434` tinglayapti, tashqi manzilda yo'q; gate e'loni yangi, Telegram oynasida Gemma yuklangan;
   - `genesis-postgresql` va watchdog, `genesis_w4_recorder` (yozuv oynasida bo'lsa yangi bo'lak);
   - qaysi eski jarayon ko'tarilmagani (6-band jadvali bilan solishtirish) va kim ularni qo'lda yoqadi.
4. Natija `logs/W0.md` ga; shundan keyin W0-07 `done`.

## 8. RDP uzilishi (foydalanuvchi qo'lda tekshirishi mumkin, 3 daqiqa)

`status` dagi `child_pid`, `child_started_at`, `restarts` ni yozib oling → RDP oynasini yoping (Sign out EMAS) →
2 daqiqadan keyin qayta ulaning → `status`: o'sha PID va vaqt, `restarts` o'zgarmagan, `logs gpu_gate --stream stdout`
da uzilish yo'q. Texnik isbot (W0-07 da o'lchangan): runner, dastur va `llama-server` session 0 da, RDP esa session 2.
