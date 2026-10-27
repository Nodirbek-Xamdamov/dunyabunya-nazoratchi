# STACK v1.0 — texnologiyalar va sabablari

Server: Windows Server, RTX 5060 Ti 16 GB, RDP orqali. Hamma narsa serverning o'zida ishlaydi (ovoz API dan tashqari).

| Qatlam | Tanlov | Sabab / eslatma |
|---|---|---|
| Til | Python 3.12, har oyna uchun alohida venv | kutubxonalar bir-biriga to'qnashmasin |
| Baza | PostgreSQL 16 (lokal) | ko'p jarayon bir vaqtda yozadi; Windows uchun oddiy o'rnatish |
| Navbat | `jobs` jadvali + `FOR UPDATE SKIP LOCKED` | Redis/RabbitMQ shart emas, bitta server |
| Embedding saqlash | `bytea` / `float4[]`, pgvector yo'q | Windows da pgvector murakkab; hodimlar soni kam |
| Telegram o'qish | Telethon (MTProto). Bot yoki userbot — W1-01 da Q4 javobiga qarab | guruh tarixi va media; yozish funksiyalari o'rovchi (wrapper) orqali o'chirilgan |
| Telegram yuborish | Alohida reporter bot (aiogram), faqat ruxsat etilgan chatga | o'qish va yozish ajratilgan |
| Lokal LLM | Gemma 4 12B (Apache 2.0), kvantlangan (QAT 4-bit), llama.cpp server yoki Ollama | 16 GB ga sig'adi; matn + rasm kirishi; runtime W0-01/W2-03 da o'lchab tanlanadi |
| Chiqish formati | JSON-sxema bilan cheklangan dekodlash (llama.cpp grammar / json_schema) | parse bo'lmaydigan javob bo'lmasin |
| Ovoz → matn | Tashqi API; provayder W2-02 bake-off da tanlanadi (ElevenLabs Scribe, AssemblyAI, Gladia, Aisha, Gemma audio) | o'zbek aniqligi haqiqiy namunalarda o'lchanadi |
| Kamera video | ffmpeg `-c copy` (RTSP sub-stream → 10 daqiqalik bo'laklar); tahlilda kerakli fps dagi kadrlar olinadi | yozish dekodlashsiz, aylanma bufer (D-12); kamera 1 fps, kalibrovkada 5 fps |
| Detektor | YOLO oilasi (Ultralytics) yoki Apache/MIT litsenziyali muqobil; W4-04 eshigida tanlanadi | Ultralytics = AGPL-3.0 yoki pullik Enterprise (P-09) |
| Tracker/ReID | past fps uchun moslangan tracker + tashqi ko'rinish embeddingi (W4-06 darajasiga qarab) | 1 fps da standart IoU-tracker zaif |
| Excel | openpyxl yoki XlsxWriter | CPU, LLM kerak emas |
| Dashboard | mavjud prototip (texnologiya Q10 / W3-01 da aniqlanadi) | dizaynni o'zgartirmaymiz |
| Servislar | NSSM (Windows service), zaxira: Task Scheduler | RDP uzilsa ham jarayon o'lmaydi |
| Vaqt | DB da UTC, biznes kun Asia/Tashkent | kun chegarasi 00:00 |
| Zaxira | `pg_dump` har kuni boshqa diskka, haftada bir restore sinovi | |
| Sirlar | `secrets/` papkasi (Windows ACL), repoga kirmaydi, agentlarga o'qish taqiqlangan | `.claude/settings.json` deny qoidalari (W0-02) |
| Ko'rinish | JSON loglar fayliga + `heartbeats` jadvali | "N daqiqa xabar kelmadi" signali |

## Taqiqlar

- Docker/WSL orqali GPU: Windows Server da ishonchsiz, ishlatilmaydi (P-02).
- Yuz va tana embeddinglari, kamera kadrlari chet el API ga yuborilmaydi.
- Telegram session fayllari repoga va loglarga tushmaydi, bitta sessiya ikki joyda bir vaqtda ishlatilmaydi.
- Modelga to'liq tarixni berish yo'q: kontekst bazadan yig'iladi (24 soatlik yig'ma xulosa + ochiq masalalar).

## Litsenziya va huquqiy nuqtalar

- Gemma 4: Apache 2.0, tijoriy foydalanish mumkin.
- YOLO (Ultralytics): ichki foydalanish uchun AGPL odatda yetadi; boshqa kompaniyalarga sotish yoki yopiq mahsulotga qo'shish rejasi bo'lsa Enterprise litsenziya yoki muqobil detektor kerak (Q2).
- Yuz/tana embeddinglari biometrik ma'lumot hisoblanishi mumkin: serverda qoladi, yozma rozilik va saqlash muddati W4-10 da hal qilinadi. Yurist tasdig'i kerak.
