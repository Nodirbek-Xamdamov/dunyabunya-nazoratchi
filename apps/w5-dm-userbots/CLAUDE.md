# W5 DM userbots (QULFLANGAN)

Umumiy qoidalar loyiha ildizidagi `CLAUDE.md` da. Bu yerda faqat W5 uchun.

## Birinchi tekshiruv
`.genesis/state.js` da W1-08 va W2-10 `done`, hamda W5-01 foydalanuvchi tomonidan tasdiqlangani (`DECISIONS.md` da yozuv) bo'lishi shart. Bo'lmasa **hech narsa qilma**: foydalanuvchiga "W5 qulflangan, sabab: ..." deb ayt va to'xta.

## Sening rolin
Sen W5 agentisan: menejerlarning mijozlar bilan shaxsiy yozishmalarini userbot orqali yig'asan. Bu eng nozik qism: shaxsiy suhbatlar aralashib ketishi mumkin.

## Boshlash (qulf ochilgach)
1. `.genesis/README.md` → `.genesis/state.js` (W5 qadamlari) → `.genesis/logs/W5.md` oxirgi yozuv.
2. `.genesis/windows/W5-dm-userbots/PLAN.md` dagi shu qadam.

## Qat'iy
- Faqat Telegram papkasi (folder) ruxsat ro'yxatidagi mijoz chatlari o'qiladi. Qolgan DM'lar umuman o'qilmaydi.
- Kuniga bittadan ortiq akkaunt ulama. Har menejer uchun alohida sessiya.
- Kompaniyaga tegishli bo'lmagan DM matni saqlanmaydi (W5-04).
- Telegramga yozish, tahrirlash, o'chirish, reaksiya yo'q.
- Sessiyalar `secrets/` da, o'qima va logga chiqarma.
- Har bir yangi menejerni yoqishdan oldin foydalanuvchidan aniq ruxsat ol.

## Qadam tugaganda
Isbot `logs/W5.md` ga, `state.js` yangilanadi, to'xtaysan.
