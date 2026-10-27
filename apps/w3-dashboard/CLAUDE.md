# W3 Dashboard + Hisobot

Umumiy qoidalar loyiha ildizidagi `CLAUDE.md` da. Bu yerda faqat W3 uchun.

## Sening rolin
Sen W3 agentisan: dashboard, Excel hisobotlar va reporter bot. Faqat bazadan o'qiysan, modelni chaqirmaysan.

## Boshlash
1. `.genesis/README.md` → `.genesis/state.js` (W3 ning birinchi `done` bo'lmagan qadami) → `.genesis/logs/W3.md` oxirgi yozuv.
2. `.genesis/windows/W3-dashboard/PLAN.md` dagi shu qadam.
3. W3-01 baza kerak emas va darhol boshlanadi. W3-02 dan boshlab W0-04 `done` bo'lishi kerak.

## Qat'iy
- Tayyor dashboard dizaynini va prototipni o'zgartirma. Faqat ma'lumot manbasini haqiqiy bazaga ulaysan.
- Bazaga faqat `w3_dashboard` roli va W0 yaratgan view'lar orqali ulan. Jadvalga to'g'ridan-to'g'ri ulanma.
- Har bir AI natijasi "AI taxmini" va ishonchlilik bilan, asl xabarga havola bilan ko'rsatilsin.
- Hodim bo'yicha baho yoki ball yo'q. Faqat neytral ko'rsatkichlar (javob vaqti, soni).
- Kamera ma'lumoti va xom xabar faqat ruxsatli rollarga.
- Reporter bot faqat ruxsat etilgan ichki chatlarga yuboradi. Mijoz guruhlariga hech qachon.
- Bot tokeni `secrets/` da, kodga yozilmaydi.

## Foydalanuvchi yordami kerak bo'lgan joylar
W3-01 (prototip kodini shu papkaga joylash, Q10), W3-06 (Excel ko'rsatkichlari, Q7), W3-07 (reporter bot yaratish va ichki guruhga qo'shish yo'riqnomasi).

## Qadam tugaganda
Isbot (skrinshot yoki test chiqishi) `logs/W3.md` ga, `state.js` yangilanadi, to'xtaysan.
