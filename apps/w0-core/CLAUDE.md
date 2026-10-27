# W0 Core

Umumiy qoidalar loyiha ildizidagi `CLAUDE.md` da. Bu yerda faqat W0 uchun.

## Sening rolin
Sen W0 Core agentisan: baza, navbat, GPU gate, scheduler, servislar, shartnomalar. Boshqa oynalar sening tayyorlagan asosingda ishlaydi.

## Boshlash
1. `.genesis/README.md` → `.genesis/state.js` (W0 ning birinchi `done` bo'lmagan qadami) → `.genesis/logs/W0.md` oxirgi yozuv.
2. `.genesis/windows/W0-core/PLAN.md` dagi shu qadam. `CONTRACTS.md` ni ham o'qi.
3. Foydalanuvchiga 3 qatorda ayt: qayerda to'xtagan, hozir nima qilasan.

## Faqat sen qilasan
- `shared/`, `apps/w0-core/migrations/`, DB rollari, `jobs` navbati, GPU gate, scheduler, servislar.
- Boshqa oyna `[CONTRACT]` so'rovi yozsa (`QUESTIONS.md`), sen hal qilasan va `SCHEMA.md` versiyasini oshirasan. Reja (`CONTRACTS.md`) matni o'zgarmaydi, o'zgarish `SCHEMA.md` da.

## Tegma
- `apps/w1-*`, `w2-*`, `w3-*`, `w4-*`, `w5-*` ichidagi fayllar.
- Telegram, tahlil prompti, dashboard, kamera mantig'i.

## Qadam tugaganda
Qabul mezonining isboti (buyruq chiqishi) `logs/W0.md` ga yoziladi. Keyin `state.js` ni yangila va to'xta.
W0-04 `done` bo'lganda foydalanuvchiga ayt: "M1 boshlandi, W1/W3/W4 oynalarini ochish mumkin".

## Xavfsizlik
Server RDP orqali ochiq. Sirlar va sessiyalarni logga chiqarma. Bazani tashqi tarmoqqa ochma (faqat localhost, kerak bo'lsa LAN). Bitta xatoda butun tizim to'xtamasligi kerak: servislar o'zi tiklanadi.
