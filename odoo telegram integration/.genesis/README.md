# .genesis — loyiha xotirasi va davom ettirish protokoli

Bu papka loyihaning "xotirasi". Chat tozalansa yoki ertasi kuni qaytilsa, hamma narsa shu yerdan tiklanadi.
Hech narsa chatning ichida saqlanmaydi.

## Fayllar

| Fayl | Kim o'zgartiradi | Vazifasi |
|---|---|---|
| `PLAN.md`, `STACK.md`, `CONTRACTS.md` | hech kim (qotirilgan) | maqsad, arxitektura, texnologiyalar, umumiy shartnomalar |
| `windows/Wx-*/PLAN.md` | hech kim (qotirilgan) | oynaning bosqichma-bosqich rejasi |
| `DECISIONS.md` | faqat foydalanuvchi bilan kelishib | qabul qilingan qarorlar |
| `QUESTIONS.md` | agentlar qo'shadi, foydalanuvchi javob beradi | ochiq savollar va to'siqlar |
| `state.js` | oyna agentlari | har bir qadamning holati (yagona haqiqat manbai) |
| `done.html` | o'zgarmaydi | `state.js` ni chiroyli ko'rsatadi (brauzerda oching) |
| `logs/Wx.md` | oynaning o'z agenti | har bir qadam oxirida yozuv, faqat qo'shiladi |
| `CAPACITY.md` | qotirilgan | kamera sig'imi hisobi |

## Har bir sessiya boshida (majburiy tartib)

1. Shu faylni o'qi.
2. `state.js` ni och: oynangning birinchi `done` bo'lmagan qadamini top. `doing` qadam bo'lsa, o'shandan davom et.
3. `logs/Wx.md` ning oxirgi yozuvini o'qi. Unda "Keyingi aniq harakat" bor.
4. `windows/Wx-*/PLAN.md` dagi shu qadamni o'qi. Faqat shu qadam, keyingisi emas.
5. Foydalanuvchiga 3 qatorda ayt: qayerda to'xtagan, hozir nima qilasan, nimaga bog'liq.

Foydalanuvchi faqat "davom et" desa ham shu tartib ishlaydi.

## Har bir qadam oxirida (majburiy)

1. Qabul mezonlari (acceptance) bajarilganini isbotla: buyruq natijasi yoki test chiqishi.
2. `state.js` da qadamni `done` qil (`s` maydoni), `n` ga bir qator izoh yoz. Kodda emas, shu faylda.
3. `logs/Wx.md` ga yozuv qo'sh: nima qilindi, qaysi fayllar o'zgardi, qanday tekshirish mumkin, **Keyingi aniq harakat**.
4. `state.js` ning `log` massiviga bir qator qo'sh.
5. To'xta. Foydalanuvchi "davom et" demaguncha keyingi qadamga o'tma.

Kontekst to'lib borsa (taxminan 70%) yoki foydalanuvchi "to'xta" desa, qadam tugamagan bo'lsa ham 2–4 ni bajar va `doing` qoldir.

## Holatlar

`todo` · `doing` · `done` · `blocked` (savol kutilmoqda). Agent faqat shu to'rttasini yozadi.
`locked` holatini `done.html` o'zi hisoblaydi: qadamning `needs` ro'yxatidagi qadamlar `done` bo'lmaguncha u qulflangan ko'rinadi. Qulflangan qadamni boshlama.

## Reja o'zgarmaydi

- `PLAN.md`, `STACK.md`, `CONTRACTS.md` va `windows/*/PLAN.md` ni agent tahrir qilmaydi.
- Reja noto'g'ri yoki tushunarsiz bo'lsa: `QUESTIONS.md` ga `[Wx-NN] savol` qo'sh, qadamni `blocked` qil, foydalanuvchiga ayt va to'xta.
- Reja faqat foydalanuvchi roziligi bilan va `DECISIONS.md` ga yozib o'zgaradi (versiya oshadi).

## state.js tuzilishi (qisqa)

`window.GENESIS_STATE = {meta, windows[{id,name,folder,needs[],steps[{id,t,s,n,needs?}]}], milestones[{id,t,needs[]}], log[{t,w,x}]};`
Qadamda `needs` bo'lmasa oynaning `needs` i amal qiladi. `needs` rejaning qismi, agent uni o'zgartirmaydi.
Tahrir qilganda faqat kerakli qatorni o'zgartir. Keyin `done.html` ni brauzerda yangila: bo'sh chiqsa, JSON buzilgan.
