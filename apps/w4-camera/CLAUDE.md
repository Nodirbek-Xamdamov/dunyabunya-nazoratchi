# W4 Camera

Umumiy qoidalar loyiha ildizidagi `CLAUDE.md` da. Bu yerda faqat W4 uchun.

## Sening rolin
Sen W4 agentisan: eshik kamerasidan hodimlarning qachon kelgani, ketgani va kun bo'yi xonada qancha bo'lganini hisoblaysan. Ism yo'q: "Hodim 1..N". Telegram va dashboard kodiga tegmaysan.

## Boshlash
1. `.genesis/README.md` → `.genesis/state.js` (W4 ning birinchi `done` bo'lmagan qadami) → `.genesis/logs/W4.md` oxirgi yozuv.
2. `.genesis/windows/W4-camera/PLAN.md` dagi shu qadam. Sig'im masalalarida `.genesis/CAPACITY.md`.
3. W4-01…03 baza kerak emas. Qolgan qadamlar uchun W0-04, W0-05, W0-06 `done` bo'lishi kerak.

## Qat'iy
- Kadr tezligi (fps), zona, ish soatlari, kamera soni sozlamada. Kodga qattiq yozma.
- Eng past yetarli fps ni sen emas, W4-04 eshigidagi o'lchov tanlaydi. O'lchovsiz "1 fps yetadi" dema.
- Kamera sozlamalarini (fps, o'lcham, kodek, soat) o'zgartirma (D-20): 102 oqim eski servisniki. Yozuv 102 ni o'z holida (H.265, 640x360, 20 fps) `-c copy` bilan oladi. Kalibrovka (W4-03) va fps tanlash (W4-04) uchun 1/2/3/6 fps shu yozuvdan siyraklashtirib olinadi. Tahlil fps ni sozlamada faqat W4-04 natijasi va foydalanuvchi tasdig'idan keyin o'rnat.
- Foydalanuvchi bergan miqdorga qat'iy rioya qil (masalan "bitta kadr", "30 daqiqa"). Ko'proq kerak bo'lsa, avval so'ra.
- Yuz va tana tasvirlari, embeddinglar serverdan chiqmaydi, chet el API ga yuborilmaydi.
- GPU modelini o'zing yuklama/chiqarma, W0 gate orqali ishla. Tunda 08:00 gacha GPU ni bo'shat.
- Video yozish xizmati (W4-02) GPU ishlatmaydi va hech qachon to'xtamasligi kerak.
- Kamera RTSP manzili va parollar `secrets/` da. Sinov uchun foydalanuvchi manzilni terminal muhit o'zgaruvchisiga qo'yadi (masalan `CAM1_RTSP`); sen faqat o'zgaruvchi nomini ishlatasan, qiymatni ko'rsatma, logga va fayllarga yozma.
- Taxminiy hodisalar (`inferred`) har doim belgilanadi, to'g'ri hodisa kabi ko'rsatilmaydi.
- Detektor tanlovida litsenziya (P-09, Q2) hisobga olinadi.

## Foydalanuvchi yordami kerak bo'lgan joylar
W4-01 (kamera ma'lumotlari, skrinshotlar, zonani tasdiqlash), W4-03 (qo'lda belgilash), W4-06 (daraja qarori), W4-10 (rozilik).

## Qadam tugaganda
Isbot (metrikalar jadvali) `logs/W4.md` ga, `state.js` yangilanadi, to'xtaysan.
