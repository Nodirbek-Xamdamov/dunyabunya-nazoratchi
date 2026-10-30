from datetime import datetime

import numpy as np
import pytest

from nazorat import config, db
from nazorat.hisob import kun_hisobi, soat_daqiqa
from nazorat.xizmat import Kuzatuvchi, zona
from nazorat.yuz import YuzNatija


@pytest.fixture(autouse=True)
def _vaqtinchalik_papkalar(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CLIPS", tmp_path / "kliplar")
    monkeypatch.setattr(config, "RASMLAR", tmp_path / "rasmlar")


def t(hm):
    return datetime(2026, 10, 29, *map(int, hm.split(":"))).timestamp()


def ev(hid, tur, hm):
    return {"hodim_id": hid, "tur": tur, "ts": t(hm), "klip": None}


def test_foydalanuvchi_misoli():
    evs = [ev(1, "kirish", "09:00"), ev(1, "chiqish", "13:10"), ev(1, "kirish", "13:15"),
           ev(1, "chiqish", "16:36"), ev(1, "kirish", "17:05"), ev(1, "chiqish", "19:00")]
    k = kun_hisobi(evs, t("23:00"))[1]
    assert soat_daqiqa(k.umumiy) == "10 soat"
    assert soat_daqiqa(k.tashqarida) == "34 daqiqa"
    assert soat_daqiqa(k.xonada) == "9 soat 26 daqiqa"
    assert k.ketdi == t("19:00") and len(k.chiqishlar) == 2


def test_qisqa_chiqish_6_soniya():
    evs = [ev(2, "kirish", "09:00"), ev(2, "chiqish", "10:00"), dict(ev(2, "kirish", "10:00"), ts=t("10:00") + 6)]
    k = kun_hisobi(evs, t("11:00"))[2]
    assert [soat_daqiqa(c.davomiylik) for c in k.chiqishlar] == ["6 soniya"]


def test_hali_xonada():
    k = kun_hisobi([ev(2, "kirish", "09:30"), ev(2, "chiqish", "12:00"), ev(2, "kirish", "12:30")], t("14:00"))[2]
    assert k.ketdi is None
    assert soat_daqiqa(k.umumiy) == "4 soat 30 daqiqa"
    assert soat_daqiqa(k.xonada) == "4 soat"


# ------------------------------------------------ xizmat mantig'i soxta kadrlarda

RANG = {"qizil": (0, 0, 255), "kok": (255, 0, 0), "yashil": (0, 255, 0),
        "yuz1": (0, 0, 200), "yuz2": (200, 0, 0), "yuz3": (0, 200, 0)}


class SoxtaReID:
    """Kesmaning o'rtacha rangidan vektor: bir xil kiyim — bir xil vektor."""
    device = "cpu"

    def embed(self, crops):
        out = []
        for c in crops:
            m = c.reshape(-1, 3).mean(0)
            v = np.zeros(512, dtype=np.float32)
            v[:3] = m
            v[3] = 30.0
            out.append(v / np.linalg.norm(v))
        return np.stack(out)


class SoxtaYuz:
    """Bosh qismi tanadan boshqa rangda bo'lsa — yuz bor, vektori shu rangdan."""

    def top(self, full, box):
        x1, y1, x2, y2 = map(int, box)
        hh = (y2 - y1) // 6
        bosh = full[y1:y1 + hh, x1:x2].reshape(-1, 3).mean(0)
        tana = full[y2 - hh:y2, x1:x2].reshape(-1, 3).mean(0)
        if np.allclose(bosh, tana):
            return None
        v = np.zeros(512, dtype=np.float32)
        v[:3] = bosh
        return YuzNatija(v / np.linalg.norm(v), 0.9, 60.0, full[y1:y1 + hh, x1:x2].copy())


ESHIK = (500, 100, 580, 300)      # eshik zonasida
STOL1 = (380, 200, 520, 345)      # eshik oldidagi stol — ham zonada
STOL3 = (700, 400, 860, 700)      # xona ichida


def kadr_va(odamlar):
    f = np.full((config.FRAME_H, config.FRAME_W, 3), 128, np.uint8)
    for o in odamlar:
        box, rang = o[1], o[2]
        x1, y1, x2, y2 = map(int, box)
        f[y1:y2, x1:x2] = RANG[rang]
        if len(o) > 3 and o[3]:
            f[y1:y1 + (y2 - y1) // 6, x1:x2] = RANG[o[3]]
    return f


class Sahna:
    def __init__(self, tmp_path, yuz=None):
        self.con = db.connect(":memory:")
        self.ts = t("09:00")
        self.k = Kuzatuvchi(self.con, SoxtaReID(), yuz, start_ts=self.ts - 3600)

    def yur(self, soniya, odamlar, fps=10):
        for _ in range(int(soniya * fps)):
            self.ts += 1 / fps
            self.k.kadr(self.ts, kadr_va(odamlar), [(o[0], o[1], 0.9) for o in odamlar])

    def hodisalar(self):
        return [(e["hodim_id"], e["tur"]) for e in db.events_for_day(self.con, db.sana(self.ts))]


def test_zona():
    assert zona(ESHIK) == "eshik"
    assert zona(STOL3) is None


def test_kirish_chiqish_qaytish(tmp_path):
    s = Sahna(tmp_path)
    s.yur(2, [(1, ESHIK, "qizil")])                       # 1-hodim eshikdan kirdi
    s.yur(10, [(1, STOL3, "qizil")])                      # stoliga o'tirdi
    s.yur(2, [(1, STOL3, "qizil"), (2, ESHIK, "kok")])    # 2-hodim kirdi
    s.yur(10, [(1, STOL3, "qizil"), (2, STOL1, "kok")])
    assert s.hodisalar() == [(1, "kirish"), (2, "kirish")]

    s.yur(2, [(1, ESHIK, "qizil"), (2, STOL1, "kok")])    # 1-hodim eshikka yurdi
    s.yur(30, [(2, STOL1, "kok")])                        # 30 s — hali chiqish yozilmaydi
    assert s.hodisalar() == [(1, "kirish"), (2, "kirish")]
    s.yur(40, [(2, STOL1, "kok")])                        # 60 s dan oshdi — chiqdi
    assert s.hodisalar()[-1] == (1, "chiqish")

    s.yur(3, [(2, STOL1, "kok"), (7, ESHIK, "qizil")])    # yangi iz, lekin o'sha qizil — 1-hodim qaytdi
    assert s.hodisalar()[-1] == (1, "kirish")
    assert len(db.hodimlar(s.con)) == 2                   # yangi hodim ochilmadi


def test_qisqa_chiqish_hisobga_olinmaydi(tmp_path):
    s = Sahna(tmp_path)
    s.yur(3, [(1, STOL1, "kok")])
    s.yur(20, [])                                         # 20 s chiqib qaytdi — 1 daqiqadan qisqa
    s.yur(3, [(5, STOL1, "kok")])                         # tez qaytdi — davomi
    assert s.hodisalar() == [(1, "kirish")]
    s.yur(120, [(5, STOL1, "kok")])
    assert s.hodisalar() == [(1, "kirish")]


def test_xonada_yoqolish_chiqish_emas(tmp_path):
    s = Sahna(tmp_path)
    s.yur(3, [(1, STOL3, "yashil")])
    s.yur(120, [])                                        # xona ichida ko'rinmay qoldi (to'silib)
    s.yur(3, [(9, STOL3, "yashil")])
    assert s.hodisalar() == [(1, "kirish")]


def test_birlashtirish_va_olib_tashlash(tmp_path):
    s = Sahna(tmp_path)
    s.yur(3, [(1, STOL3, "yashil"), (2, ESHIK, "kok")])
    db.merge(s.con, 2, 1)
    db.hide(s.con, 1)
    s.k.versiya_tekshir(s.ts)
    s.yur(10, [])
    s.yur(3, [(4, STOL3, "yashil")])                      # olib tashlangan narsa qaytsa — yangi hodim ochilmaydi
    assert [r["id"] for r in db.hodimlar(s.con)] == []


# ------------------------------------------------ yuz bo'yicha tanish

def test_ertasi_kuni_kiyim_boshqa_yuzdan_taniladi(tmp_path):
    s = Sahna(tmp_path, SoxtaYuz())
    s.yur(2, [(1, ESHIK, "qizil", "yuz1")])
    s.yur(5, [(1, STOL3, "qizil", "yuz1")])
    db.set_tur(s.con, 1, "xodim", "Nodir")
    s.yur(2, [(1, ESHIK, "qizil", "yuz1")])
    s.yur(70, [])                                         # chiqib ketdi
    s.ts += 24 * 3600                                     # ertasi kun
    s.k.versiya_tekshir(s.ts)
    s.yur(3, [(50, ESHIK, "kok", "yuz1")])                # boshqa kiyim, o'sha yuz
    assert s.hodisalar() == [(1, "kirish")]
    assert [r["id"] for r in db.hodimlar(s.con)] == [1]


def test_mehmon_alohida(tmp_path):
    s = Sahna(tmp_path, SoxtaYuz())
    s.yur(3, [(1, STOL3, "qizil", "yuz1")])               # xodim
    s.yur(3, [(1, STOL3, "qizil", "yuz1"), (2, ESHIK, "yashil", "yuz2")])  # boshqa xonadan
    db.set_tur(s.con, 1, "xodim")
    db.set_tur(s.con, 2, "mehmon", "Qo'shni xona")
    s.k.versiya_tekshir(s.ts)
    s.yur(3, [(1, STOL3, "qizil", "yuz1")])
    s.yur(70, [(1, STOL3, "qizil", "yuz1")])              # mehmon chiqib ketdi
    s.yur(3, [(1, STOL3, "qizil", "yuz1"), (9, ESHIK, "kok", "yuz2")])     # yana keldi, kiyimi boshqa
    turlar = {r["id"]: r["tur"] for r in db.hodimlar(s.con)}
    assert turlar == {1: "xodim", 2: "mehmon"}
    assert s.hodisalar()[-2:] == [(2, "chiqish"), (2, "kirish")]


def test_kech_aniqlash_yuz_keyin_korindi(tmp_path):
    s = Sahna(tmp_path, SoxtaYuz())
    s.yur(3, [(1, STOL3, "qizil", "yuz1")])
    db.set_tur(s.con, 1, "xodim")
    s.k.versiya_tekshir(s.ts)
    s.yur(2, [(1, ESHIK, "qizil", "yuz1")])
    s.yur(70, [])                                         # chiqib ketdi
    s.yur(1.5, [(7, ESHIK, "kok")])                       # kiyim boshqa, yuz ko'rinmayapti -> noma'lum
    assert len(db.hodimlar(s.con)) == 2
    s.yur(2, [(7, STOL3, "kok", "yuz1")])                 # yuz ko'rindi — bu 1-xodim
    assert [r["id"] for r in db.hodimlar(s.con)] == [1]
    assert s.hodisalar() == [(1, "kirish"), (1, "chiqish"), (1, "kirish")]


def test_id_almashuvi_yangi_odam_ochmaydi(tmp_path):
    s = Sahna(tmp_path)
    s.yur(3, [(1, STOL3, "yashil")])
    s.yur(3, [(5, STOL3, "yashil")])                      # tracker yangi raqam berdi, eskisi hali "tirik"
    s.yur(5, [(5, STOL3, "yashil")])
    assert s.hodisalar() == [(1, "kirish")]
    assert len(db.hodimlar(s.con)) == 1


def test_bitta_odamga_ikki_quti(tmp_path):
    s = Sahna(tmp_path)
    s.yur(3, [(1, STOL3, "yashil")])
    s.yur(5, [(1, STOL3, "yashil"), (2, (705, 410, 860, 700), "yashil")])  # ikkinchi quti ustma-ust
    assert len(db.hodimlar(s.con)) == 1


# ------------------------------------------------ xodim = stolida doimiy o'tiradigan odam

@pytest.fixture
def qisqa_xodim_chegarasi(monkeypatch):
    monkeypatch.setattr(config, "XODIM_OTIRISH_S", 10 * 60)


def turi(s, hid):
    return {r["id"]: r["tur"] for r in db.hodimlar(s.con)}[hid]


def test_stolda_otirgan_xodim_boladi(tmp_path, qisqa_xodim_chegarasi):
    s = Sahna(tmp_path)
    s.yur(2, [(1, ESHIK, "qizil")])
    s.yur(8 * 60, [(1, STOL3, "qizil")], fps=1)          # 8 daqiqa — hali yetmaydi
    assert turi(s, 1) == "nomalum"
    s.yur(5 * 60, [(1, STOL3, "qizil")], fps=1)          # 2 daq kutish + 11 daq o'tirdi
    assert turi(s, 1) == "xodim"
    assert db.otirish(s.con, 1, db.sana(s.ts)) >= 10 * 60


def test_yurib_yurgan_farrosh_xodim_bolmaydi(tmp_path, qisqa_xodim_chegarasi):
    s = Sahna(tmp_path)
    joylar = [STOL3, (300, 380, 460, 700), (900, 380, 1060, 700), (600, 300, 740, 560)]
    for i in range(25 * 60 // 20):                        # 25 daqiqa xona bo'ylab yuradi
        s.yur(20, [(1, joylar[i % 4], "yashil")], fps=1)
    assert turi(s, 1) == "nomalum"


def test_qolda_mehmon_belgilangan_xodim_bolmaydi(tmp_path, qisqa_xodim_chegarasi):
    s = Sahna(tmp_path)
    s.yur(3, [(1, STOL3, "kok")])
    db.set_tur(s.con, 1, "mehmon")
    s.k.versiya_tekshir(s.ts)
    s.yur(20 * 60, [(1, STOL3, "kok")], fps=1)
    assert turi(s, 1) == "mehmon"


def test_eski_kliplar_ochiriladi(tmp_path):
    from nazorat.xizmat import eski_kliplarni_ochir
    for kun in ("2026-09-20", "2026-10-01", "2026-10-29"):
        (config.CLIPS / kun).mkdir(parents=True)
    assert eski_kliplarni_ochir(datetime(2026, 10, 30)) == ["2026-09-20"]
    assert sorted(p.name for p in config.CLIPS.iterdir()) == ["2026-10-01", "2026-10-29"]
