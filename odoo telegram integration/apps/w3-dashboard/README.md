# W3 dashboard

Prototip `dashboard\` ning nusxasi (D-15). Dizayn o'sha, ma'lumot manbasi Genesis PostgreSQL ning `api` view'lari.
AI chat, agentlar va agent botlari (D-16), eski kamera sahifalari (D-17) bu yerga ko'chirilmagan.
Ishlab turgan prototip (`dashboard\`, port 8502) ga tegilmaydi.

## Ishga tushirish

```powershell
powershell -ExecutionPolicy Bypass -File "apps\w3-dashboard\run_dashboard.ps1"         # http://127.0.0.1:8503
powershell -ExecutionPolicy Bypass -File "apps\w3-dashboard\run_dashboard.ps1" -Stop
```

Faqat `127.0.0.1`. Loglar: `logs\dashboard.err.log`. Muhit: shu papkadagi `.venv` (`requirements.txt` + `pip install -e ..\..\shared`).

## Sozlamalar

`app.toml` (sirsiz): baza nomi (`[db] database`, hozir `genesis_test`), port, kesh, SLA, xavf bali. Istalgan kalitni
`W3_<KALIT>` muhit o'zgaruvchisi bilan almashtirish mumkin (masalan `W3_DATABASE`). Server manzili/porti `config.toml` `[postgres]`.
Parol: `secrets\postgres.env` dagi `W3_DASHBOARD_PASSWORD`, faqat ulanish paytida `genesis_common.read_secret_env` bilan o'qiladi.

## Ma'lumot qatlami (W3-02)

- `core/db.py`: `w3_dashboard` roli, sessiya `default_transaction_read_only=on`, `search_path=api`, `statement_timeout`;
  `Session` faqat `SELECT`/`WITH` qabul qiladi; `df(..., limit, offset)` — sahifalash.
- `core/queries.py`: barcha SQL, faqat `api.v_*`. Natija ustunlari prototipdagidek, shuning uchun sahifalar o'zgarmagan.
  Prototip tushunchalari qanday xaritalangani — fayl boshidagi izoh va `MAPPING.md`.
- View'i yo'q ko'rsatkichlar bo'sh qaytadi ("—"): saqlangan `daily_stats` qatorlari (hisobot bilan moslik),
  hisobot yuborilgan vaqt, tasniflab bo'lmagan xabarlar soni.

## Testlar

```powershell
cd "apps\w3-dashboard"; .venv\Scripts\python.exe -E -m pytest
```

`genesis_test` ga ulanadi (W0 fixtures: `python -E shared/fixtures/load_fixtures.py`). `tests/test_data_layer.py` —
yozishni rad etish, faqat `api`, sahifalash, fixtures raqamlari.
