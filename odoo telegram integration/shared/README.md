# shared — umumiy kod (faqat W0 o'zgartiradi)

## genesis_common: sozlama, sirlar, loglar

O'rnatish (oynaning venv'ida, yo'l qo'shtirnoqda):

```powershell
& ".venv\Scripts\python.exe" -m pip install -e "D:\Demos\odoo telegram integration\shared"
```

Ishlatish:

```python
from genesis_common import load_config, read_secret, read_secret_env, setup_logging

cfg = load_config("w1-tg-ingest")          # config.toml + config.local.toml + apps/w1-tg-ingest/app.toml -> cfg["app"]
log = setup_logging("w1-tg-ingest", cfg)   # var/logs/w1-tg-ingest/w1-tg-ingest.jsonl (JSON qatorlar, kunlik aylanish)
media_dir = cfg.path("paths.media")        # E:\data\media
token = read_secret("reporter_bot.txt", cfg)   # secrets/reporter_bot.txt; qiymat loglarda *** bo'ladi
db = read_secret_env("postgres.env", cfg)      # KEY=VALUE fayl -> dict
log.info("started", extra={"job_id": 1})
```

Qoidalar:
- `config.toml` (loyiha ildizida) — sirsiz, git'da. Oynaga xos sozlama `apps/<oyna>/app.toml` da (oynaning o'zi yozadi). Lokal o'zgartirish `config.local.toml` (git'ga tushmaydi).
- Sir faqat `secrets/` da yoki `GENESIS_SECRET_<NOM>` muhit o'zgaruvchisida (`camera1.txt` -> `GENESIS_SECRET_CAMERA1_TXT`). Kodga, logga, repoga yozilmaydi.
- Ildiz avtomatik topiladi (`config.toml` + `.genesis` bor papka) yoki `GENESIS_ROOT` dan.
- Log yozuvida ro'yxatdan o'tgan sir qiymatlari, `rtsp://user:pass@`, bot tokenlari, `password=`/`token=` qiymatlari `***` ga almashtiriladi.

Testlar (loyiha ildizidan):

```powershell
python -m unittest discover -s "shared/tests" -v
```

## genesis_queue: ish navbati (W0-05)

`core.jobs` ustida: `enqueue` (idempotency kaliti), `claim` (SKIP LOCKED, tur/gpu_class/ustuvorlik, lease), `heartbeat`,
`complete`/`fail` (eksponensial qayta urinish, 5 dan keyin `dead`), `requeue_dead`, `run_worker` sikli, CLI
`python -m genesis_queue status|dead|requeue`. Qoidalar, job turlari va misollar: `SCHEMA.md` §4a.
O'rnatish `pip install -e` bilan (yuqorida), psycopg 3 ham o'rnatiladi.

## genesis_service: servislar (W0-07)

Task Scheduler (S4U, session 0) + har dastur uchun runner: avtostart, xatoda qayta ishga tushirish, loglar, toza
to'xtatish. W0 servislari `genesis_service/services.toml` da (`ollama`, `gpu_gate`). Yo'riqnoma: `SERVICES.md`.
Sinovlar: `genesis_service/tests/run_service_tests.py` (sinov servislari), `run_w0_acceptance.py` (haqiqiy W0 servislari).
