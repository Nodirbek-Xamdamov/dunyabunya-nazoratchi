# Hisoblovchi — to'lov eslatma boti

<!-- qisqacha -->
> **Qisqacha.** To'lov eslatma boti. Guruhga tashlangan sverka rasmiga hodim ismi va to'lov sanasi reply qilinadi; bot rasmdan buyurtma raqami va mijozni o'qib oladi va to'lov kuni eslatmani shaxsiy chatga yuboradi.
>
> **Texnologiyalar:** Python, Telegram bot, OpenAI (rasm o'qish), SQLite
>
> **Ishga tushirish:** `python main.py` (sozlamalar `.env` da, namuna: `.env.example`)

Joyi: `D:\Production\Hisoblovchi` (2026-10-12 da `D:\Demos` dan ko'chirilgan).

Telegram guruhiga sverka (hujjat) rasmi tashlanadi. Rasmga **reply** qilib mijoz bilan gaplashgan hodim ismi va
to'lov sanasi yoziladi (ism oldidan `#` qo'yish shart emas):

```
Nodir, 12,10,2026 +
```

Bot rasmdan buyurtma raqamini va mijoz ismini ("Klient" / "Mijoz" yozuvi yonidagi) o'qib oladi. **Guruhda bot hech
narsa yozmaydi**: reply qabul qilinsa unga 👍, qabul qilinmasa 👎 reaksiya qo'yadi, xolos. To'lov kuni eslatmani
**guruhga emas, botga shaxsiy chatda `/start` bosganlarga** yuboradi:

```
🔔 To'lov eslatmasi

1. S88784 (Karimov Jasur) - Nodir
2. S88790 (OOO «Baraka Qurilish») - Ali
```

Har bir eslatma bir qator: buyurtma raqami (mijoz) - hodim ismi. Sana yozilmaydi: ro'yxat qaysi kuni kelsa, o'sha
kuni to'laydiganlar. (Faqat kechikib yuborilgan eslatma oxirida o'z sanasi turadi.) Buyurtma raqami — havola:
bosilsa guruhdagi o'sha rasm ochiladi (faqat superguruhlarda).

Shaxsiy chatda pastda ikkita tugma turadi:

- **📋 Bugungi** — bugun to'lov qilishi kerak bo'lganlarning hammasi (eslatilgan-eslatilmaganidan qat'i nazar,
  09:00 dan oldin ham).
- **🗓 Oraliq** — kalendar chiqadi: avval boshlanish kuni, keyin tugash kuni bosiladi; shu oraliqdagi ro'yxat sanalar
  bo'yicha chiqadi.

Ikkala ro'yxat avval chatga matn bo'lib keladi; tagidagi **📊 Excel** tugmasi bosilsa, o'sha ro'yxat Excel fayl
bo'lib keladi.

## Qachon yuboriladi

- **Bugungi sana yoki «bugun» yozilsa** — mijoz shu bugun to'layman degan: eslatma darhol emas, shu kuni
  `EVENING_AT` (standart 18:00) da yuboriladi. Reply 18:00 dan keyin yozilsa — darhol.
- **Kelgusi sana yozilsa** — o'sha kuni `REMIND_AT` (standart 09:00, Toshkent vaqti) da.
- Kutilayotgan eslatmaning sanasi yangi reply bilan bugunga ko'chirilsa, u ham 18:00 da ketadi.
- **Bot o'zi yuborgan eslatma tagida Excel tugmasi yo'q.** Excel faqat so'ralgan ro'yxat («Bugungi», «Oraliq»)
  tagida va 20 tadan uzun ro'yxatda chiqadi (pastga qarang).
- **Ro'yxat uzun bo'lsa** — bitta xabarda 20 ta eslatma chiqadi, tagida **«Davomini chiqarish»** (keyingi 20 ta) va
  **«Excel»** tugmalari. «Bugungi» va «Oraliq» ro'yxatlarida ham shunday.
- Kun davomida qo'shilgan eslatmalar raqami davom etadi (ertalab 25 ta kelgan bo'lsa, keyingisi `26.`).
- Bot o'sha kuni o'chiq bo'lgan bo'lsa, yonganida o'tib ketgan eslatmalarni ham o'z sanasi bilan yuboradi.

## Excel

Fayl buxgalteriyaning o'z jadvali ko'rinishida chiqadi (varaq nomi `Hujjat`):

| Hujjat | Sana | Hafta kuni | Mijoz | Summa | To'langan | Hodim |
|---|---|---|---|---|---|---|
| S94679 | 12.10.2026 | Dushanba | DAVRON 9896 | | ✕ | SAIDAHMAD |
| | | | **Jami:** | **0,00** | | |

`Summa` bo'sh — uni o'zlari kiritadi, tagidagi `Jami` o'zi hisoblanadi. `To'langan` har doim `✕` bo'lib chiqadi:
bot to'lov qilingan-qilinmaganini bilmaydi.

## Kimga yuboriladi

- Botga shaxsiy chatda `/start` bosgan har bir odamga, har bir eslatma bir martadan.
- Faqat hujjat tashlangan **guruhning a'zosiga**: begona odam botga `/start` bossa, bot unga "faqat guruh a'zolari
  uchun" deb javob beradi va hech narsa yubormaydi.
- Bot faqat `.env` dagi `ALLOWED_CHAT_IDS` guruhida ishlaydi (hozir bitta: «LIMIT»). Boshqa guruhga qo'shilsa, u
  yerdagi rasmlarni o'qimaydi — shunda begona guruh OpenAI hisobidan sarflay olmaydi. Yangi guruh qo'shish uchun
  uning ID'sini shu qatorga vergul bilan yozib, botni qayta yoqing.
- `/start` kunning o'rtasida bosilsa, o'sha kungi eslatmalar shu zahoti keladi.
- Hali hech kim `/start` bosmagan bo'lsa, eslatma kutib turadi va birinchi `/start` dan keyin yuboriladi.

## Buyruqlar

Guruhda (bot faqat reaksiya bilan javob beradi):

| Amal | Natija |
|---|---|
| Rasmga reply: `Nodir, 12,10,2026 +` | 👍 — eslatma saqlandi |
| Shu rasmga yana boshqa sana | 👍 — kutilayotgan eslatmaning sanasi ko'chirildi (rasm qayta o'qilmaydi) |
| Rasmga reply: `/bekor` | 👍 — kutilayotgan eslatma bekor qilindi (masalan, mijoz to'lab bo'lgan); 👎 — bekor qiladigan narsa yo'q |
| `#Ism` bor, lekin sana tushunilmadi / bunday kun yo'q / sana o'tib ketgan | 👎 |
| Sana yozilmagan (masalan faqat ism) | hech narsa — sana shart |
| Oddiy gap, yolg'iz `+`, rasm bo'lmagan xabarga reply | hech narsa |

Shaxsiy chatda (buyruqlar faqat shu yerda ishlaydi):

| Buyruq | Natija |
|---|---|
| `/start` | Eslatmalar shu chatga keladigan bo'ladi |
| 📋 Bugungi (yoki `/bugun`) | Bugun to'lov qilishi kerak bo'lganlar ro'yxati |
| 🗓 Oraliq (yoki `/oraliq`) | Ikki sana tanlanadi, shu oraliqdagi ro'yxat chiqadi |
| `/eslatmalar` | Kutilayotgan (hali kuni kelmagan) eslatmalar — siz a'zo bo'lgan hamma guruh bo'yicha |
| `/chatid` | Shu chatning ID'si |

Reply qanday yoziladi:

- Sana: `12,10,2026`, `12.10.2026`, `12/10/26`, `12.10`, `12 oktabr`, `bugun`, `ertaga`. Yil yozilmasa, eng yaqin
  kelgusi sana olinadi. Oxiridagi `+` shart emas.
- Ism — mijoz bilan gaplashgan hodim, `#` bilan ham, `#`siz ham: `Nodir, 12,10,2026 +`, `#Nodir 12.10`, yoki ism,
  sana va `+` alohida qatorlarda. `#`siz yozilganda ism (1–3 so'z) sanadan **oldin** turishi va boshqa so'z
  bo'lmasligi kerak — aks holda bot buni oddiy gap deb biladi. `#` bilan yozilsa, sana boshqa so'zlar orasida
  bo'lsa ham topiladi (`#Nodir, 12,10,2026 + to'laydi dedi`). Ism umuman yozilmasa (faqat sana), ism o'rniga reply
  yozgan odamning Telegram ismi chiqadi.
- O'tib ketgan yoki mavjud bo'lmagan sana qabul qilinmaydi (👎).
- Bitta rasm bo'yicha bitta sanaga bitta eslatma: yuborib bo'lingan sana qayta yozilsa, ikkinchisi yaratilmaydi.
- Reply tahrirlansa (edit), bot uni qayta o'qiydi: kutilayotgan eslatmaning sanasi yoki ismi tuzatiladi.
  Yuborib bo'lingan eslatmani tahrir bilan qaytarib bo'lmaydi.
- Rasmdan buyurtma raqami yoki mijoz o'qilmasa ham eslatma saqlanadi va eslatma bilan birga **rasmning o'zi**
  yuboriladi (o'qilmagan joyda "📷 rasmda" deb yoziladi).
- Guruhda bot nima o'qiganini aytmaydi, shuning uchun hammasi `bot.log` da: saqlangan eslatma (`reminder … saved:
  … order …, customer …`), rad etilgan (`refused`) va e'tiborsiz qoldirilgan (`not a picture`, `has no date`)
  reply'lar. Eslatma kelmasa, sababi shu yerdan ko'rinadi.

## O'rnatish

1. @BotFather'da bot yarating, tokenni `.env` dagi `TELEGRAM_BOT_TOKEN` ga, OpenAI kalitini `OPENAI_API_KEY` ga
   yozing.
2. Botni guruhga qo'shing va **admin qiling** (yoki BotFather → `/setprivacy` → Disable). Aks holda Telegram
   botga rasmga yozilgan reply'larni yetkazmaydi va bot kim guruh a'zosi ekanini tekshira olmaydi.
3. Botni faqat o'z guruhingizga bog'lash uchun guruh ID'sini `ALLOWED_CHAT_IDS` ga yozing (bo'sh qolsa bot
   qo'shilgan hamma guruhda ishlaydi). ID `bot.log` da ko'rinadi: `message … in chat -100… (Guruh nomi)`.

```powershell
py -3.12 -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python main.py
```

## Rasmni o'qish

Rasmni OpenAI o'qiydi (`.env`: `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-5.4-mini`) — lokal model ishlatilmaydi. Sana
yozilgan har bir rasm OpenAI'ga yuboriladi; sana yozilmagan rasmlar hech qayerga yuborilmaydi.

- Rasm yonboshlab yoki teskari olingan bo'lsa, bot uni to'g'rilab qayta o'qitadi (shunday rasmga 2–3 ta so'rov).
- Aloqa uzilsa yoki OpenAI band bo'lsa (429, 5xx), so'rov 3 martagacha takrorlanadi. Baribir o'qilmasa, eslatma
  saqlanadi va matn o'rniga rasmning o'zi yuboriladi.
- Rasmdan faqat buyurtma raqami va mijoz ismi olinadi. Sana rasmdan olinmaydi — u faqat reply'dan.
- Har bir so'rovning vaqti va token sarfi `bot.log` da: `photo read by … tokens: … in, … out`.
- Kalit production'dagi hujjat boti (`D:\Production\telegram-odoo-bot\.env`) bilan bir xil. U yerda kalit
  almashtirilsa, bu yerdagi `.env` da ham almashtirish kerak.

O'lchov (2026-10-12, hujjat botidagi 60 ta haqiqiy telefon rasmi, 11 tasi yonboshlab olingan):

| O'qish usuli | Buyurtma raqami | Mijoz | Rasmga o'rtacha |
|---|---|---|---|
| Bot hozir ishlatayotgani (`gpt-5.4-mini`, rasmni to'g'rilash bilan) | 59/60 | 59/60 | 2,7 s, 1,2 ta so'rov, ~2600 token |
| Bitta oddiy so'rov, to'g'rilashsiz | 59/60 | 53/60 | 2,1 s, 1 ta so'rov, ~2000 token |

Qayta o'lchash: `venv\Scripts\python.exe scripts\bench_reader.py 60` (xuddi shu rasmlarda; har bir o'qish — pullik
so'rov). Model yoki prompt o'zgarsa, avval shu bilan tekshiring.

## Tuzilishi

- `app/bot.py` — Telegram handlerlar, ro'yxat va tugmalar, eslatma yuboruvchi sikl (har daqiqada tekshiradi)
- `app/dates.py` — reply'dagi sana va `#Ism` ni o'qish
- `app/extract.py` — rasmdan mijoz va buyurtma raqamini o'qish (OpenAI), yonboshlagan rasmni to'g'rilash
- `app/excel.py` — ro'yxatni `.xlsx` qilish
- `app/picker.py` — «Oraliq» uchun kalendar
- `app/db.py` — eslatmalar, `/start` bosgan chatlar va kimga nima yuborilgani, SQLite `data/hisoblovchi.db`
- `scripts/watchdog.ps1` — bot o'chib qolsa qayta yoqadi (Scheduled Task uchun), log `bot.log`
- `scripts/bench_reader.py` — rasm o'qish aniqligini haqiqiy hujjat rasmlarida o'lchash

Testlar: `venv\Scripts\python -m unittest discover -s tests -t .`
