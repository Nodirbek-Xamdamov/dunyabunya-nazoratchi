"""Xodimlar kirdi-chiqdi paneli. Jonli video yo'q: jadval va kirish/chiqishdagi 5 soniyalik videolar.

Ma'lumotni fonda ishlaydigan xizmat yozadi (python -m nazorat.xizmat); bu sahifa o'qiydi va
odamlarning turini (xodim / mehmon) belgilaydi. Jadvaldagi odamni bossangiz — uning shu kungi videolari.
"""
import json
import time
from datetime import date, datetime

import pandas as pd
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


# ---------------------------------------------------------------- yon panel
st.sidebar.header("Sozlamalar")
kun = st.sidebar.date_input("Sana", value=date.today(), format="DD.MM.YYYY")
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


def jadval(ids, key):
    """Jadval; bosilgan qatordagi odamning raqamini qaytaradi."""
    q = []
    for hid in ids:
        k = hisob[hid]
        q.append({
            "Hodim": nomi(hid),
            "Keldi": hm(k.keldi),
            "Ketdi": hm(k.ketdi) if k.ketdi else "hali xonada",
            "Chiqib ketishlar": len(k.chiqishlar),
            "Tashqarida": soat_daqiqa(k.tashqarida),
            "Umumiy (keldi→ketdi)": soat_daqiqa(k.umumiy),
            "Xonada": soat_daqiqa(k.xonada),
        })
    tanlov = st.dataframe(pd.DataFrame(q), use_container_width=True, hide_index=True,
                          on_select="rerun", selection_mode="single-row", key=key)
    qatorlar = tanlov.selection.rows
    return ids[qatorlar[0]] if qatorlar else None


def videolar(hid):
    """Odamning shu kungi barcha kirish/chiqish videolari, vaqt tartibida."""
    k = hisob[hid]
    ketdi_ts = k.ketdi
    keldi_ts = k.keldi
    roy = []
    for e in ev_by.get(hid, []):
        if e["tur"] == "kirish":
            yorliq = "Keldi" if abs(e["ts"] - keldi_ts) < 0.5 else "Qaytib kirdi"
        else:
            yorliq = "Ketdi" if ketdi_ts and abs(e["ts"] - ketdi_ts) < 0.5 else "Chiqdi"
        roy.append((yorliq, e))
    st.markdown(f"**{nomi(hid)}** — keldi {hms(k.keldi)}, "
                f"{'ketdi ' + hms(k.ketdi) if k.ketdi else 'hali xonada'} · umumiy {soat_daqiqa(k.umumiy)} · "
                f"tashqarida {soat_daqiqa(k.tashqarida)} · **xonada {soat_daqiqa(k.xonada)}**")
    bor = [(y, e) for y, e in roy if e["klip"] and (config.CLIPS / e["klip"]).exists()]
    if not bor:
        st.caption("Bu kunda video yo'q.")
        return
    ustun = st.columns(4)
    for i, (yorliq, e) in enumerate(bor):
        with ustun[i % 4]:
            st.caption(f"{yorliq} · {hms(e['ts'])}")
            st.video(str(config.CLIPS / e["klip"]))
    if len(bor) < len(roy):
        st.caption(f"{len(roy) - len(bor)} ta hodisaning videosi yo'q (xizmat qayta ishga tushganda yoki "
                   f"{config.KLIP_SAQLASH_KUN} kundan eski).")


# ---------------------------------------------------------------- xodimlar
st.title("Xodimlar: kirdi-chiqdi va xonadagi vaqt")
if ogohlantirish:
    st.error(ogohlantirish, icon="🚨")
tartib = sorted(hisob, key=lambda i: hisob[i].keldi)
xodimlar = [i for i in tartib if odamlar[i]["tur"] == "xodim"]
mehmonlar = [i for i in tartib if odamlar[i]["tur"] == "mehmon"]
nomalumlar = [i for i in tartib if odamlar[i]["tur"] == "nomalum"]

st.subheader("Xonaning xodimlari")
if xodimlar:
    st.caption("Xodimning qatorini bosing — shu kun kelgan, chiqib-kirgan va ketgan paytdagi 5 soniyalik videolar ochiladi.")
    hid = jadval(xodimlar, "j_xodim")
    if hid is not None:
        videolar(hid)
else:
    st.info("Bu kunda xodim yozuvi yo'q.")
st.caption(f"Xodim — stolida doimiy o'tiradigan odam: bir kunda joyida {config.XODIM_OTIRISH_S // 60} daqiqadan "
           "ko'p o'tirgan odam avtomatik xodim bo'ladi. Farrosh, qorovul, boshqa xonadan kelganlar xodim bo'lmaydi. "
           f"Videolar {config.KLIP_SAQLASH_KUN} kun saqlanadi.")

# ---------------------------------------------------------------- boshqa kirganlar
if nomalumlar:
    st.subheader(f"Boshqa kirganlar — xodim emas ({len(nomalumlar)})")
    st.caption("Kirib-chiqib yurganlar (farrosh, qorovul, boshqa xonadan kelganlar). Qatorini bossangiz — "
               "videolari va uni belgilash tugmalari chiqadi.")
    hid = jadval(nomalumlar, "j_nomalum")
    if hid is not None:
        videolar(hid)
        ism = st.text_input("Ismi", key=f"ism_{hid}", placeholder="Ismi (ixtiyoriy)")
        a, b, c, d1, d2 = st.columns([1, 1, 1, 2, 1])
        if a.button("Xodim", key=f"x_{hid}", use_container_width=True, type="primary"):
            db.set_tur(con, hid, "xodim", ism or None)
            st.rerun()
        if b.button("Mehmon", key=f"m_{hid}", use_container_width=True,
                    help="Boshqa xonadan kelgan — xodimlar jadvaliga tushmaydi"):
            db.set_tur(con, hid, "mehmon", ism or None)
            st.rerun()
        if c.button("Olib tashlash", key=f"o_{hid}", use_container_width=True,
                    help="Odam emas yoki xato aniqlangan"):
            db.hide(con, hid)
            st.rerun()
        boshqalar = [i for i in odamlar if i != hid and odamlar[i]["tur"] != "nomalum"]
        if boshqalar:
            nishon = d1.selectbox("Bu aslida", boshqalar, format_func=nomi, key=f"b_{hid}",
                                  label_visibility="collapsed")
            if d2.button("Shu odam", key=f"bb_{hid}", use_container_width=True):
                db.merge(con, hid, nishon)
                st.rerun()

# ---------------------------------------------------------------- mehmonlar
if mehmonlar:
    st.subheader("Boshqa xonadan kelganlar (mehmonlar)")
    hid = jadval(mehmonlar, "j_mehmon")
    if hid is not None:
        videolar(hid)

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
