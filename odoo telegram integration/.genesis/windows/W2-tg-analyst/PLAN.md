# W2 TG-Analyst — reja (qotirilgan, v1.3: D-33)

Papka: `apps/w2-tg-analyst`. Boshlash sharti: **W0-04, W0-05 `done`**; W2-01 uchun Q6 namunalari; GPU qadamlari uchun W0-06.
Mas'uliyat: ovoz→matn, tegishlilik filtri, tasnif, 24 soatlik kontekst, kunlik agregat.
Tegmaydi: Telegramga ulanish (W1), dashboard va Excel (W3), model yuklash/chiqarish (W0 gate). Telegramga yozmaydi.
Prompt tuzilmasi: `PROMPT-SPEC.md` (qotirilgan). Aniq prompt matnlari W2-05 va W2-06 da yoziladi va `prompts/` da versiyalanadi.

## Qadamlar

**W2-01 Baholash to'plami**
- Qil: foydalanuvchi bilan 200–300 ta haqiqiy (anonimlashtirilgan) suhbat bo'lagini qo'lda belgilash: tegishli/tegishli emas, kategoriya, kayfiyat, jiddiylik. Belgilash jadvali va metrikalar (har sinf uchun precision/recall/F1).
- Natija: `apps/w2-tg-analyst/eval/` da to'plam va baholash buyrug'i.
- v1.3 (D-33): kategoriya = faqat 7 bo'lim (Dastavka va Logistika; Mahsulot Sifati va Nuqsonlar; Komplektatsiya va Hajm; Xodimlar va Xizmat Ko'rsatish; Narx va To'lovlar; Qaytarish va Kafolat; Ijobiy Fikrlar), "Boshqa" yo'q. To'plamga "shikoyatga o'xshash, lekin kompaniyaga aloqasiz" salbiy namunalar majburiy kiritiladi.
- Qabul: to'plamda har bir asosiy sinfdan kamida 20 ta misol (kam bo'lsa nechtasi yetishmasligi yozilgan), salbiy namunalar soni yozilgan, belgilash qoidalari yozilgan.
- Bog'liq: Q6.

**W2-02 Ovoz → matn tanlovi (bake-off)**
- Qil: 50–100 ta haqiqiy voice (o'zbek, rus, aralash) bilan provayderlarni solishtirish: ElevenLabs Scribe, AssemblyAI, Gladia, Aisha, Gemma 4 12B audio. O'lchov: so'z xatosi (WER) qo'lda tuzatilgan 20 ta namunada, tezlik, narx/daqiqa, chet elga uzatish talabi. Tanlangan provayder uchun adapter va zaxira zanjiri.
- Qabul: natija jadvali `logs/W2.md` da, tanlov asoslangan; adapter `transcribe` job'ini bajaradi va `transcripts` ga yozadi.

**W2-03 Lokal Gemma runtime**
- Qil: W0-01 tavsiyasi bo'yicha Gemma 4 12B ni servis qilish, JSON-sxemali chiqish, rasm kirishi (narx ro'yxati, yetkazib berish, nuqson fotosi) sinovi, bir vaqtdagi so'rovlar soni va tezlik o'lchovi.
- Qabul: 100 ta sinov so'rovning 100% i parse bo'ladigan JSON qaytaradi; o'tkazuvchanlik (xabar/daqiqa) va VRAM `logs/W2.md` da.

**W2-04 Suhbatni bo'laklash**
- Qil: xabarlarni `episodes` ga ajratish: vaqt oralig'i (standart 30 daqiqa), reply zanjiri, qatnashchilar; voice matni va rasm tavsifi xabar o'rniga kiradi. Qoida asosida, LLM'siz.
- Qabul: 20 ta qo'lda tekshirilgan suhbatda bo'laklash mantiqiy (kamida 90% to'g'ri), qayta ishlaganda bir xil natija.

**W2-05 A bosqich: kompaniyaga tegishlimi**
- Qil: `PROMPT-SPEC.md` §3 bo'yicha prompt yozish, baholash to'plamida sinash, `relevance` (yes/no/unsure) yozish; "no" bo'lsa matn 72 soatdan keyin o'chiriladi (P-05, D-27). v1.3 (D-33): "yes" faqat aniq kompaniya haqida (mahsulot, buyurtma, yetkazish, narx/to'lov, xizmat, hodimlar) bo'lsa; aloqasiz "yomon ekan" kabi gaplar "no".
- Qabul: to'plamda "tegishli"ni o'tkazib yuborish (false negative) ≤ 3%; xato misollar tahlili `logs/W2.md` da.

**W2-06 B bosqich: tasnif**
- Qil: `PROMPT-SPEC.md` §4 bo'yicha prompt, kategoriya (v1.3, D-33: faqat 7 bo'lim, `core.categories`; "Boshqa"/other sinfi yo'q)/kayfiyat/jiddiylik/dalil/obyektlar chiqishi, ishonchlilik chegarasi; ishonch past bo'lsa `review_queue` ga yo'naltirish.
- Qabul: PLAN.md §6 dagi aniqlik mezonlari (F1 ≥ 0.75, shikoyat recall ≥ 0.85) bajarilgan yoki sabab va tuzatish rejasi yozilgan.

**W2-07 24 soatlik kontekst**
- Qil: har chat uchun yig'ma xulosa va ochiq masalalar ro'yxatini bazada yuritish; tahlilda kontekst bazadan yig'iladi (model xotirasida emas); kechasi siqish.
- Qabul: oldingi kunning shikoyati bugungi javobga bog'lanadi (test stsenariy); kontekst hajmi chegaradan oshmaydi.

**W2-08 Media tahlili**
- Qil: voice → transkript → shu quvur; rasm → faktlar (matn, mahsulot, nuqson); video → audio transkript + chegaralangan kalit kadrlar. Qisqa/noaniq voice uchun "aniq emas" belgisi.
- Qabul: har tur uchun 10 ta namunada natija episodga qo'shiladi va asl mediaga havola saqlanadi.

**W2-09 Kunlik agregatsiya**
- Qil: `daily_stats`: kategoriya bo'yicha soni, ochiq/yopilgan masalalar, mijoz yozdi → hodim javob berdi vaqti (Q7 bo'yicha), "tahlil kutayotgan: N". `daily_close` job'i.
- Qabul: bir kunlik fixtures bo'yicha qo'lda hisoblangan raqamlar bilan mos.

**W2-10 Sifat sikli**
- Qil: to'liq baholash, drift nazorati (haftalik qayta baholash), odam tuzatishi uchun `review_queue` oqimi, qayta ishlash buyrug'i (prompt yangilansa oxirgi N kunni qayta tahlil).
- Qabul: baholash hisoboti; chegaralar bajarilmasa dashboard "sinov rejimi" belgisi bilan chiqadi.

## Chegaralar

- Modelga xom tarixni emas, tayyorlangan kontekstni beradi.
- Har bir `analyses` qatorida `prompt_version` va `model_id` bo'lishi shart.
- Shaxsiy xususiyatlar (salomatlik, din, siyosat, oilaviy) haqida xulosa chiqarmaydi.
