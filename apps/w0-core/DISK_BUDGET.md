# DISK_BUDGET — disk byudjeti (W0-03)

Sana: 2026-10-26. O'lchangan raqamlar alohida belgilangan, qolgani taxmin (Q3 javobi kelganda aniqlanadi).
Bo'sh joy (o'lchandi, 12:50): **E: 1862 GB** (foydalanuvchi aytgan 1855 GB dan biroz ko'p), F: 687 GB, G: 240 GB, D: 229 GB, C: 97 GB.

## 1. Kirish raqamlari

| Manba | Raqam | Qayerdan |
|---|---|---|
| Genesis kamera yozuvi, 102 oqim (D-20: H.265 640x360 20 fps, `-c copy`) | 35 kbit/s (VBR, kamerada chegara 256 kbit/s) → 15.8 MB/soat | o'lchandi, W4-01 (`apps/w4-camera/CAMERA_INTAKE.md`) |
| Eski yozuvchi, 101 oqim (`E:\Kamera raw data`, 07:00–23:00) | bugun 10 fayl / ~100 daqiqa = 953 MB → **~570 MB/soat ≈ 9.2 GB/kun** | o'lchandi (`du`) |
| Telegram media | eski bot xom arxivi: 3 guruh, 10-14…10-26 da 56 MB → ~1.4 MB/guruh/kun | o'lchandi; 200 guruhga ko'chirish taxmin |
| PostgreSQL matn | 200 guruh × 100 xabar/kun, xabar + tahlil + indeks ~2 KB → ~40 MB/kun | taxmin (Q3) |
| Bo'sh klaster | `E:\pgdata` = 64 MB | o'lchandi |
| pg_dump (`-Fc -Z 6`) | 1000 qatorli sinov jadvali → 28 KB | o'lchandi; katta bazada ~15–25% |

## 2. E: (2 TB HDD) — yillik byudjet

| Iste'molchi | Kuniga | Oyiga | Yiliga / barqaror holat | Izoh |
|---|---|---|---|---|
| Genesis kamera, 1 kamera, 07–23 (16 soat) | 0.25 GB | 7.6 GB | 30 kunlik bufer: **~8 GB barqaror** | D-12/D-13 aylanma bufer, muddat W0-08 da |
| (o'sha, 24 soat yoki VBR chegarasida 256 kbit/s) | 0.38 / 2.8 GB | 11 / 83 GB | 30 kun: 11–83 GB | eng yomon holat |
| Genesis kamera, 50 kamera, 16 soat (P-13, kelajak) | 12.6 GB | 380 GB | 30 kunlik bufer: ~380 GB | |
| Telegram media, 200 guruh | ~0.3 GB | ~9 GB | **~105 GB/yil** (voice/video ko'p bo'lsa ×3 = ~315 GB) | `E:\data\media\yil\oy\guruh\`, o'chirilmaydi (D-13) |
| PostgreSQL (`E:\pgdata`) | ~40 MB | ~1.2 GB | ~15 GB/yil + WAL 8–10 GB | oylik arxiv G: ga ko'chadi (D-13), bazada qolgani kamayadi |
| `E:\data\tmp` | — | — | 10 GB zaxira | vaqtinchalik |
| **Genesis jami (1 kamera)** | | | **~140 GB 1-yil oxirida** (eng yomon ~420 GB) | 1862 GB ga bemalol sig'adi |
| **Eski yozuvchi (101, `E:\Kamera raw data`)** | **9.2 GB** | **~275 GB** | **~6.7 oyda E: ni to'ldiradi (~2027 may)** | Genesis nazoratida emas: Q20 |

Xulosa:
1. Genesis o'zi 1 kamera bilan E: ning 10–20% idan kam ishlatadi. 50 kamera bo'lsa 30 kunlik bufer ~380 GB qo'shiladi, yana sig'adi.
2. **Asosiy xavf: eski 101 yozuvchi.** U kuniga ~9 GB yozadi va aylanma bufer yo'q (loyiha eslatmasiga ko'ra 50 GB dan kam joy qolsa to'xtaydi; bu tekshirilmadi). Q20 da o'chirish yoki saqlash muddati hal qilinmasa, E: taxminan 2027 may oyida to'ladi va Genesis yozuvlari ham to'xtaydi. W0-08 ogohlantirishi E: bo'sh joyini kuzatadi (chegara: 200 GB).
3. Eski bot ham `E:\Telegram raw data` ga yozadi. Hozir 3 guruh (~4 MB/kun). Agar u 200 guruhga kirsa, media ikki marta saqlanadi (+~105 GB/yil).

## 3. F: (zaxira, 687 GB) va G: (oylik arxiv, 240 GB)

| Disk | Nima | Hajm |
|---|---|---|
| F: `F:\genesis\backup` | kunlik `pg_dump`, eng yangi 14 tasi + globals | 1-yil oxirida baza ~15 GB → dump ~3 GB × 14 ≈ **45 GB** |
| G: `G:\genesis\archive` | oylik partition arxivi (siqilgan) | ~0.3 GB/oy → **~4 GB/yil** |

Ochiq nuqta: **Telegram media va kamera yozuvlari zaxiralanmaydi** (faqat E: da). E: buzilsa ular yo'qoladi, baza esa F: dan tiklanadi. Media zaxirasi kerakmi yoki yo'qmi, foydalanuvchi hal qiladi (F: da joy bor: 1-yil media ~105 GB).

## 4. D: (NVMe SSD, 229 GB)

`var\logs` (kichik), oyna venv'lari (~2–5 GB har biri). WAL `D:\pgwal` da (D-22): `max_wal_size` 8 GB + zaxira ≈ 10–12 GB.
