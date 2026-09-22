# PostgreSQL backup / restore (P0-4)

## Avtomatik kunlik backup

`backup_db.ps1` `.env`dagi `DATABASE_URL`ni o'qib, `pg_dump -F c` (custom
format, siqilgan, `pg_restore` bilan tiklanadigan) orqali
`E:\Backups\dunyabunya_db\dunyabunya_agent_db_<timestamp>.dump` fayliga
zaxira yozadi va 14 kundan eskirgan nusxalarni o'chiradi.

Windows Task Scheduler orqali har kuni soat 03:00 da avtomatik ishga
tushadi (`dunyabunya_db_backup` vazifasi — `setup_backup_task.ps1` orqali
bir marta ro'yxatga olingan, 2026-09-22).

**2026-09-22 yangilanishi:** server PostgreSQL 17'ga o'tkazildi (eski
16-versiya xizmati butunlay olib tashlangan, `dunyabunya_agent_user`/
`dunyabunya_agent_db` yo'qolgan edi) — qayta yaratildi va eski backup'dan
tiklandi (pastga qarang). `backup_db.ps1` endi PostgreSQL 17'ning
`pg_dump.exe`sini ishlatadi va E: diskka yozadi (D: bilan bir xil jismoniy
diskda emas — CEO ko'rsatmasi bo'yicha, "serverdan boshqa joyda saqlash"
talabini qisman yaxshilaydi, garchi hamon SHU SERVERNING o'zida bo'lsa ham).

Qo'lda ishga tushirish:
```powershell
powershell -ExecutionPolicy Bypass -File "D:\Demos\dunyabunya ceo agent\scripts\backup_db.ps1"
```

Holatni tekshirish:
```powershell
Get-ScheduledTaskInfo -TaskName dunyabunya_db_backup
```

## ⚠️ Hali qilinmagan: haqiqiy serverdan tashqarida saqlash

Backuplar E: diskda — bu D: bilan solishtirganda alohida jismoniy disk
(taxminan 1.8TB, boshqa hajm/volume ID), lekin baribir SHU SERVERNING
o'zida. Server butunlay ishdan chiqsa (yong'in, o'g'irlik va h.k.), bu ham
yordam bermaydi. To'liq bajarish uchun quyidagilardan BIRI kerak (tashqi
hisob/kalitlar kerak, shuning uchun hali avtomatik amalga oshirilmadi):
- Bulut saqlash (masalan S3-mos, Google Drive, Yandex Disk) — backup
  script oxiriga yuklab qo'yish qadamini qo'shish;
- Boshqa jismoniy server/NAS'ga tarmoq orqali nusxalash (masalan `robocopy`
  yoki `rsync`).

## Restore (tiklash) — TO'LIQ tekshirilgan (2026-09-22)

PostgreSQL 17'ga o'tish paytida bu haqiqiy avariya tiklash stsenariysining
o'zi bo'ldi (rejalashtirilgan test emas) — natija:

1. **Fayl yaxlitligi tekshiruvi** (`pg_restore --list`) — muvaffaqiyatli,
   119 TOC yozuvi to'g'ri o'qildi (avvalroq, eski 16-versiyada sinalgan).

2. **To'liq jismoniy restore (yangi bazaga, real ma'lumot bilan)** —
   MUVAFFAQIYATLI bajarildi va tekshirildi:
   ```powershell
   $env:PGPASSWORD = "<postgres superuser paroli>"
   & "C:\Program Files\PostgreSQL\17\bin\psql.exe" -h localhost -U postgres -c `
     "CREATE ROLE dunyabunya_agent_user WITH LOGIN PASSWORD '<parol>';"
   & "C:\Program Files\PostgreSQL\17\bin\psql.exe" -h localhost -U postgres -c `
     "CREATE DATABASE dunyabunya_agent_db OWNER dunyabunya_agent_user;"
   & "C:\Program Files\PostgreSQL\17\bin\pg_restore.exe" -h localhost -U postgres `
     -d dunyabunya_agent_db --no-owner --role=dunyabunya_agent_user <backup-fayli>.dump
   ```
   Tekshiruv natijasi (`dunyabunya_agent_user` hisobi bilan, ilovaning
   o'zi ishlatadigan aynan shu kirish huquqi bilan): barcha 15 jadval
   mavjud va to'g'ri egalik qilingan, qatorlar soni mos (topics=9,
   employees=62, task_log=9 — restore vaqtidagi haqiqiy hajm).

Xulosa: backup fayllari HAQIQATDA tiklanadigan ekan — nazariy emas, amalda
tasdiqlangan.
