# W1-01 · Ulanish usuli (qaror va asoslash)

Sana: 2026-10-26, yangilandi 2026-10-27. Qaror: **D-36** (D-25 ni o'zgartiradi) — Genesis **mavjud (eski) botning
tokenidan** foydalanadi, alohida yangi bot ochilmaydi (bot 200+ guruhda allaqachon admin). Eski bot
(`@dunyabunya_assistant_bot`, `app.bot`) ishlab turadi; uning kodi, sozlamasi, `.env`, SQLite, logi va jarayoniga
Genesis tegmaydi va ularni o'qimaydi.

## 1. Tanlov: Telethon (MTProto), bot tokeni bilan kirgan rasmiy bot

| Mezon | A. Bot API + aiogram (HTTP long polling) | B. Bot API + lokal Bot API server | **C. Telethon, bot tokeni bilan (tanlandi)** |
|---|---|---|---|
| Fayl yuklab olish chegarasi | **20 MB** | 2000 MB | **2000 MB** (MTProto orqali) |
| Qo'shimcha server | yo'q | `telegram-bot-api` (C++, Windows'da yig'ish va yangilab turish) | yo'q |
| `api_id` / `api_hash` (my.telegram.org) | kerak emas | kerak | kerak |
| Kiruvchi port / ochiq HTTPS | yo'q | yo'q | yo'q (doimiy chiquvchi ulanish) |
| Akkaunt xavfi | yo'q | yo'q | yo'q (bu bot, userbot emas) |
| O'tkazib yuborilgan xabarni ID bo'yicha olish (W1-05) | yo'q | yo'q | **bor** (supergroup) |
| W5 (DM, MTProto) bilan bir kutubxona | yo'q | yo'q | **ha** |
| STACK.md | aiogram — faqat W3 reporter boti | — | **"Telegram o'qish: Telethon (MTProto)"** |

Foydalanuvchi bilan kelishuv (2026-10-26): "Genesis arxitekturasi uchun qaysi kutubxona yaxshiroq bo'lsa o'sha". O'qish —
Telethon; yuborish (W3 reporter) va eski bot — aiogram'da qoladi, eski bot boshqa texnologiyaga ko'chirilmaydi.

Sabablar:
1. Mijoz guruhlarida qurilish videolari ko'p; 20 MB chegarasi (A) ularning bir qismini yo'qotadi. B ham, C ham 2 GB gacha
   yuklaydi, lekin B qo'shimcha C++ server (Windows'da yig'ish, yangilash, yana bir servis) talab qiladi, C esa yo'q.
2. STACK.md o'qish uchun allaqachon Telethon ni belgilagan; D-25 faqat "bot yoki userbot" savolini hal qiladi — bot.
3. Telethon botda ham guruh qoidalari bir xil (privacy, boshqa botlar, tarix — §3), ya'ni Q4 da qabul qilingan cheklovlar o'zgarmaydi.
4. Narxi: foydalanuvchi bir marta my.telegram.org da "ilova" ochib `api_id`/`api_hash` oladi (bu ilova kaliti, akkaunt
   bilan o'qilmaydi, akkaunt hech qachon tizimga kiritilmaydi).

Agar `api_id`/`api_hash` olish imkoni bo'lmasa — zaxira A (aiogram) va 20 MB dan katta fayllar `too_large` deb belgilanadi
(W1-03), keyinroq B ga o'tish mumkin.

## 2. Long polling yoki webhook

**Webhook yo'q.** Webhook uchun serverga tashqaridan ochiq HTTPS manzil kerak; server RDP orqali ochiq va kiruvchi portlarni
ko'paytirish xavfli (`apps/w0-core/SECURITY.md`). Telethon serverdan Telegramga chiquvchi doimiy MTProto ulanishini ushlaydi
(long polling ma'nosida "biz so'raymiz"), hech qanday kiruvchi port ochilmaydi.

**Bitta token, ikki iste'molchi (D-36):** eski bot Bot API `getUpdates` bilan o'qiydi. Bot API da bir token bilan faqat
bitta `getUpdates` iste'molchisi bo'lishi mumkin (ikkinchisi 409 oladi), shuning uchun Genesis Bot API dan **o'qimaydi**:
`getUpdates`, `setWebhook`, `deleteWebhook`, `logOut`, `close` hech qachon chaqirilmaydi (faqat `getMe`, u yangilanish
olmaydi). Genesis MTProto da alohida avtorizatsiya (o'z sessiya fayli) bilan tinglaydi; MTProto sessiyani `log_out` qilish
ham taqiqlangan (`ReadOnlyClient` da yo'q). Ikkalasi ham yangilanishni olishi qabul sinovida isbotlanadi: eski bot ishlab
turganda 3 sinov guruhidagi xabarlar ikkala tomonda ham bor (eski bot — 8502 dashboard, foydalanuvchi tekshiradi;
Genesis — tinglovchi logi). Eski bot birorta sinov xabarini olmay qolsa, Genesis ulanishi darhol uziladi.
Tekshiruv: `tests/test_offline.py::test_no_update_or_logout_calls`.

**Sessiya:** `secrets\w1_bot.session` bir marta yaratiladi va qayta ishlatiladi; har ishga tushishda qayta login qilinmaydi
(flood-wait xavfi). Bitta sessiya bir vaqtda faqat bitta jarayonda.

## 3. Bot cheklovlari (Q4 da qabul qilingan)

- **Eski tarix yo'q.** Bot faqat guruhga qo'shilgandan keyingi xabarlarni oladi. W1-05 faqat bot ishlamagan oraliqni to'ldiradi.
- **Boshqa botlarning xabarlari ko'rinmaydi** (eski bot va reporter bot xabarlari Genesis'ga tushmaydi — kerak emas).
- **Bot guruhlarda allaqachon admin** (D-36): admin bot privacy mode'dan qat'i nazar hamma xabarni ko'radi; guruhga qo'shish,
  `/setprivacy` va BotFather sozlamalari o'zgartirilmaydi. Genesis bot ko'rgan **barcha** guruhlardan yangilanish oladi,
  lekin faqat `core.sources` dagi `enabled = true` guruhlarni o'qiydi (§5). Avval 3 ta sinov guruhi, keyin bosqichma-bosqich 200+.
- **Uzilish 24 soatdan oshmasligi kerak:** Telegram bot uchun o'tkazib yuborilgan yangilanishlarni cheklangan vaqt saqlaydi (W1-07).
- 2 GB dan katta fayl yuklanmaydi (W1-03 `too_large` belgisi).
- Guruh a'zolarini kuzatuvdan xabardor qilish — foydalanuvchi/yurist qarori (Q4 ning 2-qismi).

## 4. Sirlar (token, ilova kaliti, sessiya)

| Fayl (`secrets\`) | Mazmuni | Kim yozadi |
|---|---|---|
| `w1_bot_token.txt` | eski (mavjud) botning tokeni, bitta qator (D-36) | foydalanuvchi |
| `telegram_api.env` | `TG_API_ID=...`, `TG_API_HASH=...` | foydalanuvchi |
| `w1_bot.session` | Telethon sessiyasi (bot avtorizatsiya kaliti) | dastur birinchi ulanishda |

- `secrets\` ACL: faqat SYSTEM, Administrators, `Администратор` (W0-04); git'ga tushmaydi; oyna agentlariga o'qish taqiqlangan (W0-02).
- Kod sirlarni faqat `genesis_common.read_secret` / `read_secret_env` orqali o'qiydi; o'qilgan qiymat log redaksiyasiga
  ro'yxatlanadi (`***`). Token URL'da ishlatilganda xato matni ham redaksiyadan o'tadi. Agent tokenni hech qachon ko'rmaydi.
- DPAPI shifrlash hozir ishlatilmaydi: W0-07 servislari boshqa hisobda ishlashi mumkin; himoya — ACL. W0-10 da qayta ko'riladi.

## 5. Faqat-o'qish kafolati (qoida 4, P-04)

- `w1ingest.readonly.ReadOnlyClient` Telethon klientini o'raydi va faqat ruxsat etilgan metodlarni beradi (ulanish, `get_me`,
  xabar hodisalari, `get_entity`, keyin W1-03 da `download_media`, W1-05 da `get_messages`). Boshqa har qanday atribut
  (`send_message`, `edit_message`, `delete_messages`, `forward_messages`, reaksiya, pin, …) `AttributeError` beradi.
- `tests/test_offline.py` kodda yozish chaqiruvlari yo'qligini `grep` bilan tekshiradi.
- Ruxsat ro'yxati: `core.sources` (`enabled = true`). Ro'yxatdan tashqari chatdan faqat metadata (chat id, nom) `sources` ga
  `enabled = false` bilan yoziladi (tasdiqlash uchun), xabar matni **o'qilmaydi va saqlanmaydi**.
