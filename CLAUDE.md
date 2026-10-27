# Umumiy qoidalar (barcha oynalar uchun)

Loyiha: qurilish mahsulotlari savdosi kompaniyasi uchun Telegram suhbatlari va eshik kamerasini tahlil qiluvchi lokal tizim.
Barcha oynalar bitta loyiha. Har bir oyna o'z papkasida (`apps/wX-*`) ishlaydi va o'z `CLAUDE.md` iga ega.

## Boshlashdan oldin
`.genesis/README.md` ni o'qi va u yerdagi "sessiya boshida" tartibini bajar. Holat `.genesis/state.js` da.
Yo'llar loyiha ildiziga nisbatan berilgan. Sen `apps/wX-*` papkasida ishlasang, `.genesis/` = `../../.genesis/`, `shared/` = `../../shared/`. Birinchi marta ruxsat so'ralsa, foydalanuvchi ruxsat beradi (W0-02 buni sozlamaga yozadi).

## Qat'iy qoidalar
1. Reja qotirilgan. `.genesis/PLAN.md`, `STACK.md`, `CONTRACTS.md` va `windows/*/PLAN.md` ni tahrir qilma. Tushunarsiz bo'lsa `QUESTIONS.md` ga yoz va to'xta.
2. Bir vaqtda bitta qadam. Qadam tugagach `state.js` va `logs/Wx.md` ni yangila, keyin to'xta.
3. Faqat o'z papkangda yoz. Boshqa oynaning papkasiga va `shared/` ga tegma. `shared/` va DB sxemasini faqat W0 o'zgartiradi. Boshqa oynaga o'zgarish kerak bo'lsa, `QUESTIONS.md` ga `[CONTRACT]` belgisi bilan yoz.
4. Telegramga hech narsa yozma. Guruhlarga faqat W3 ning reporter boti Excel yuboradi.
5. Sirlar (`secrets/`, `.env`, `*.session`, API kalitlar, bot tokenlari) ni o'qima, chiqarma, loglarga va repoga yozma.
6. Kamera yuzlari va embeddinglar serverdan chiqmaydi. Chet el API ga faqat ovoz fayllari yuboriladi (qaror D-03).
7. Kod ingliz tilida (nomlar, izohlar), foydalanuvchi bilan muloqot va hujjatlar o'zbek tilida.
8. Hech qachon taxmin bilan "tayyor" dema. Tayyor = qabul mezoni bajarilgan va isbot logda.
9. Biror narsa ishlamasa, yashirma: `blocked` qil va sababini yoz.
