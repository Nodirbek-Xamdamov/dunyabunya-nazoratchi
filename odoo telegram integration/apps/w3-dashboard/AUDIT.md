# W3-01 · Prototip auditi

Sana: 2026-10-26. Prototip: `D:\Demos\odoo telegram integration\dashboard` (faqat o'qildi, ishga tushirilmadi).
Xarita: `MAPPING.md`.

## 1. Texnologiya

- **Streamlit 1.66** (Python), pandas, plotly, openpyxl. Bitta jarayon, port 8502, `run_dashboard.ps1`, o'z `.venv`.
- Ma'lumot: botning **SQLite** bazasi (`data\monitor.db`), `mode=ro` + `query_only`, so'rov vaqt chegarasi, kesh
  (`st.cache_data`, ochiq davr 60 s, yopiq 600 s). Kamera: alohida `camera.db` (SQLite).
- O'z holati: `dashboard\data\state.db` (AI o'zgartirgan ko'rinish, chat tarixi, agentlar).
- AI: OpenAI / Anthropic (chat, agentlar), agentlarning Telegram botlari (`agent_bots.py`).
- Hajm: ~17 300 qator Python (testlar bilan), 14 ta test fayli. Dizayn: `ui/style.css`, `ui/theme.py`, matnlar `ui/i18n.py`.

## 2. Sahifalar

Asosiy: Umumiy · Guruhlar (+ guruh sahifasi) · Bo'limlar (+ toifa sahifasi) · AI agentlar.
Tahlil: Javob tezligi · Xodimlar · Dinamika · Qidiruv. Tizim: Hisobotlar · Tizim.
Mening sahifalarim (chatda yaratilgan). Yangi kamera: Jonli (ko'rinadi); Bugun, Kunlar, Sozlash (kodda bor, yashirin).
Har sahifa `layout.Block` bloklaridan iborat (jami ~90 blok) — CEO ularni chat orqali yashiradi/ko'chiradi.

## 3. Meyorlar (PLAN.md, P-12)

| # | Meyor | Baho | Asos |
|---|---|---|---|
| 1 | Kod tuzilishi o'qiladigan | ✅ yaxshi | `core/` (ma'lumot, ta'riflar), `ui/` (komponentlar), `views/` (sahifa = bloklar). Barcha ko'rsatkich ta'riflari bitta joyda (`core/metrics.py`), docstringlar, testlar |
| 2 | Ma'lumot qatlami UI dan ajratilgan | ✅ yaxshi | Telegram SQL'ning hammasi `core/queries.py` da (~500 qator), sahifalar faqat `data.get("nom", ...)` chaqiradi; ulanish `core/db.py` da. Istisno: kamera `camera/store.py` (alohida, o'zi yozadi ham), AI vositalari `assistant/data_tools.py` |
| 3 | Login va rollar qo'shish oson | ⚠️ o'rtacha | Hozir bitta umumiy parol (`ui/auth.py`, bitta `gate`). Sahifalar bitta registrda (`app.py` `SPECS`), shuning uchun rolga qarab sahifa/blokni yashirish oson. Foydalanuvchi jadvali, sessiya, `audit_log` yozish — yangidan qo'shiladi |
| 4 | Mock ma'lumotga qattiq bog'lanmagan | ✅ | Demo — alohida SQLite fayl (`demo/build_demo.py`, `DEMO_MODE=1`), kod bir xil so'rovlar bilan ishlaydi. Raqamlar kodga yozilmagan |

## 4. Asosiy xavflar (qayta yozish emas, lekin ish hajmi)

1. **Ma'nosi o'zgaradi:** xabar bo'yicha tasnif → episode bo'yicha (`analyses`). `queries.py` to'liq qayta yoziladi
   (SQLite → PostgreSQL view'lar), `metrics.py` ta'riflari yangilanadi. Sahifalar va dizayn o'zgarmaydi.
2. **Javob tezligi** (prototipning eng kuchli qismi) uchun shartnomada obyekt yo'q — `MAPPING.md` §3.1. Hal
   bo'lmasa Javob tezligi, Xodimlar sahifalari va 3 ta KPI bo'sh qoladi.
3. **Kamera sahifalari boshqa modelda** (stol + ismli xodim + namoz). Reja: eshik + "Hodim N" + xonada bo'lish.
   Bugun/Kunlar sahifalarining **ko'rinishi** saqlanadi, hisob qismi (`camera/report.py`, `store.py`) yangi
   view'larga qayta yoziladi. «Sozlash» bazaga yozadi — W3 da qolmaydi (W4 ga). «Jonli» — W4/maxfiylik qarori.
4. **AI chat, agentlar, agent botlari** W3 qoidalariga zid ("modelni chaqirmaydi", Telegramga faqat reporter bot).
   Kod alohida modullarda (`assistant/`, `ui/chat.py`, `views/agents*.py`, `agent_bots.py`) — o'chirish/ajratish oson.
5. **Rejada bor, prototipda yo'q:** "AI taxmini" belgisi + ishonchlilik, asl xabarga havola, jiddiylik filtri,
   review navbati sahifasi, rollar, ko'rish jurnali. Bular mavjud komponentlarga qo'shiladi.
6. Streamlit cheklovi: har foydalanuvchi uchun server-side render; 200+ guruh va bir necha foydalanuvchi uchun yetadi
   (kesh bor). Tashqi API kerak bo'lsa (masalan mobil ilova) — keyin alohida.

## 5. Tavsiya

**Yangilash (standart, P-12).** 4 meyordan 3 tasi to'liq bajarilgan, 3-meyor o'rtacha, lekin bitta joyga (`auth.gate`
+ sahifa registri) qo'shiladi. Qayta yozish dizaynni, ~90 blokni, CEO o'zgartirishlar tizimini va testlarni
yo'qotadi, bunga sabab yo'q.

Yangilash doirasi (W3-02 dan boshlab, foydalanuvchi tasdiqlasa):
- `core/db.py` → PostgreSQL, `w3_dashboard` roli, faqat view'lar; `core/queries.py` → view'lar ustidagi SQL;
  `core/metrics.py` → episode ta'riflari.
- Kamera: `camera/store.py` + `camera/report.py` qayta yoziladi (`v_attendance_*`), sahifa ko'rinishi saqlanadi.
- Qo'shiladi: "AI taxmini" + ishonchlilik, havola, review navbati, login/rollar, `audit_log`.

Qaroringiz kerak bo'lgan joylar:
1. Yangilash yoki qayta yozish (tavsiya: yangilash).
2. AI chat / agentlar / agent botlari: W3 dan olib tashlansinmi, alohida ilova bo'lib qolsinmi (rejaga kiritish
   `DECISIONS.md` orqali)?
3. Kamera: namoz vaqti, ish jadvali, stol bo'yicha hisob kerakmi (rejada yo'q) yoki faqat D-11 (kirish, ketish,
   xonada jami)?
4. W3 kodi qayerda turadi: `dashboard\` dan `apps\w3-dashboard\` ga nusxa olib (ishlab turgan dashboard'ga
   tegmasdan) yangilanadimi? Tavsiya: ha, nusxa.
