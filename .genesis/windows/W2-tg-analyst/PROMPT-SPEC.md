# PROMPT-SPEC — guruh yozishmalarini tahlil qilish prompti tuzilmasi (v1.3: D-33)

Bu yerda prompt **tuzilmasi va qoidalari**. Aniq matn W2-05/W2-06 da haqiqiy namunalar bilan yoziladi, sinaladi va `apps/w2-tg-analyst/prompts/` da `a_v1.md`, `b_v1.md` ko'rinishida versiyalanadi.

## 1. Umumiy tamoyillar

- Ikki bosqich: arzon A (tegishlimi?) va to'liq B (tasnif). B faqat A o'tkazganlar uchun.
- Model faqat berilgan matnga tayanadi. Dalil (xabar id + qisqa iqtibos) majburiy, topilmasa "aniq emas".
- Instruksiya inglizcha (model aniqroq bajaradi), chiqish maydonlari va xulosa o'zbek tilida (lotin), misollar asl tilida (uz lotin/kirill, rus, aralash).
- Chiqish JSON-sxema bilan cheklangan (parse xatosi bo'lmaydi). Bir marta qayta urinish, keyin `dead`.
- Kategoriyalar, glossariy, ishonchlilik chegarasi **konfiguratsiyada**, promptning ichida qattiq yozilmagan.

## 2. Kirish (kontekst paketi) — bazadan yig'iladi

1. Episode matni: har qator `[vaqt] [ROL: CLIENT|EMPLOYEE|UNKNOWN] ism: matn`. Voice: `[voice 0:42] transkript`. Rasm: `[photo] tavsif`.
2. Chatning 24 soatlik yig'ma xulosasi (`chat_context`).
3. Ochiq masalalar ro'yxati (masalan "Mijoz X, yetkazib berish kechikdi, hal bo'lmagan").
4. Kompaniya glossariysi: mahsulot nomlari, o'lchov birliklari, mahalliy so'zlar, ruscha/o'zbekcha sinonimlar.
5. Shu episode uchun oldingi tasnif (takror hisoblamaslik uchun).

## 3. A bosqich skeleti (tegishlilik)

```
ROLE: You filter chat fragments for a construction-materials trading company.
TASK: Answer "yes" ONLY if the fragment is explicitly about this company: its products, orders,
delivery, prices/payment, service or staff. Everything else is "no".
NOT relevant: personal or general talk, jokes, greetings only, politics, family, off-topic; a negative
remark that is not about the company ("yomon ekan" about the weather, a road, another shop) is NOT a complaint.
If it may be about the company but the reference is unclear -> "unsure" (never guess).
Output JSON: {relevant: yes|no|unsure, reason: <=15 words}
```

v1.3 (D-33): "yes" faqat aniq kompaniya haqida bo'lsa. "no" bo'lgan matn statistikaga kirmaydi va 72 soatdan keyin o'chadi (D-27).

## 4. B bosqich skeleti (tasnif)

```
ROLE / DOMAIN: analyst for construction-materials sales chats (uz/ru mixed).
INPUT: episode, 24h summary, open issues, glossary.
RULES: use only given text; quote evidence (message id + short quote); no inference about
private traits (health, religion, politics, family); sarcasm -> lower confidence;
categories come ONLY from the 7 sections in §5 (no "other"/"unclear" class);
if it cannot be placed in a section with confidence -> low confidence (goes to review_queue).
OUTPUT (JSON schema): categories[] (section codes), primary_category, sentiment(-2..2), severity(0..3),
summary_uz (<=25 words), evidence[], entities{product, order_ref, manager, amount, deadline},
needs_action(bool), resolved(yes|no|unknown), confidence(0..1), language
```

## 5. Kategoriyalar (v1.3, D-33; ro'yxat bazada `core.categories`)

Faqat 7 bo'lim, "Boshqa" yo'q (kod — nom):
`delivery` — Dastavka va Logistika · `quality` — Mahsulot Sifati va Nuqsonlar · `completeness` — Komplektatsiya va Hajm ·
`staff_service` — Xodimlar va Xizmat Ko'rsatish · `price_payment` — Narx va To'lovlar · `returns_warranty` — Qaytarish va Kafolat ·
`positive` — Ijobiy Fikrlar.
Shikoyat bo'lim emas, kayfiyat (`sentiment` < 0) bilan belgilanadi. A bosqichdan "yes" o'tgan, lekin hech bir bo'limga
ishonch bilan tushmagan fragment `review_queue` ga tushadi.

## 6. Misollar (few-shot)

8–12 ta haqiqiy anonim misol baholash to'plamidan: shu jumladan qiyin salbiy misollar (hazil, kinoya, juda qisqa javoblar, lotin/kirill aralash) va majburiy ravishda "shikoyatga o'xshash, lekin kompaniyaga aloqasiz" misollar (v1.3, D-33). Misollar to'plamdan alohida: baholashda ishlatilgan misol promptda bo'lmaydi.

## 7. Sifat nazorati

- `prompt_version` + `model_id` har bir natijada saqlanadi, prompt yangilansa oxirgi N kun qayta tahlil qilinadi.
- Baholash to'plamida F1/recall chegarasidan o'tmagan prompt ishga tushmaydi (PLAN.md §6).
- Ishonchlilik < chegara yoki bo'limga ishonch bilan joylashmagan → `review_queue` (v1.3: "unclear" sinfi yo'q); dashboard buni "AI taxmini, tekshirish kerak" deb ko'rsatadi.
- Hodimga qarshi shikoyat faqat dalil iqtibosi bilan ko'rsatiladi; hodim bo'yicha ball yo'q, faqat neytral ko'rsatkichlar (javob vaqti).
