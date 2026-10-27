# W3 Dashboard + Hisobot — reja (qotirilgan, v1.3: D-31, D-32, D-33)

Papka: `apps/w3-dashboard`. Boshlash sharti: W3-01 **darhol** boshlanadi (baza kerak emas); W3-02 dan boshlab **W0-04 `done`**.
Mas'uliyat: dashboard, Excel hisobotlar, reporter bot.
Tegmaydi: modelni chaqirish, Telegram o'qish, kamera mantig'i. Bazaga faqat W0 yaratgan view'lar orqali ulanadi (`CONTRACTS.md` §6) va faqat o'qiydi.

## Qadamlar

**W3-01 Prototip auditi va maydonlar xaritasi**
- Qil: tayyor dashboard va prototip kodini shu papkaga joylash (foydalanuvchi beradi), texnologiya va sahifalarni aniqlash, har bir ko'rsatkich uchun "qaysi DB view/ustundan" xaritasini tuzish. Dizaynni o'zgartirmaydi.
- Natija: `apps/w3-dashboard/MAPPING.md` (sahifa → ko'rsatkich → view → ustun), yetishmayotganlar ro'yxati (W0 ga `[CONTRACT]` so'rovi).
- Qabul: har bir ko'rsatkich xaritada bor yoki "ma'lumot manbasi yo'q" deb belgilangan.
- Qaror (P-12): audit oxirida `AUDIT.md` da tavsiya: **yangilash** (standart) yoki qayta yozish. Meyorlar: (1) kod tuzilishi o'qiladigan, (2) ma'lumot qatlami UI dan ajratilgan yoki ajratish oson, (3) login va rollarni qo'shish oson, (4) mock ma'lumotga qattiq bog'lanmagan. Qaror foydalanuvchiniki: agent qayta yozishni o'zi boshlamaydi.
- Bog'liq: Q10.

**W3-02 Faqat-o'qish ma'lumot qatlami**
- Qil: view'lardan o'qiydigan ma'lumot qatlami (API yoki server-side), sahifalash, `w3_dashboard` roli bilan ulanish; sinov uchun W0-09 fixtures.
- Qabul: dashboard ma'lumotni DB dan oladi (soxta ma'lumot bilan), yozish urinishi rad etiladi (salbiy test).

**W3-03 Kategoriya sahifalari**
- Qil: v1.3 (D-33) 7 bo'lim bo'yicha sahifalar (Dastavka va Logistika; Mahsulot Sifati va Nuqsonlar; Komplektatsiya va Hajm; Xodimlar va Xizmat Ko'rsatish; Narx va To'lovlar; Qaytarish va Kafolat; Ijobiy Fikrlar; ro'yxat `api.v_categories`, "Boshqa" yo'q, aloqasiz xabar statistikaga kirmaydi), filtrlar (sana, guruh, menejer, jiddiylik), har bir yozuvda asl xabarga havola, "AI taxmini" belgisi va ishonchlilik, `review_queue` sahifasi (odam tuzatishi).
- Qabul: guruhdagi xabar tasniflangan holda ko'rinadi, filtrlar to'g'ri, tuzatish `audit_log` ga yoziladi. **M2 shu qadam bilan yopiladi.**

**W3-04 Kamera sahifalari**
- Qil: v1.3 (D-31, D-32): har asosiy hodim ("Hodim 1..4") uchun keldi va ketdi (kirish/chiqish kadri bilan), ishda, xonada, chiqish oraliqlari ro'yxati (`api.v_staff_day`, V008); kun bo'yi bandlik chizig'i (timeline), noaniqlik belgisi (`inferred`). Kadrlar faqat ruxsatli rollarga (W3-05).
- Qabul: W4 fixtures bilan sahifalar to'g'ri hisoblangan vaqtni ko'rsatadi.
- Bog'liq: W4-07 natija formati.

**W3-05 Kirish va rollar**
- Qil: login, rollar (egasi, menejer), kamera ma'lumotlariga alohida ruxsat, ko'rish jurnali (`audit_log`).
- Qabul: ruxsatsiz foydalanuvchi kamera va xom xabarlarni ko'ra olmaydi.

**W3-06 Kunlik Excel (Telegram)**
- Qil: hisobot generatori (openpyxl/XlsxWriter, CPU): varaqlar — Xulosa, Shikoyatlar, Maqtovlar, Javob vaqtlari, Ochiq masalalar, Tahlil kutayotgan. Ko'rsatkichlar Q7 bo'yicha.
- Qabul: bir kunlik fixtures'dan fayl hosil bo'ladi, raqamlar qo'lda hisobga mos, Excel da ochiladi.
- Bog'liq: Q7, W2-09.

**W3-07 Reporter bot**
- Qil: alohida bot tokeni, faqat ruxsat etilgan ichki guruhga yuboradi; 00:10 da Telegram Excel, 09:00 da kamera hisoboti; yetkazish jurnali, qayta urinish; W0-08 signallari ham shu bot orqali admin guruhiga.
- Qabul: ikkala hisobot jadval bo'yicha yetib boradi, noto'g'ri chatga yuborish urinishi rad etiladi.

**W3-08 Servis va zaxira**
- Qil: dashboard va reporter servis sifatida (W0-07 bilan), LAN/HTTPS kirish, sozlamalar zaxirasi.
- Qabul: serverni qayta ishga tushirgach dashboard o'zi ko'tariladi.

## Chegaralar

- Dashboard har doim "AI taxmini" deb ko'rsatadi, hodim bo'yicha baho/ball yo'q.
- Kamera ma'lumoti va xom xabar faqat ruxsatli rollarga.
