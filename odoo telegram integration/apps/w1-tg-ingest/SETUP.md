# W1 · Foydalanuvchi qiladigan qadamlar (D-36)

Genesis **mavjud (eski) botning tokenidan** foydalanadi. Yangi bot ochilmaydi, BotFather'da hech narsa o'zgartirilmaydi.
Tokenni, `api_hash` ni chatga, hujjatga yoki loyiha papkasidagi boshqa joyga **yozmang**. Faqat quyidagi fayllarga.
Papka: `D:\Demos\odoo telegram integration\secrets\` (faqat administrator ochadi).

## 1. Tokenni faylga qo'yish

1. Bloknot (Notepad) ni **administrator sifatida** oching.
2. Eski botning tokenini bitta qatorga qo'ying: faqat `123456789:AA...` ko'rinishidagi token. `BOT_TOKEN=` kabi nom,
   bo'sh joy va qo'shtirnoq bo'lmasin.
3. Saqlang: `D:\Demos\odoo telegram integration\secrets\w1_bot_token.txt` (fayl turi "Barcha fayllar", kodlash UTF-8).

Agent eski botning `.env` faylini o'qimaydi: tokenni siz o'zingiz ko'chirasiz.

## 2. Ilova kaliti (my.telegram.org) — MTProto ulanishi uchun (CONNECTION.md §1)

1. Brauzerda **https://my.telegram.org** → telefon raqami bilan kiring (kod Telegramga keladi).
2. **API development tools** → ilova yarating: App title `Genesis`, Short name `genesis`, Platform `Desktop`.
3. `App api_id` (raqam) va `App api_hash` (32 belgili) chiqadi.
4. Bloknotda (administrator) shu ikki qatorni yozing va
   `D:\Demos\odoo telegram integration\secrets\telegram_api.env` deb saqlang:
   ```
   TG_API_ID=1234567
   TG_API_HASH=0123456789abcdef0123456789abcdef
   ```
   Bu akkauntga kirish emas: akkaunt tizimga ulanmaydi, kalit faqat bot ulanishi uchun ilova identifikatori.

## 3. Agentga xabar

Chatga faqat **"token qo'ydim"** deb yozing. Agent fayllar bor va formati to'g'riligini qiymatini ko'rsatmasdan tekshiradi,
keyin `getMe` (bot ma'lumoti) va bitta MTProto ulanishini qiladi (`secrets\w1_bot.session` bir marta yaratiladi).

## 4. Qabul sinovi: 3 ta sinov guruhi

Bot guruhlarda allaqachon admin: guruhga qo'shish va huquq o'zgartirish **kerak emas**.

1. Agent tinglovchini ishga tushiradi va sizga aniq matn beradi.
2. Siz 3 ta sinov guruhining har biriga o'sha matnni yozasiz.
3. Eski bot shu xabarlarni olganini **8502 dashboardida** o'zingiz tekshirasiz (agent eski botga qaramaydi).
4. Agentga 3 guruh nomini va dashboard natijasini aytasiz. Eski bot birorta xabarni olmagan bo'lsa, agent Genesis
   ulanishini darhol uzadi.
5. Agent tinglovchi logidan (faqat chat id va xabar id, matn emas) Genesis ham xabarlarni olganini ko'rsatadi.
   Sinov guruhlari `core.sources` da `enabled = true` qilinadi; ro'yxatdan tashqari guruh xabari o'qilmaydi.
