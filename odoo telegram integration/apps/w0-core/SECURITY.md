# SECURITY — xavfsizlik holati

W0-02 da boshlandi (2026-10-26). To'liq hisobot va tiklash sinovi W0-10 da. Bu yerda faqat topilgan xavf va qilingan sozlama yoziladi; serverning tarmoq sozlamalariga tegilmagan.

## 1. RDP (foydalanuvchi o'zi cheklaydi — Genesis tegmaydi)

- RDP yoqilgan, 0.0.0.0:3389, NLA yoqilgan (W0-01).
- Firewall qoidasi: **har qanday manzildan, barcha profillarda ruxsat**; tarmoq profillari Public.
- Xavf: server internetga ko'rinsa parol tanlash (brute-force) va RDP zaifliklari. Server administrator akkaunti bilan ishlaydi, unda barcha sirlar, Telegram sessiyalari va kamera paroli bor: RDP buzilsa hammasi ochiladi.
- Tavsiya (foydalanuvchi qiladi): RDP ni VPN orqali yoki IP ro'yxati bilan cheklash, akkaunt bloklash siyosati, kuchli parol, iloji bo'lsa MFA. W0-10 da qayta tekshiriladi.

## 2. Tarmoqqa ochiq boshqa portlar (W0-02 inventari, `LEGACY.md`)

| Port | Kim | Manzil | Izoh |
|---|---|---|---|
| 5432 | PostgreSQL 17 (eski, CEO agent) | 0.0.0.0 | Genesis bazasi faqat 127.0.0.1 da bo'ladi (W0-03) |
| 80 | Apache 2.2 | 0.0.0.0 | eski versiya, egasi aniqlansin |
| 8002 | CEO agent | 0.0.0.0 | |
| 8502 | eski dashboard | 192.168.87.2 (portproxy) | LAN dan ochiq, parol yo'q (loyiha eslatmasi) |
| 445, 5985, 135 | Windows (SMB, WinRM, RPC) | 0.0.0.0 | |

Bularga W0-02 tegmadi. Ochiqligi firewall profiliga bog'liq; W0-10 da tekshiriladi.

## 3. Agent ruxsatlari (W0-02 da qilindi)

Har bir oynada `apps/<oyna>/.claude/settings.json`:
- `additionalDirectories`: `.genesis`, `shared`.
- `deny` (Read/Edit): `secrets/**`, D: dagi barcha `.env`, `.env.bak*`, `*.session`, `*.session-journal`, E: dagi `*.session`.
- `deny` (Bash/PowerShell): buyruq matnida `secrets/`, `secrets\` yoki `.session` bo'lsa.

Sinov (2026-10-26, `logs/W0.md` da isbot): 6 oynaning hammasida `.genesis/README.md` o'qildi, `secrets/_canary_not_secret.txt` rad etildi. Nazorat: sozlamasiz papkadan (`shared/`) o'sha canary o'qildi, ya'ni rad etishni aynan deny qoidasi qiladi.

Cheklovlar (ochiq xavf):
1. **Oynadan tashqari sessiyalar himoyalanmagan.** Foydalanuvchi darajasidagi `~/.claude/settings.json` da butun loyiha ildizi `additionalDirectories` da bor. Shuning uchun `apps/wX` dan boshqa joyda ochilgan Claude sessiyasi (masalan home papkadan yoki loyiha ildizidan) `secrets/` ni o'qiy oladi. Tavsiya: o'sha deny qoidalarini foydalanuvchi darajasidagi sozlamaga ham qo'shish (foydalanuvchi qarori).
2. **Shell orqali aylanib o'tish mumkin.** Bash/PowerShell deny faqat buyruq matnini tekshiradi; o'zgaruvchi, glob yoki skript ichidan o'qishni to'xtatmaydi. Windows da Claude Code sandbox yo'q. Asosiy himoya: agentlar `CLAUDE.md` qoida 5 ga amal qiladi, sirlar faqat dastur kodi orqali o'qiladi (`shared/genesis_common`).
3. **Workspace trust.** Loyiha `.claude/settings.json` dagi `additionalDirectories` faqat loyiha (git ildizi `D:\Demos\odoo telegram integration`) "trusted" bo'lgandan keyin ishlaydi. Hozir trust qabul qilinmagan, `.genesis` ga kirish foydalanuvchi darajasidagi sozlama orqali ishlayapti. Oynani birinchi ochganda trust oynasida "ha" deyiladi. Deny qoidalari trust'siz ham ishlaydi (sinovda tasdiqlandi).
4. Deny agentlar uchun yozishni ham taqiqlaydi: yangi sir faylini foydalanuvchi yoki skript (masalan W0-03 parol generatori) yozadi, agent emas.

## 3a. PostgreSQL (W0-03)

Yangi Genesis klasteri (`genesis-postgresql`, `E:\pgdata`):
- Faqat `127.0.0.1:5433` va `[::1]:5433` da tinglaydi (`netstat` bilan tekshirildi). `pg_hba`: faqat 127.0.0.1/::1, faqat `scram-sha-256`; `trust` yo'q, LAN yo'q, replikatsiya yo'q.
- Ilova rollari superuser emas, `CREATEDB/CREATEROLE` yo'q. `PUBLIC` dan baza va sxema huquqlari olingan, `postgres` bazasiga ilova rollari ulana olmaydi. 519 ta ruxsat tekshiruvida nomuvofiqlik yo'q (`logs/W0.md`).
- Parollar serverga SCRAM verifier bo'lib yuborildi (ochiq parol server logiga tushmaydi). Zaxiradagi `globals_*.sql` parol xeshlarisiz (`--no-role-passwords`).
- `E:\pgdata` ACL: faqat Administrators, SYSTEM, NetworkService.

**Eski PostgreSQL 17 (`postgresql-x64-17`, CEO agent) — xavf, tegilmadi:**
- `0.0.0.0:5432` va `[::]:5432` da, ya'ni barcha tarmoq interfeyslarida tinglaydi.
- Firewall'da 5432 port yoki `postgres.exe` uchun ruxsat qoidasi topilmadi. Profillar Public, kiruvchi standart amal `NotConfigured` (Windows'da odatda bloklash). Lekin tashqaridan tekshirilmadi. Firewall o'chirilsa yoki keng qoida qo'shilsa, baza LAN/internetga ochiladi. Uning `pg_hba.conf` i o'qilmadi (ko'rsatma: tegilmaydi).
- Tavsiya (egasi hal qiladi): `listen_addresses = 'localhost'` yoki aniq IP, yoki firewall'da 5432 ni aniq bloklash. W0-10 da tashqi skan.

**WAL va watchdog (D-22, 2026-10-26):** `pg_wal` = `D:\pgwal` (ACL: SYSTEM, Administrators, NetworkService). Vazifa `genesis_pg_watchdog` har daqiqada **yuqori huquq bilan** (S4U, Highest) `apps\w0-core\pg\pg_watchdog.ps1` ni ishga tushiradi. Bu skriptni o'zgartira olgan odam administrator huquqiga ega bo'ladi, shuning uchun `apps\w0-core\pg` ham quyidagi ACL doirasida.

**ACL toraytirildi (2026-10-26, W0-04 qoldig'i, `apps\w0-core\pg\tighten_acl.ps1`):** `secrets\`, `F:\genesis\backup`, `apps\w0-core\pg` — meros uzildi, faqat SYSTEM (F), Administrators (F) va `AI-SERVER\Администратор` (SID …-500, Modify) qoldi; `BUILTIN\Users` (unga `AI-SERVER\User`, INTERACTIVE, Authenticated Users kiradi) olib tashlandi. Foydalanuvchi SID i alohida kerak: S4U/Limited vazifalar (`genesis_pg_backup`, `genesis_w4_recorder`) va oddiy oynadan ochilgan jarayonlarda Administrators guruhi ishlamaydi. NetworkService (`genesis-postgresql`) bu papkalarni o'qimaydi, unga ruxsat berilmagan. Tekshirildi: vaqtinchalik S4U/Limited vazifa `camera1.txt` va `postgres.env` ni o'qidi, `w3_dashboard` ulandi; `genesis_pg_backup` natija 0; `genesis-postgresql` ishlaydi; W4 yozuvchisi yozishda davom etdi. Eski ACL `var\acl_backup\*.acl` da, qaytarish: `tighten_acl.ps1 -Restore`.

## 4. Sirlar joylashuvi

- Genesis sirlari: `secrets/` (git'ga tushmaydi, tekshirildi). Kod ularni faqat `genesis_common.read_secret()` orqali o'qiydi; o'qilgan qiymat loglarda `***` bo'ladi (testda tasdiqlangan).
- Eski tizim sirlari loyiha ildizida: `.env` va 11 ta `.env.bak*`, `userbot_session.session`, `camera_service\` va `dashboard\` ning o'z `.env` lari. Ko'chirilmadi (eski tizimga tegilmaydi), git'ga tushmaydi, agentlarga deny.
- Windows ACL bilan `secrets/` ni cheklash: W0-10.
