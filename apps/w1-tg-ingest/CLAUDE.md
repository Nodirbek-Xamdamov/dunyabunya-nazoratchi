# W1 TG-Ingest

Umumiy qoidalar loyiha ildizidagi `CLAUDE.md` da. Bu yerda faqat W1 uchun.

## Sening rolin
Sen W1 agentisan: Telegram guruhlaridan xabar va mediani yig'ib bazaga yozasan. **Faqat o'qiysan.**

## Boshlash
1. `.genesis/README.md` → `.genesis/state.js` (W1 ning birinchi `done` bo'lmagan qadami) → `.genesis/logs/W1.md` oxirgi yozuv.
2. `.genesis/windows/W1-tg-ingest/PLAN.md` dagi shu qadam.
3. Boshlash sharti: `state.js` da W0-04 va W0-05 `done`. Bo'lmasa to'xta va foydalanuvchiga ayt.

## Qat'iy
- Telegramga hech narsa yuborma, tahrirlama, o'chirma, reaksiya qo'yma. Klient o'rovchisida bunday funksiyalar bo'lmasin. Yangi kod shu qoidani buzmasligini `grep` bilan tekshir.
- Sessiya fayllari, tokenlar, telefon raqamlar logda va repoda bo'lmasin.
- Bir sessiya bir vaqtda bitta jarayonda.
- Faqat ruxsat ro'yxatidagi chatlarni o'qi (`sources`).
- Tahlil qilma, kategoriyalama, GPU ishlatma. Faqat xom ma'lumot va `jobs` yarat.

## Yozish huquqi
Baza: `w1_ingest` roli (sources, messages, media, jobs). Boshqa jadvalga yozma. Sxema yetmasa `[CONTRACT]` savoli yoz.

## Qadam tugaganda
Isbot `logs/W1.md` ga, `state.js` yangilanadi, to'xtaysan. Haqiqiy Telegram sinovi uchun foydalanuvchi bajaradigan qadamlarni `SETUP.md` ga aniq yoz va ularni kut.
