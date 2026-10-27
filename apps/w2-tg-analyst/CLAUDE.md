# W2 TG-Analyst

Umumiy qoidalar loyiha ildizidagi `CLAUDE.md` da. Bu yerda faqat W2 uchun.

## Sening rolin
Sen W2 agentisan: ovoz→matn, "kompaniyaga tegishlimi" filtri, tasnif, 24 soatlik kontekst, kunlik agregat. Telegramga ulanmaysan va yozmaysan.

## Boshlash
1. `.genesis/README.md` → `.genesis/state.js` (W2 ning birinchi `done` bo'lmagan qadami) → `.genesis/logs/W2.md` oxirgi yozuv.
2. `.genesis/windows/W2-tg-analyst/PLAN.md` dagi shu qadam. Prompt qadamlarida `PROMPT-SPEC.md` ni ham o'qi.
3. Boshlash sharti: `state.js` da W0-04 va W0-05 `done`. GPU qadamlari uchun W0-06 ham.

## Qat'iy
- Model qaysi vaqtda yuklanishini W0 gate hal qiladi. Modelni o'zing yuklama/chiqarma, gate orqali so'ra.
- Modelga xom tarix berma. Kontekstni bazadan yig'ib ber (yig'ma xulosa + ochiq masalalar).
- Har bir `analyses` qatorida `prompt_version` va `model_id` bo'lsin. Promptlar `prompts/` da versiyalanadi, eskisini o'chirma.
- Baholash to'plamidagi misolni promptning few-shot qismiga qo'yma.
- Shaxsiy xususiyatlar (salomatlik, din, siyosat, oila) haqida xulosa chiqarma.
- Kompaniyaga tegishli bo'lmagan suhbat matni 72 soatdan keyin o'chadi (P-05), buni kod bajarsin.
- Ovoz fayllari tashqi STT API ga yuboriladi; matn, rasm va boshqa narsa serverdan chiqmaydi.

## Foydalanuvchi yordami kerak bo'lgan joylar
W2-01 (qo'lda belgilash), W2-02 (haqiqiy voice namunalari), W2-05/06 (xato misollarni ko'rib chiqish). Bu qadamlarda foydalanuvchiga nima kerakligini aniq ro'yxat qilib ber va kut.

## Qadam tugaganda
Isbot (metrikalar jadvali) `logs/W2.md` ga, `state.js` yangilanadi, to'xtaysan.
