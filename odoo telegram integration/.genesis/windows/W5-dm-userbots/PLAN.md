# W5 DM userbots — reja (qotirilgan, QULFLANGAN)

Papka: `apps/w5-dm-userbots`. Boshlash sharti: **W1-08 `done`, W2-10 `done` va W5-01 (huquqiy tekshiruv) foydalanuvchi tomonidan tasdiqlangan.** Bundan oldin bu oyna ochilmaydi.
Mas'uliyat: menejerlarning mijozlar bilan shaxsiy (DM) yozishmalarini userbot orqali yig'ish. Barcha hodimlarda Premium bor (Telegram transkripsiyasi ham mavjud, lekin o'zbek tilida tasdiqlanmagan).
Tegmaydi: W1 yadrosi (faqat uning akkaunt reestriga adapter qo'shadi), tahlil (W2 o'sha quvurni ishlatadi).

## Qadamlar

**W5-01 Huquqiy va rozilik tekshiruvi**
- Qil: hodimlardan yozma rozilik shabloni talablari, mijozlarga xabar berish usuli, yurist tasdig'i uchun savollar ro'yxati, nima saqlanadi/saqlanmaydi.
- Qabul: foydalanuvchi yozma tasdiqlagan (`DECISIONS.md` ga yoziladi). Tasdiq bo'lmaguncha keyingi qadamlar `locked`.

**W5-02 Akkaunt ulash tartibi**
- Qil: kuniga bitta akkaunt ulash qoidasi (Telegram himoyasini qo'zg'atmaslik uchun), har menejer uchun alohida sessiya va API ma'lumotlari, Telegram papkasi (folder) asosidagi ruxsat ro'yxati: userbot faqat shu papkadagi mijoz chatlarini o'qiydi.
- Qabul: bitta menejerning akkaunti ulandi, papkadan tashqari chat o'qilmaydi (salbiy test).

**W5-03 DM ingest adapteri**
- Qil: W1 reestriga adapter: DM xabarlari va mediasi `messages`/`media` ga, `sources.type = dm`; hodim va mijoz rollari.
- Qabul: W1 quvuri DM uchun ham ishlaydi (fixtures bilan va 1 haqiqiy akkaunt bilan).

**W5-04 Maxfiylik filtri**
- Qil: kompaniyaga tegishli bo'lmagan DM matni A bosqichdan keyin darhol saqlanmaydi (metadata qoladi), shaxsiy suhbat tahlilga o'tmaydi.
- Qabul: shaxsiy mavzudagi sinov DM bazada matn sifatida qolmaydi.

**W5-05 Bosqichma-bosqich yoqish**
- Qil: menejerlarni birin-ketin yoqish, har biridan keyin 24 soat kuzatuv va hisobot.
- Qabul: har bir menejer uchun kuzatuv hisoboti `logs/W5.md` da va foydalanuvchi tasdig'i.
