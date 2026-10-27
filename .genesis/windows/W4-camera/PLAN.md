# W4 Camera — reja (qotirilgan, v1.2: D-20, D-23, D-24; v1.3: D-31, D-32)

Papka: `apps/w4-camera`. Boshlash sharti: W4-01…03 **darhol** (baza kerak emas); W4-02 diskka yozishi uchun W0-02; qolgan qadamlar **W0-04, W0-05, W0-06 `done`**.
Mas'uliyat: video bo'laklarini yozish, detektsiya, kirish/chiqish hodisalari, anonim shaxs, xonada bo'lish vaqti, tungi batch.
Asosiy vazifa: har bir hodimning **qachon kelgani, qachon ketgani va kun bo'yi xonada qancha bo'lgani** (ism yo'q: "Hodim 1..N").
Tegmaydi: Telegram, dashboard kodi, GPU modelini yuklash (W0 gate).

## Qadamlar

**W4-01 Kamera qabuli**
- Qil: RTSP ulanish barqarorligi, kunduz/kechqurun/orqa yorug'lik namunalari, rezolyutsiya va kodek, sub-stream'da 1 fps va 5 fps o'rnatilishini tekshirish (Q17), eshik zonasi va kirish/chiqish chizig'ini belgilash, "xona" ta'rifi (Q14): xonadan tashqari yo'nalish (hojatxona, ko'cha) qaysi eshikdan ekanini aniqlash.
- Natija: `CAMERA_INTAKE.md` (parametrlar, namunaviy kadrlar, zona koordinatalari).
- Qabul: 30 daqiqa uzluksiz oqim sinovi, uzilishlar soni yozilgan; foydalanuvchi zonani tasdiqlagan.
- Bog'liq: Q8, Q14.

**W4-02 Video bo'laklarini yozish xizmati (D-12, D-20, D-24)**
- Qil: ffmpeg `-c copy` (dekodlashsiz) bilan kameraning sub-stream'ini 10 daqiqalik bo'laklarga yozish, `E:\data\video\<kamera>\<sana>\` + kunlik `manifest.jsonl` (v1.2; W0-04 `core.import_manifest_record` bilan `video_segments` ga), vaqt NTP bilan sinxron (UTC), uzilishni aniqlash va qayta ulanish, ish soatlaridan tashqarida yozmaslik (v1.2, D-24: 07:00–22:00, sozlamada), aylanma bufer (standart 7 kun, sozlanadi) va disk byudjeti. v1.2 (D-20): kamera sozlamasi o'zgartirilmaydi (102 oqim eski servisniki); 102 oqim o'z holida (H.265, 640x360, 20 fps) yoziladi, fps tahlilda siyraklashtiriladi. GPU va dekodlash ishlatmaydi.
- Qabul: 1 kamerada 2 soatlik yozuvda bo'laklar soni kutilganiga mos, uzilishlar `video_segments` da belgilangan, 24 soat barqaror (xotira o'smaydi), bufer muddatidan eski bo'laklar avtomatik o'chadi.
- Bog'liq: Q11, Q17.

**W4-03 Haqiqat to'plami (v1.2: 20 fps yozuvdan, D-20)**
- Qil: v1.2 (D-20): alohida 5 fps kalibrovka yozuvi yo'q, oddiy yozuv 20 fps da bo'lgani uchun u haqiqat to'plami uchun yetarli. Yozuvdan foydalanuvchi bilan kamida 1 ish kunining qo'lda belgilangan kirish/chiqish vaqtlari (kim, qachon, qaysi yo'nalishda, xonadan tashqariga chiqishlar ham) olinadi. Belgilash uchun oddiy tartib. Sabab: faqat 1 fps yozuvda "1 fps o'tkazib yuborgan hodisalar"ni o'lchab bo'lmaydi, chunki haqiqat ham 1 fps bilan cheklangan. To'plam va uning video qirqimlari `F:\genesis\keep` ga nusxalanadi (D-23). Eshik zonasining yakuniy tekshiruvi shu yerda (D-24).
- Qabul: to'plam fayli va qoidalar mavjud; hodimlar soni, kirish/chiqish soni yozilgan; haqiqat kunlarining 20 fps yozuvi W4-04 uchun saqlangan (aylanma bufer o'chirmaydi).
- Bog'liq: W4-02 (video bo'laklari kerak).

**W4-04 Detektor va tracker (past fps) — fps tanlash eshigi**
- Qil: odam detektori (YOLO yoki Apache/MIT muqobil, P-09 va Q2 bo'yicha), past fps uchun moslangan bog'lash (masofa + tashqi ko'rinish, IoU'ga bog'liq emas), 20 fps yozuvni siyraklashtirib 1, 2, 3 va 6 fps ni haqiqat to'plamida solishtirish (v1.2, D-20). Maqsad: 1 fps. Eshik zonasida odam ko'ringanda fps ni ko'taradigan adaptiv variantni ham solishtirish.
- Qabul: har bir fps uchun kirish/chiqish recall va precision jadvali. Tanlov qoidasi: recall ≥ 0.95 beradigan **eng past** fps. 1 fps o'tmasa foydalanuvchiga hisobot beriladi va 2, 3 yoki 6 fps tanlanadi. Tanlangan qiymat `cameras.analysis_fps` ga yoziladi; kamera sozlamasi o'zgarmaydi (v1.2, D-20).
- Bog'liq: W4-03, W0-06.

**W4-05 Eshikdan o'tish hodisalari**
- Qil: v1.2 (D-24): ostona kreslo orqasida yashirin, shuning uchun hodisa eshik zonasida trekning paydo bo'lishi/yo'qolishi va yo'nalishidan olinadi (zona `intake\zone_config.json`, vaqtincha tasdiqlangan, yakuniy tekshiruv W4-03/W4-05 da); yo'nalish (kirdi/chiqdi), takror hodisalarni birlashtirish (debounce), o'tish 1–2 kadrda o'tib ketsa tiklash (`inferred=true` bilan belgilanadi), `camera_events` ga yozish. v1.3 (D-31): har hodisaga vaqt belgili kadr saqlanadi (`camera_event_frames`, fayl D-29 bo'yicha `E:\data\camera_events\YYYY\MM\DD\<kamera>\`).
- Qabul: haqiqat to'plamida hodisalar recall ≥ 0.95; tiklangan hodisalar ulushi hisobotda.

**W4-06 4 ta doimiy hodim ("Hodim 1..4", daraja C; v1.3, D-31)**
- Qil: daraja C (Q9 javobi, D-31): 4 ta asosiy hodim doimiy "Hodim 1..4" (`anon_persons.staff_no`, V008), ism yo'q. Boshqa kirib-chiqqanlar ("mehmon") hisobga olinmaydi. Ro'yxatga olish (tashqi ko'rinish embeddingi) haqiqat to'plamidan (W4-03). Kunlar bo'yicha tanish ishonchsiz bo'lsa dashboardda rasm bo'yicha foydalanuvchi tasdig'i (kadr + taklif qilingan "Hodim N"). Embeddinglar `anon_persons` da, serverdan chiqmaydi. Qadam oxirida 640x360 (102 oqim) yetarliligini o'lchash; yetmasa 101 oqimidan faqat odam kesmasini olish variantini hisobot qilish (kamera sozlamasi o'zgarmaydi, D-20).
- Qabul: haqiqat to'plamida bir odam chiqib-kirganda va kunlar bo'yicha bir xil "Hodim N" olishi ulushi (id continuity) ≥ 0.9; mehmon hech qaysi "Hodim N" ga yozilmagan; 640x360 o'lchovi va 101 kesmasi bo'yicha xulosa logda.
- Bog'liq: Q9, W4-10 (huquqiy).

**W4-07 Xonada bo'lish vaqti**
- Qil: `presence_sessions` (kirdi–chiqdi oraliqlari), kunlik jami, yo'qolgan chiqish uchun qoida (masalan kun oxirida ochiq sessiya yopilmaydi, "noaniq" belgisi), `daily_presence`. v1.3 (D-32) formulalari: **birinchi kirish** = kunning birinchi `in` hodisasi; **oxirgi chiqish** = oxirgi `out`; **ishda** = birinchi kirish → oxirgi chiqish; **chiqish oraliqlari** = ketma-ket sessiyalar orasidagi har bir bo'shliq (hojatxona, abet/tushlik va boshqa har qanday chiqish) ro'yxati; **xonada** = ishda minus chiqish oraliqlari yig'indisi. Xona ichidagi harakat (namoz va h.k.) xonada hisoblanadi. Misol: 08:00 keldi, 1 soat abet, 18:00 ketdi → ishda 10 soat, xonada 9 soat. Natija W3 uchun `api.v_staff_day` da (V008).
- Qabul: haqiqat to'plamida har bir hodim uchun kunlik jami vaqt xatosi ≤ 5 daqiqa.

**W4-08 Tungi batch (v1.2: 1 kamerada 22:10 dan, maqsad 00:00; ko'p kamerada 00:00–08:00)**
- Qil: v1.2 (D-24, D-14): 1 kamera bosqichida `coresident` rejimda shu kunning yozuvi 22:10 da (yozuv 22:00 da tugaydi) tahlil qilina boshlaydi, maqsad 00:00 gacha natija; tugamasa 00:00–08:00 da davom etadi. `camera_day_batch` job'ini bajarish: kun video bo'laklari (dekodlash va bazaviy fps ga tushirish), kamera/bo'lak bo'yicha davom ettiriladigan (o'lsa qolgan joydan), progress heartbeats'ga, 08:00 gacha tugamasa qismiga natija + signal; oyna yetmasa W0-06 sozlamasi bilan kengaytiriladi.
- Qabul: ishni o'rtada to'xtatib qayta yoqqanda natija bir xil; 1 kamerada natija 00:00 gacha tayyor (o'lchov logda); `timeslice` rejimda 08:00 da GPU bo'shaydi.
- Bog'liq: W0-06.

**W4-09 Sig'im benchmark**
- Qil: sun'iy ko'p kamerali yuk (bir kameraning oqimi/bo'laklari N nusxa, N 50 gacha: dekodlash + tahlil), o'tkazuvchanlik (kadr/s) va oyna ichida nechta kamera sig'ishi; `CAPACITY.md` hisobi bilan solishtirish.
- Qabul: o'lchangan raqamlar `logs/W4.md` da; 8 soatlik oynaga N kamera sig'adi/sig'maydi xulosasi.
- Bog'liq: D-11 (hozir 1 kamera, kelajakda 50+ mumkin), Q18.

**W4-10 Maxfiylik**
- Qil: saqlash muddatlari (video bo'lak: aylanma bufer muddati; embedding; hodisa), o'chirish buyrug'i, kirish nazorati, hodimlarga xabar/rozilik matnini tayyorlash uchun talablar ro'yxati (yurist tasdig'i foydalanuvchi tomonidan).
- Qabul: muddati o'tgan video bo'lak va embedding avtomatik o'chadi (test); `PRIVACY.md` yozilgan.

## Chegaralar

- Yuz va tana tasvirlari, embeddinglar serverdan chiqmaydi, chet el API ga yuborilmaydi.
- Video bo'laklari va hodisalar vaqti UTC, hisobot kuni Asia/Tashkent.
- fps, zona, kamera soni, ish soatlari `cameras` sozlamasida; kodda qattiq yozilmagan.
