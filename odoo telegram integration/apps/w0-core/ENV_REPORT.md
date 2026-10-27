# ENV_REPORT — muhit auditi va GPU smoke-test (W0-01)

Sana: 2026-10-26 · Bajardi: W0 Core agenti · Testlar: `apps/w0-core/smoke/`

Muhim: barcha testlar **jonli tizim ishlab turgan paytda** o'tkazildi. Shu paytda serverda `camsvc.service` (YOLO, GPU), `camsvc.recorder`, `camsvc.live_relay`, Streamlit dashboard (8502) va bir nechta Telegram botlar ishlab turgan edi. Raqamlar shu fon yuklamasi bilan olingan, hech qaysi jarayon to'xtatilmagan.

## 1. Server

| Nima | Qiymat |
|---|---|
| OS | Windows Server 2019 Standard, 10.0.17763 (64-bit), rus tilidagi lokal |
| CPU | Intel Core i7-14700K: 20 yadro (8P + 12E), 28 oqim |
| RAM | 64 GB DDR5-4800 (2 × 32 GB Team Group). Test paytida bo'sh: ~53 GB |
| GPU | NVIDIA GeForce RTX 5060 Ti, 16 GB (16311 MiB), compute capability 12.0 (Blackwell) |
| NVIDIA drayver | 616.56, CUDA UMD 13.4 (Blackwell uchun talab 12.8+ — bajarilgan) |
| CUDA Toolkit (nvcc) | o'rnatilmagan. Hozircha kerak emas: PyTorch va Ollama o'z CUDA kutubxonalari bilan keladi |
| Python | 3.12.7 (`C:\Program Files\Python312`), `py` launcher bor |
| ffmpeg | 9.0.1 essentials (`C:\ffmpeg\ffmpeg.exe`, PATH da), hwaccel: cuda, d3d11va, qsv. Yana bir nusxa: `C:\SmartVision\FFMPEG\` |
| Ollama | 0.33.3 (yangilanish 0.40.11 chiqqan, o'rnatilmadi). Avtostartda emas: sessiya boshida ishlamayotgan edi |
| llama.cpp | alohida o'rnatilmagan. Ollama ichida llama.cpp asosidagi `llama-server.exe` bor (`lib\ollama`, CUDA 13 backend) |
| git | 2.55.0 |
| Docker / WSL / Node | yo'q (P-02 ga mos) |
| RDP | yoqilgan, port 3389, NLA yoqilgan. Firewall qoidasi: **har qanday manzildan, barcha profillarda ruxsat**. Tarmoq profillari Public. W0-10 da ko'riladi |

## 2. Disklar

| Harf | Belgi | Fizik disk | Turi | Hajmi | Bo'sh | Hozirgi ishlatilishi |
|---|---|---|---|---|---|---|
| C: | System | Crucial P3 500 GB (CT500P3SSD8) | NVMe SSD | 214 GB | 97 GB | Windows, dasturlar, Ollama modellari (`C:\Users\...\.ollama`) |
| D: | Install SSD | o'sha NVMe | NVMe SSD | 251 GB | 229 GB | `D:\Demos`, `D:\Production` (mavjud loyihalar) |
| **E:** | Data | **TOSHIBA HDWT720 (2 TB)** | **HDD**, SATA | 1863 GB | 1855 GB | `Kamera raw data`, `Telegram raw data` (mavjud loyihalar yozmoqda) |
| F: | Data | WD Purple 1 TB (WD10PURZ) | HDD, SATA | 687 GB | 687 GB | bo'sh |
| G: | Support | o'sha WD Purple | HDD, SATA | 244 GB | 240 GB | `install` papkasi |

- **2 TB disk = E:**, turi HDD (Q16 javobi). Uch fizik disk, hammasi Healthy.
- Baza (PostgreSQL) uchun SSD: D: (229 GB bo'sh). Zaxira uchun boshqa fizik disk: F: (WD Purple) — E: dan alohida disk.

## 3. GPU smoke-test natijalari

| Test | Natija | Tezlik | VRAM |
|---|---|---|---|
| `nvidia-smi` | ✅ ishladi | — | jami 16311 MiB, fon ~650–700 MiB |
| GPU matmul (PyTorch 2.14.1+cu132, 8192²) | ✅ ishladi, xato fp16 3.8e-4, fp32 2.9e-6 | fp16 **45.9 TFLOPS**, fp32 16.1 TFLOPS | ~1.0 GB |
| Gemma 4 12B, **Ollama** (`gemma4:12b`, Q4_K_M, 11.9B) | ✅ 100 token | sovuq start 9.1 s yuklash; generatsiya **60–62 tok/s**, prompt 160–370 tok/s | **~9.2 GB** (ctx 8192) |
| Gemma 4 12B, **llama.cpp** (Ollama ichidagi `llama-server`, o'sha GGUF + mmproj) | ✅ 100 token | generatsiya **46 tok/s**, prompt 126–417 tok/s, yuklash ~8 s | **~9.3 GB** (ctx 8192, 4 slot) |
| Rasm kirishi (Ollama) | ✅ "INVOICE 4721", qizil doira, ko'k kvadratni to'g'ri aytdi | 135 prompt token, 1.4 s | — |
| Rasm kirishi (llama.cpp) | ✅ xuddi shunday to'g'ri | 46 tok/s | — |
| JSON-sxema bilan chiqish | ✅ ikkalasida ham `{"category":"order","confidence":0.98}` parse bo'ldi | — | — |
| YOLO (Ultralytics 8.4.183, `yolo26m.pt`) GPU da `bus.jpg` | ✅ `cuda:0`, 5 ta obyekt (bus, person) | **17 ms/kadr** (640 px) | +0.55 GB |
| YOLO `yolo26x.pt` | ✅ `cuda:0`, 5 ta obyekt | **30 ms/kadr** | +0.75 GB |
| **Birga (coresident)**: Gemma generatsiya + YOLO-m bir vaqtda | ✅ sig'di | YOLO 48 ms/kadr, Gemma 59 tok/s | eng yuqori **10.4 GB / 16.3 GB** |
| **Birga**: Gemma + YOLO-x | ✅ sig'di | YOLO 71 ms/kadr, Gemma 50 tok/s | eng yuqori **10.6 GB / 16.3 GB** |

Izohlar:
- "YOLO yolg'iz" o'lchovida Gemma VRAM da yuklangan, lekin bo'sh turgan edi; fonda jonli kamera servisi ham GPU dan foydalanayotgan edi.
- Birga ishlaganda ~5.7 GB VRAM zaxira qoladi. Ya'ni `coresident` rejimi VRAM bo'yicha mumkin, lekin YOLO 2.4–2.8 marta sekinlashadi, Gemma 5–17% sekinlashadi.
- Kontekst 8192 dan katta bo'lsa, Gemma VRAM i oshadi. Bu o'lchanmadi (W2-03 / W0-06 da).
- Ollama tezroq: modelning Ollama sozlamasida `draft_num_predict 3` bor (taxminan spekulyativ dekodlash), rasmli so'rovda 103 tok/s gacha ko'rindi.

## 4. Runtime tavsiyasi

**Tavsiya: Ollama** (W2-03 da yakuniy tasdiqlanadi).
- Tezroq (60 vs 46 tok/s), allaqachon o'rnatilgan, model allaqachon yuklab olingan.
- Rasm va JSON-sxema bilan cheklangan chiqish ishlaydi.
- Modelni yuklash/chiqarish oddiy (`keep_alive`, `ollama stop`) — W0-06 GPU gate uchun qulay.
- llama.cpp zaxira variant sifatida ishlaydi va o'sha GGUF faylni o'qiydi. Lekin bu Ollama ichidagi build (`0.3.0-dev`), rasmiy llama.cpp relizi emas. Rasmiy relizni yuklab olish uchun alohida ruxsat kerak bo'ladi.

## 5. Keyingi qadamlar uchun xavf va eslatmalar

1. **GPU allaqachon band**: mavjud `camsvc.service` (jonli kamera, YOLO26) GPU da 24/7 ishlayapti. W0-06 GPU gate shuni hisobga olishi kerak.
2. **Ollama avtostartda emas**: sessiya boshida ishlamayotgan edi. Audit paytida `ollama list` buyrug'i uni o'zi ishga tushirdi va u hozir ham ishlab turibdi. Mavjud Telegram bot (lokal `gemma4:12b`) ham Ollama ga bog'liq. W0-07 da servis qilish kerak.
3. **Ollama yangilanishi** (0.33.3 → 0.40.11) tayyor turibdi va avtomatik o'rnatilishi mumkin. Versiyani qotirish masalasini W0-07 da hal qilish kerak.
4. **RDP** hamma manzilga ochiq (firewall: Any). W0-10 da VPN yoki IP ro'yxati.
5. **E: (2 TB) HDD**: media va video bo'laklari uchun yetadi, PostgreSQL uchun emas. Baza D: (SSD) ga, zaxira F: ga tavsiya etiladi (W0-03, Q12/Q16).
6. CUDA Toolkit (nvcc) yo'q. Faqat manbadan kompilyatsiya kerak bo'lsa o'rnatiladi, hozircha shart emas.

## 6. Qayta tekshirish

Testlar uchun mavjud venv ishlatildi: `D:\Demos\odoo telegram integration\camera_service\.venv` (torch 2.14.1+cu132, ultralytics 8.4.183), hech narsa o'rnatilmadi.

```
cd apps/w0-core/smoke
$PY = "D:\Demos\odoo telegram integration\camera_service\.venv\Scripts\python.exe"
& $PY -I gpu_matmul.py
& $PY -I make_test_image.py test_image.png
& $PY -I gemma_ollama.py gemma4:12b test_image.png
& $PY -I yolo_coresident.py <yolo26m.pt yo'li> <bus.jpg yo'li>
# llama.cpp: ollama stop gemma4:12b; lib\ollama\cuda_v13 papkasidan llama-server -m <blob> --mmproj <blob> -ngl 99 -c 8192 --host 127.0.0.1 --port 8091 --jinja
& $PY -I gemma_llamacpp.py http://127.0.0.1:8091 test_image.png
```
