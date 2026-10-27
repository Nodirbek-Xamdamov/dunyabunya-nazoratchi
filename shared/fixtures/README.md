# shared/fixtures — sun'iy sinov ma'lumoti (W0)

Haqiqiy xabar, odam va kamera kadri yo'q. Faqat sinov bazalari uchun (`genesis` ga yuklashni rad etadi).

```powershell
cd "D:\Demos\odoo telegram integration"
python -E shared/fixtures/load_fixtures.py                       # genesis_test, kunlar: kecha va bugun
python -E shared/fixtures/load_fixtures.py --anchor 2026-10-26    # aniq kunlar (takrorlanadigan test)
```

- `w3_demo.py` — ma'lumot ta'rifi va SQL generatori (deterministik).
- `load_fixtures.py` — bazani yaratadi (yo'q bo'lsa), migratsiyalarni qo'llaydi, ma'lumotni qayta yuklaydi (idempotent), barmoq izi chiqaradi.
- Tarkibi va W3 ulanishi: `shared/SCHEMA.md` §8a.
