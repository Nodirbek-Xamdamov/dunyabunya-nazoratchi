# PostgreSQL backup / restore (P0-4)

## Avtomatik kunlik backup

`backup_db.ps1` `.env`dagi `DATABASE_URL`ni o'qib, `pg_dump -F c` (custom
format, siqilgan, `pg_restore` bilan tiklanadigan) orqali
`D:\Backups\dunyabunya_db\dunyabunya_agent_db_<timestamp>.dump` fayliga
zaxira yozadi va 14 kundan eskirgan nusxalarni o'chiradi.

Windows Task Scheduler orqali har kuni soat 03:00 da avtomatik ishga
tushadi (`dunyabunya_db_backup` vazifasi — `setup_backup_task.ps1` orqali
bir marta ro'yxatga olingan, 2026-09-22).

Qo'lda ishga tushirish:
```powershell
powershell -ExecutionPolicy Bypass -File "D:\Demos\dunyabunya ceo agent\scripts\backup_db.ps1"
```

Holatni tekshirish:
```powershell
Get-ScheduledTaskInfo -TaskName dunyabunya_db_backup
```

## ⚠️ Hali qilinmagan: serverdan tashqarida saqlash

Backuplar hozircha shu SERVERNING O'ZIDA (`D:\Backups\...`) saqlanadi —
bu disk/server halokati holatida yetarli EMAS (spec: "fayllarni bot
serveridan boshqa joyda saqlash"). Buni to'liq bajarish uchun quyidagilardan
BIRINI tanlash va sozlash kerak (bunga tashqi hisob/kalitlar kerak, shuning
uchun avtomatik amalga oshirilmadi):
- Bulut saqlash (masalan S3-mos, Google Drive, Yandex Disk) — backup
  script oxiriga yuklab qo'yish qadamini qo'shish;
- Boshqa jismoniy server/NAS'ga tarmoq orqali nusxalash (masalan `robocopy`
  yoki `rsync`).

## Restore (tiklash) — tekshirilgan jarayon

2026-09-22 da sinovdan o'tkazildi:

1. **Fayl yaxlitligi tekshiruvi** (haqiqiy bazaga ulanmasdan) — muvaffaqiyatli:
   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\pg_restore.exe" --list <backup-fayli>.dump
   ```
   119 ta TOC yozuvi (barcha jadvallar: managers, employees, conversations,
   group_messages, task_logs, topics va h.k.) to'g'ri o'qildi.

2. **To'liq jismoniy restore (yangi bo'sh bazaga)** — SINALMADI: ilova
   o'zining DB hisobi (`dunyabunya_agent_user`, `.env`dagi) bilan yangi baza
   yarata olmaydi (`CREATEDB` huquqi yo'q — bu ataylab shunday, xavfsizlik
   uchun yaxshi amaliyot). To'liq restore sinovini bajarish uchun
   **PostgreSQL superuser (`postgres`) paroli** kerak — bu `.env`da yo'q.
   Superuser kalit berilsa, quyidagi buyruqlar bilan haqiqiy restore
   sinaladi:
   ```powershell
   $env:PGPASSWORD = "<postgres superuser paroli>"
   & "C:\Program Files\PostgreSQL\16\bin\createdb.exe" -h localhost -U postgres dunyabunya_restore_test
   & "C:\Program Files\PostgreSQL\16\bin\pg_restore.exe" -h localhost -U postgres -d dunyabunya_restore_test <backup-fayli>.dump
   # Tekshirish: jadvallar va qator soni to'g'ri kelishini solishtirish
   & "C:\Program Files\PostgreSQL\16\bin\dropdb.exe" -h localhost -U postgres dunyabunya_restore_test
   ```

Haqiqiy avariya holatida yuqoridagi buyruqlar (test bazasi o'rniga real
bazaga, avval eski bazani boshqa nomga o'zgartirib) qo'llaniladi.
