# pg — Genesis PostgreSQL klasteri (W0-03)

Klaster: `E:\pgdata`, servis `genesis-postgresql` (NetworkService, Auto), port 5433, faqat localhost, SCRAM (D-18, D-21).
Dastur: mavjud PostgreSQL 17.11 (`C:\Program Files\PostgreSQL\17\bin`). Eski klaster (`postgresql-x64-17`, port 5432) ga tegilmaydi.

| Fayl | Vazifasi |
|---|---|
| `genesis.conf`, `pg_hba.conf` | sozlamalar manbasi; `init_cluster.ps1` ularni `E:\pgdata` ga nusxalaydi |
| `init_cluster.ps1` | initdb (UTF8, ICU `und`, checksumlar), ACL, servis. Qayta ishga tushirsa sozlamani yangilaydi |
| `pgsecrets.py` | `secrets/postgres.env` va `secrets/pgpass.conf` ni yaratadi (qiymatlarni chiqarmaydi) |
| `setup_db.py` | rollar (CONTRACTS §5), `genesis` bazasi, `core`/`api` sxemalari. Parol serverga faqat SCRAM verifier bo'lib boradi |
| `roles.py` | ruxsat xaritasi; W0-04 migratsiyadan keyin `grants_sql()` ni chaqiradi |
| `role_test.py` | salbiy/ijobiy ruxsat testlari vaqtinchalik bazada (`genesis_roletest`, oxirida o'chiriladi) |
| `backup.ps1`, `register_backup_task.ps1` | kunlik `pg_dump` → `F:\genesis\backup` (14 ta), vazifa `genesis_pg_backup` 02:30 |
| `restore_test.py` | eng yangi dump'ni `genesis_restore_test` ga tiklaydi, solishtiradi, o'chiradi |
| `move_wal.ps1` | `pg_wal` ni `D:\pgwal` ga ko'chiradi, `E:\pgdata\pg_wal` junction bo'ladi (D-22). Bajarilgan: 2026-10-26 |
| `pg_watchdog.ps1`, `register_watchdog_task.ps1` | vazifa `genesis_pg_watchdog` (har daqiqa + boot, S4U Highest): servis to'xtasa qayta ishga tushiradi. `pg_ctl` postmaster qulashini Windows'ga oddiy to'xtash deb bildiradi, shuning uchun SCM qoidalari yetmaydi |
| `fsync/pg_test_fsync_{E,D,D_pgwal}.txt` | `pg_test_fsync -s 3` natijalari |
| `migrate.py` (W0-04) | migratsiyalar `../migrations/V<NNN>__*.{up,down}.sql`: `status`, `up`, `down`; `genesis_migrator` bilan, checksum, keyin `roles.grants_sql()` |
| `schema_test.py` (W0-04) | vaqtinchalik bazada: oy chegarasi, DEFAULT partition, retention, manifest importi, W3 view'lari |
| `archive_month.py` (W0-04) | oylik arxiv `archive` / `restore` (G:), SCHEMA.md §4 |
| `gen_schema_doc.py` (W0-04) | `shared/SCHEMA.md` ni bazadan qayta yozadi |
| `tighten_acl.ps1` | `secrets\`, `F:\genesis\backup`, shu papka: faqat SYSTEM, Administrators, admin foydalanuvchi; `-Restore` eski ACL ni qaytaradi (SECURITY.md) |

**Rejali to'xtatish** (texnik ish): avval `var\run\pg_maintenance.flag` faylini yarating, aks holda watchdog servisni bir daqiqa ichida qayta ishga tushiradi. Ish tugagach faylni o'chiring.

Buyruqlar (yo'llar qo'shtirnoqda):

```powershell
& "D:\Demos\odoo telegram integration\apps\w0-core\pg\init_cluster.ps1"
cd "D:\Demos\odoo telegram integration\apps\w0-core\pg"
python -E setup_db.py
python -E role_test.py
Start-ScheduledTask -TaskName genesis_pg_backup
python -E restore_test.py
```

Ilovalar ulanishi: `127.0.0.1:5433`, baza `genesis`, rol o'z oynasiniki, parol `genesis_common.read_secret_env("postgres.env")["<ROL>_PASSWORD"]`.
