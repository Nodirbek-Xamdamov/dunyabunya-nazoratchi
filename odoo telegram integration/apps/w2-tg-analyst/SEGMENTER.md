# W2-04 Suhbatni bo'laklash (episodes) — qoidalar v1 (`w2-seg-v1`)

Qoida asosida, LLM'siz, deterministik. Kod: `w2analyst/segmenter.py` (sof mantiq), `w2analyst/store.py` (baza),
`w2analyst/render.py` (model uchun episod matni). Sozlama: `app.toml` `[segmenter]`.

## Qoidalar

| # | Qoida |
|---|---|
| R1 | Xabarlar `(sent_at, tg_message_id)` tartibida. Episod Toshkent yarim tunini (hisobot kuni, P-07) va forum mavzusini (`thread_id`) kesib o'tmaydi. Manbalar alohida. |
| R2 | `service` xabarlar va botlar (`sender_is_bot`) hech qaysi episodga kirmaydi. |
| R3 | Reply asl xabarning episodiga qo'shiladi (shu kun, shu mavzu, asl episod oxiridan `reply_window_hours`=12 ichida). Mavzu ildiziga "reply" (forum'da `reply_to = thread_id`) haqiqiy reply emas. |
| R4 | Aks holda: oldingi episod oxiridan `gap_minutes`=30 ichida bo'lsa (≤ 30, chegara kiradi) shu episod. |
| R5 | yoki `extended_gap_minutes`=60 ichida: (a) yozuvchi episodda qatnashgan va oxirgi yozgan u emas (suhbatdosh qaytdi), yoki (b) hodim (`people.role=employee`) javob yozdi, oxirgi xabar mijozniki edi (reply bosilmagan javob). |
| R4/R5 tanlov | Bir nechta yaqin episod bo'lsa (reply eski episodni jonlantirganda), yozuvchi qatnashgani, keyin eng oxirgisi tanlanadi. |
| R6 | Episod `max_episode_hours`=6 dan uzun (R3 bundan mustasno) va `max_messages`=200 dan ko'p bo'lmaydi: chegaraga yetsa yangi episod. |
| R7 | Kech kelgan (tartibsiz) xabar: vaqti ichiga tushgan episodga, keyin oldingisiga (R4/R5), keyin 30 daqiqa ichidagi keyingisiga; bo'lmasa yangi episod. Saqlangan episodlar birlashtirilmaydi va bo'linmaydi. |

Voice/video matni (`transcripts`, `is_primary` birinchi) va rasm tavsifi (W2-08) xabar o'rniga kiradi:
`[10:07 #124] [EMPLOYEE] Vali: [voice 0:42] transkript`. Xabar id har qatorda: dalil iqtibosi uchun (PROMPT-SPEC §1).

## Bazaga yozish

- Har `(manba, kun)` uchun, shu versiyadagi episodga kirmagan xabar bo'lsa: `pg_advisory_xact_lock(72040, source_id)`,
  kunning saqlangan episodlari qayta yuklanadi, yangi xabarlar vaqt tartibida qo'shiladi, yangi episod INSERT, o'zgargani UPDATE.
- `w2_analyst` da DELETE yo'q va kerak ham emas: episod faqat uzayadi. Qayta ishlash yangi xabar bo'lmasa hech narsa o'zgartirmaydi.
- Versiya `segmenter_version` da; boshqa versiyalar (masalan `fixture-v1`) e'tiborga olinmaydi. Qoida/sozlama o'zgarsa versiya oshiriladi.
- Ishga tushirish: `.venv\Scripts\python.exe -E -m w2analyst segment --db genesis_test [--source N]`.
  `genesis` ga `--prod` siz yozmaydi (W2-05 dan keyin ishchi chaqiradi).

## Chegara holatlar va ma'lum cheklovlar

- 30 daqiqa ichidagi ikki parallel suhbat bitta episod bo'ladi (S13). Episodda bir nechta kategoriya bo'lishi mumkin (B bosqich `categories[]`).
- Yarim tunda suhbat bo'linadi (S10): hisobot kuni uchun ataylab. Reply ham kunni kesib o'tmaydi.
- Reply'siz, 60 daqiqadan kech javob alohida episod bo'ladi; bog'lash W2-07 (24 soat kontekst) va W2-09 (`response_pairs`) ishi.
- Kech kelgan xabar ikki episod orasini to'ldirsa ham ular birlashmaydi (oldingisiga qo'shiladi).
- Tahlil qilingan episodga keyin xabar qo'shilsa `episodes.updated_at` yangilanadi; qayta tahlilni W2-05/W2-10 hal qiladi.
- Ochiq/yopiq episod: oxirgi xabardan `gap_minutes` o'tmagan episod hali uzayishi mumkin (A/B bosqich yopiqlarini oladi).
- `people.role` bo'lmasa (noma'lum) R5(b) ishlamaydi; hodimlar ro'yxati to'ldirilgach yaxshilanadi.

## Isbot

Sun'iy, qo'lda belgilangan 20 suhbat (`tests/scenarios.py`): 19/20 = 95% (yagona xato: S13). Haqiqiy suhbatlarda tekshiruv
W2-01 to'plami bilan qilinadi.
