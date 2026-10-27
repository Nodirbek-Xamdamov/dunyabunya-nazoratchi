# W1 TG-Ingest — reja (qotirilgan)

Papka: `apps/w1-tg-ingest`. Boshlash sharti: **W0-04 va W0-05 `done`**, W1-01 uchun Q4 javobi.
Mas'uliyat: Telegram guruhlaridan xabar va mediani yig'ib bazaga yozish. Faqat o'qiydi.
Tegmaydi: tahlil, kategoriyalash, GPU, dashboard. Telegramga hech narsa yubormaydi.

## Qadamlar

**W1-01 Ulanish usuli va xavfsiz sessiya**
- Qil: Q4 javobiga qarab rasmiy bot yoki userbot. API ma'lumotlarini olish yo'riqnomasi, sessiyani Windows DPAPI yoki shifrlangan faylda saqlash, o'qiladigan chatlar uchun ruxsat ro'yxati (`sources`).
- Natija: `apps/w1-tg-ingest/SETUP.md` (foydalanuvchi qo'lda qiladigan qadamlar), ulanish sinov skripti.
- Qabul: ulanish ishlaydi, ruxsat ro'yxatidan tashqari chat umuman o'qilmaydi (salbiy test).
- Qabul (D-36, v1.4): eski bot ishlab turganda ikkalasi ham (eski bot va W1 MTProto tinglovchisi) 3 sinov guruhidagi xabarni oladi va eski bot ishi buzilmaydi; W1 Bot API `getUpdates`, `setWebhook`, `deleteWebhook`, `logOut`, `close` ni chaqirmaydi.
- Bog'liq: Q4, D-36.

**W1-02 Faqat-o'qish tinglovchi**
- Qil: ruxsat etilgan guruhlardagi yangi xabarlarni real vaqtda `messages` ga yozish. Telegram klientini o'rovchi qatlam orqali ishlatish: yuborish/tahrirlash/o'chirish/reaksiya funksiyalari o'rovchida mavjud emas.
- Qabul: 20 ta test xabar bazaga to'liq tushadi (takrorlansa ikki marta yozilmaydi), kodda `send_*` chaqiruvi yo'qligi `grep` bilan isbotlangan.

**W1-03 Media yuklash va W2 uchun job**
- Qil: voice, video, video_note, photo, document yuklash (hajm chegarasi `config.toml` da), diskka `data/media/YYYY-MM-DD/` ga yozish, hash, `media` qatori, tegishli `jobs` (`transcribe`, `analyze_image`) yaratish.
- Qabul: har tur uchun namunaviy fayl yuklandi, katta fayl chegaradan oshsa o'tkazib yuboriladi va belgilanadi, job'lar navbatda ko'rinadi.

**W1-04 Tahrir/o'chirish, reply va yuboruvchi roli**
- Qil: tahrirlangan va o'chirilgan xabarlarni belgilash (eski versiya saqlanadi), reply/forward zanjiri, `people` jadvali bilan yuboruvchi roli (`employee`/`client`/`unknown`).
- Qabul: tahrir va o'chirish sinovi, reply zanjiri to'g'ri, noma'lum yuboruvchi `unknown` bo'ladi.
- Bog'liq: Q5.

**W1-05 To'xtab qolgandan keyin to'ldirish**
- Qil: uzilish davrida o'tkazib yuborilgan xabarlarni aniqlash va to'ldirish (gap detection), flood-wait bo'lsa kutish, qayta urinish.
- Qabul: ingest 30 daqiqaga o'chirib yoqilganda o'tkazib yuborilgan xabarlar bazaga qo'shiladi, takror yo'q.

**W1-06 Ko'p akkauntga tayyor reestr**
- Qil: akkauntlar reestri (hozircha bitta), har akkaunt uchun alohida sessiya va ruxsat ro'yxati; W5 keyin shu reestrga DM adapteri qo'shadi.
- Qabul: ikkinchi (soxta) akkaunt konfiguratsiyasi kod o'zgarishisiz qo'shiladi.

**W1-07 Ishonchlilik**
- Qil: avtomatik qayta ulanish, bitta nusxa qulfi (ikkinchi nusxa ishga tushmaydi), heartbeat, sessiya bekor bo'lsa aniq signal, jarayon o'lsa servis ko'taradi.
- Qabul: tarmoq uzib yoqilganda 2 daqiqada tiklanadi, ikkinchi nusxa ishga tushsa darhol chiqib ketadi.

**W1-08 48 soatlik sinov**
- Qil: haqiqiy guruhlarda faqat-o'qish rejimida 48 soat ishlatish, hisobot: yo'qolgan xabar soni, uzilishlar, xotira/disk o'sishi.
- Qabul: bazadagi xabarlar soni Telegram Desktop eksporti bilan tanlama taqqoslanganda 100% mos; hisobot `logs/W1.md` da.

## Chegaralar

- Sessiya fayllari, tokenlar va telefon raqamlari logga va repoga tushmaydi.
- Bitta sessiya bir vaqtda faqat bitta jarayonda ishlaydi.
- Akkauntlar kuniga bittadan ulanadi (W5 uchun qoida shu yerda ham amal qiladi).
