"""Xodimlar kirdi-chiqdi paneli. Jonli video yo'q: jadval, chiqish/kirish kadrlari va 5 soniyalik kliplar.

Ma'lumotni fonda ishlaydigan xizmat yozadi (python -m nazorat.xizmat); bu sahifa o'qiydi va
odamlarning turini (xodim / mehmon) belgilaydi.
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


def rasm(rel, sarlavha=None):
    if rel and (config.CLIPS / rel).exists():
        st.image(str(config.CLIPS / rel), caption=sarlavha, use_container_width=True)
    elif sarlavha:
        st.caption(f"{sarlavha}: kadr yo'q")


def video(rel, sarlavha):
    if rel and (config.CLIPS / rel).exists():
        st.caption(sarlavha)
        st.video(str(config.CLIPS / rel))


def portret(hid, width=110):
    r = odamlar.get(hid)
    if r and r["rasm"] and (config.RASMLAR / r["rasm"]).exists():
        st.image(str(config.RASMLAR / r["rasm"]), width=width)


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
otirdi = db.otirish_kun(con, kun_s)
ev_by = {}
for e in events:
    ev_by.setdefault(e["hodim_id"], []).append(e)


def hodisa(hid, tur, ts):
    return next((e for e in ev_by.get(hid, []) if e["tur"] == tur and abs(e["ts"] - ts) < 0.5), None)


def jadval(ids):
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
            "Joyida o'tirdi": soat_daqiqa(otirdi.get(hid, 0)),
        })
    st.dataframe(pd.DataFrame(q), use_container_width=True, hide_index=True)


def tafsilot(hid):
    k = hisob[hid]
    c0, c1 = st.columns([1, 9])
    with c0:
        portret(hid, 90)
    with c1:
        st.markdown(f"**{nomi(hid)}** — keldi {hms(k.keldi)}, "
                    f"{'ketdi ' + hms(k.ketdi) if k.ketdi else 'hali xonada'} · umumiy {soat_daqiqa(k.umumiy)} · "
                    f"tashqarida {soat_daqiqa(k.tashqarida)} · **xonada {soat_daqiqa(k.xonada)}**")
        tugmalar = st.columns(6)
        i = 0

        def tugma(label, chiq_e, kir_e):
            nonlocal i
            with tugmalar[i % 6]:
                with st.popover(label, use_container_width=True):
                    a, b = st.columns(2)
                    if chiq_e is not None:
                        with a:
                            rasm(chiq_e["rasm"], f"Chiqib ketayotgan payt · {hms(chiq_e['ts'])}")
                            video(chiq_e["klip"], "Chiqish videosi (5 s)")
                    if kir_e is not None:
                        with b if chiq_e is not None else a:
                            rasm(kir_e["rasm"], f"Kirgan payt · {hms(kir_e['ts'])}")
                            video(kir_e["klip"], "Kirish videosi (5 s)")
            i += 1

        tugma(f"Keldi {hm(k.keldi)}", None, hodisa(hid, "kirish", k.keldi))
        for c in k.chiqishlar:
            tugma(f"Chiqdi {hm(c.chiqdi)} → {hm(c.qaytdi)} · {soat_daqiqa(c.davomiylik)}",
                  hodisa(hid, "chiqish", c.chiqdi), hodisa(hid, "kirish", c.qaytdi))
        if k.ketdi:
            tugma(f"Ketdi {hm(k.ketdi)}", hodisa(hid, "chiqish", k.ketdi), None)
    st.divider()


# ---------------------------------------------------------------- xodimlar
st.title("Xodimlar: kirdi-chiqdi va xonadagi vaqt")
if ogohlantirish:
    st.error(ogohlantirish, icon="🚨")
st.caption(f"Kirish va chiqish kliplari {config.KLIP_SAQLASH_KUN} kun saqlanadi, keyin o'chiriladi.")
tartib = sorted(hisob, key=lambda i: hisob[i].keldi)
xodimlar = [i for i in tartib if odamlar[i]["tur"] == "xodim"]
mehmonlar = [i for i in tartib if odamlar[i]["tur"] == "mehmon"]
nomalumlar = [i for i in tartib if odamlar[i]["tur"] == "nomalum"]

st.subheader("Xonaning xodimlari")
if xodimlar:
    jadval(xodimlar)
    st.caption("Chiqish tugmasini bossangiz — chiqib ketayotgan va qaytib kirgan paytdagi kadrlar ochiladi.")
    for hid in xodimlar:
        tafsilot(hid)
else:
    st.info("Bu kunda xodim yozuvi yo'q.")
st.caption(f"Xodim — stolida doimiy o'tiradigan odam: bir kunda joyida {config.XODIM_OTIRISH_S // 60} daqiqadan "
           "ko'p o'tirgan odam avtomatik xodim bo'ladi. Farrosh, qorovul, boshqa xonadan kelganlar xodim bo'lmaydi.")

# ---------------------------------------------------------------- aniqlanmaganlar
if nomalumlar:
    st.subheader(f"Boshqa kirganlar — xodim emas ({len(nomalumlar)})")
    st.caption("Kirib-chiqib yurganlar (farrosh, qorovul, boshqa xonadan kelganlar). Stolida "
               f"{config.XODIM_OTIRISH_S // 60} daqiqa o'tirsa — o'zi xodimga o'tadi. Kerak bo'lsa qo'lda belgilang: "
               "keyingi safar tizim uni yuzidan taniydi.")
    for hid in nomalumlar:
        k = hisob[hid]
        c0, c1, c2 = st.columns([1, 4, 5])
        with c0:
            portret(hid)
        with c1:
            st.markdown(f"**Hodim {hid}** · keldi {hm(k.keldi)} · "
                        f"{'ketdi ' + hm(k.ketdi) if k.ketdi else 'hali xonada'} · xonada {soat_daqiqa(k.xonada)} · "
                        f"joyida o'tirdi {soat_daqiqa(otirdi.get(hid, 0))}")
            e = hodisa(hid, "kirish", k.keldi)
            if e is not None:
                with st.popover("Kirgan paytini ko'rish"):
                    rasm(e["rasm"], hms(e["ts"]))
                    video(e["klip"], "Kirish videosi (5 s)")
        with c2:
            ism = st.text_input("Ismi", key=f"ism_{hid}", label_visibility="collapsed", placeholder="Ismi")
            a, b, c = st.columns(3)
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
                d1, d2 = st.columns([3, 1])
                nishon = d1.selectbox("Bu aslida", boshqalar, format_func=nomi, key=f"b_{hid}",
                                      label_visibility="collapsed")
                if d2.button("Shu odam", key=f"bb_{hid}", use_container_width=True):
                    db.merge(con, hid, nishon)
                    st.rerun()
        st.divider()

# ---------------------------------------------------------------- mehmonlar
if mehmonlar:
    st.subheader("Boshqa xonadan kelganlar (mehmonlar)")
    jadval(mehmonlar)
    with st.expander("Mehmonlarning kirish-chiqish kadrlari"):
        for hid in mehmonlar:
            tafsilot(hid)

# ---------------------------------------------------------------- boshqarish
with st.expander("Odamlarni boshqarish (ism, tur, birlashtirish)"):
    if not odamlar:
        st.write("Hali hech kim yo'q.")
    else:
        ids = list(odamlar)
        c1, c2 = st.columns(2)
        with c1:
            hid = st.selectbox("Odam", ids, format_func=lambda i: f"{nomi(i)} ({odamlar[i]['tur']})", key="bosh_h")
            portret(hid)
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
