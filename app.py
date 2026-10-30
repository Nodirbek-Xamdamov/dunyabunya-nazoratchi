"""Xodimlar kirdi-chiqdi paneli. Jonli video yo'q: jadval va kirish/chiqishdagi 5 soniyalik videolar.

Ma'lumotni fonda ishlaydigan xizmat yozadi (python -m nazorat.xizmat); bu sahifa o'qiydi va
odamlarning turini (xodim / mehmon) belgilaydi. Qator oxiridagi «⋮» -> «Kadrlarni ko'rish» — shu odamning
o'sha kungi videolari (?hodim=ID&sana=YYYY-MM-DD).
"""
import json
import time
from datetime import date, datetime

import streamlit as st

from nazorat import config, db
from nazorat.hisob import kun_hisobi, soat_daqiqa

st.set_page_config(page_title="Xodimlar: kirdi-chiqdi", layout="wide")


@st.cache_resource
def _con():
    return db.connect()


con = _con()


def hm(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "—"


def hms(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "—"


def nomi(hid):
    r = odamlar.get(hid)
    n = r["nom"] if r else None
    return f"#{hid} {n}" if n else f"Hodim {hid}"


qp = st.query_params
try:
    tanlangan = int(qp["hodim"]) if "hodim" in qp else None
    qp_kun = datetime.strptime(qp["sana"], "%Y-%m-%d").date() if "sana" in qp else None
except ValueError:
    tanlangan, qp_kun = None, None

# ---------------------------------------------------------------- yon panel
st.sidebar.header("Sozlamalar")
kun = st.sidebar.date_input("Sana", value=qp_kun or date.today(), format="DD.MM.YYYY")
kun_s = kun.strftime("%Y-%m-%d")

holat, holat_ts = db.get_holat(con, "xizmat")
ogohlantirish = None
if holat and time.time() - holat_ts < 30:
    h = json.loads(holat)
    st.sidebar.success(f"Xizmat ishlayapti · {h['fps']} kadr/s · kamera {'ulangan' if h['kamera'] else 'UZILGAN'}")
    if not h["kamera"]:
        ogohlantirish = "Kamera uzilgan — hozir kirdi-chiqdi yozilmayapti. Kamera va tarmoqni tekshiring."
else:
    st.sidebar.error("Kuzatuv xizmati ishlamayapti" + (f" (oxirgi: {hm(holat_ts)})" if holat_ts else ""))
    ogohlantirish = ("Kuzatuv xizmati ishlamayapti" + (f" (oxirgi xabar {hm(holat_ts)})" if holat_ts else "")
                     + " — kirdi-chiqdi yozilmayapti.")
if st.sidebar.button("Yangilash", use_container_width=True):
    st.rerun()

odamlar = {r["id"]: r for r in db.hodimlar(con)}
events = [e for e in db.events_for_day(con, kun_s) if e["hodim_id"] in odamlar]
hozir = time.time() if kun == date.today() else datetime.combine(kun, datetime.max.time()).timestamp()
hisob = kun_hisobi(events, hozir)
ev_by = {}
for e in events:
    ev_by.setdefault(e["hodim_id"], []).append(e)

USTUNLAR = ["Hodim", "Keldi", "Ketdi", "Chiqib ketishlar", "Tashqarida", "Umumiy (keldi→ketdi)", "Xonada", ""]
KENGLIK = [2, 1, 1.2, 1.1, 1.1, 1.4, 1.2, 0.5]


def korish(hid):
    st.query_params.from_dict({"hodim": str(hid), "sana": kun_s})


def jadval(ids, key, amallar=False):
    """Jadval; har qator oxirida «⋮» menyusi: «Kadrlarni ko'rish» (va noma'lumlar uchun belgilash)."""
    with st.container(border=True):
        for c, n in zip(st.columns(KENGLIK), USTUNLAR):
            c.markdown(f"**{n}**")
        for hid in ids:
            k = hisob[hid]
            qator = st.columns(KENGLIK, vertical_alignment="center")
            for c, v in zip(qator, [nomi(hid), hm(k.keldi), hm(k.ketdi) if k.ketdi else "hali xonada",
                                    len(k.chiqishlar), soat_daqiqa(k.tashqarida), soat_daqiqa(k.umumiy),
                                    soat_daqiqa(k.xonada)]):
                c.write(v)
            with qator[-1].popover("⋮"):
                st.button("Kadrlarni ko'rish", key=f"{key}_v_{hid}", icon=":material/movie:",
                          on_click=korish, args=(hid,), use_container_width=True)
                if amallar:
                    if st.button("Xodim deb belgilash", key=f"{key}_x_{hid}", use_container_width=True):
                        db.set_tur(con, hid, "xodim")
                        st.rerun()
                    if st.button("Mehmon deb belgilash", key=f"{key}_m_{hid}", use_container_width=True,
                                 help="Boshqa xonadan kelgan — xodimlar jadvaliga tushmaydi"):
                        db.set_tur(con, hid, "mehmon")
                        st.rerun()
                    if st.button("Olib tashlash", key=f"{key}_o_{hid}", use_container_width=True,
                                 help="Odam emas yoki xato aniqlangan"):
                        db.hide(con, hid)
                        st.rerun()


def videolar_sahifasi(hid):
    """Faqat bitta odamning tanlangan kundagi kirish/chiqish videolari."""
    if st.button("Orqaga", icon=":material/arrow_back:"):
        st.query_params.clear()
        st.rerun()
    st.title(f"{nomi(hid)} — {kun.strftime('%d.%m.%Y')} videolari")
    if hid not in hisob:
        st.info("Bu kunda bu odamning kirish-chiqishi yo'q.")
        return
    k = hisob[hid]
    st.markdown(f"Keldi **{hms(k.keldi)}** · {'ketdi **' + hms(k.ketdi) + '**' if k.ketdi else 'hali xonada'} · "
                f"umumiy {soat_daqiqa(k.umumiy)} · tashqarida {soat_daqiqa(k.tashqarida)} · "
                f"**xonada {soat_daqiqa(k.xonada)}**")
    roy = []
    for e in ev_by.get(hid, []):
        if e["tur"] == "kirish":
            yorliq = "Keldi" if abs(e["ts"] - k.keldi) < 0.5 else "Qaytib kirdi"
        else:
            yorliq = "Ketdi" if k.ketdi and abs(e["ts"] - k.ketdi) < 0.5 else "Chiqdi"
        roy.append((yorliq, e))
    bor = [(y, e) for y, e in roy if e["klip"] and (config.CLIPS / e["klip"]).exists()]
    if not bor:
        st.info("Bu kunda video yo'q.")
        return
    ustun = st.columns(3)
    for i, (yorliq, e) in enumerate(bor):
        with ustun[i % 3]:
            st.markdown(f"**{yorliq}** · {hms(e['ts'])}")
            st.video(str(config.CLIPS / e["klip"]))
    if len(bor) < len(roy):
        st.caption(f"{len(roy) - len(bor)} ta hodisaning videosi yo'q (xizmat qayta ishga tushganda yoki "
                   f"{config.KLIP_SAQLASH_KUN} kundan eski).")


if ogohlantirish:
    st.error(ogohlantirish, icon="🚨")

if tanlangan is not None:
    videolar_sahifasi(tanlangan)
    st.stop()

# ---------------------------------------------------------------- xodimlar
st.title("Xodimlar: kirdi-chiqdi va xonadagi vaqt")
tartib = sorted(hisob, key=lambda i: hisob[i].keldi)
xodimlar = [i for i in tartib if odamlar[i]["tur"] == "xodim"]
mehmonlar = [i for i in tartib if odamlar[i]["tur"] == "mehmon"]
nomalumlar = [i for i in tartib if odamlar[i]["tur"] == "nomalum"]

st.subheader("Xonaning xodimlari")
if xodimlar:
    jadval(xodimlar, "j_xodim")
else:
    st.info("Bu kunda xodim yozuvi yo'q.")
st.caption("Qator oxiridagi «⋮» → «Kadrlarni ko'rish»: shu kun kelgan, chiqib-kirgan va ketgan paytdagi 5 soniyalik "
           f"videolar. Xodim — stolida doimiy o'tiradigan odam (bir kunda {config.XODIM_OTIRISH_S // 60} daqiqadan "
           f"ko'p); farrosh, qorovul, boshqa xonadan kelganlar xodim bo'lmaydi. Videolar {config.KLIP_SAQLASH_KUN} "
           "kun saqlanadi.")

if nomalumlar:
    st.subheader(f"Boshqa kirganlar — xodim emas ({len(nomalumlar)})")
    jadval(nomalumlar, "j_nomalum", amallar=True)

if mehmonlar:
    st.subheader("Boshqa xonadan kelganlar (mehmonlar)")
    jadval(mehmonlar, "j_mehmon")

# ---------------------------------------------------------------- boshqarish
with st.expander("Odamlarni boshqarish (ism, tur, birlashtirish)"):
    if not odamlar:
        st.write("Hali hech kim yo'q.")
    else:
        ids = list(odamlar)
        c1, c2 = st.columns(2)
        with c1:
            hid = st.selectbox("Odam", ids, format_func=lambda i: f"{nomi(i)} ({odamlar[i]['tur']})", key="bosh_h")
            ism = st.text_input("Ismi", value=odamlar[hid]["nom"] or "", key=f"bosh_ism_{hid}")
            tur = st.radio("Turi", db.TURLAR, index=db.TURLAR.index(odamlar[hid]["tur"]), horizontal=True,
                           format_func={"xodim": "Xonaning xodimi", "mehmon": "Mehmon (boshqa xona)",
                                        "nomalum": "Noma'lum"}.get, key=f"bosh_tur_{hid}")
            if st.button("Saqlash"):
                db.set_tur(con, hid, tur, ism)
                st.rerun()
        with c2:
            manba = st.selectbox("Qaysi odam", ids, format_func=nomi, key="bir_m")
            nishon = st.selectbox("Kimga qo'shilsin (bir odam ekan)", [i for i in ids if i != manba],
                                  format_func=nomi, key="bir_n")
            if st.button("Birlashtirish") and nishon:
                db.merge(con, manba, nishon)
                st.rerun()
            ol = st.selectbox("Olib tashlanadigan", ids, format_func=nomi, key="ol_h")
            if st.button("Olib tashlash"):
                db.hide(con, ol)
                st.rerun()
