"""fps_sinov.py natijalarini jonli xizmat yozgan hodisalar (22 kadr/s) bilan solishtiradi.

    python tools/fps_solishtir.py --out papka --start "2026-10-30 17:06:27" --soat 3

Moslik: bir xil tur (kirish/chiqish), vaqt farqi <= 20 s. Yangi (noma'lum) odamlarning raqamlari ikki
tomonda har xil bo'ladi — birinchi mos kelgan hodisa bo'yicha bir-biriga bog'lanadi.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nazorat import config  # noqa: E402

FARQ_S = 20


def solishtir(ref, test, maxid):
    """ref, test: [(hodim, tur, ts)]. Qaytaradi: to'g'ri, boshqa_odam, tushib_qolgan, ortiqcha (ro'yxatlar)."""
    xarita: dict[int, int] = {}
    ishlatilgan = set()
    togri, boshqa, tushdi = [], [], []
    for r in ref:
        nomzod = [(abs(t[2] - r[2]), j, t) for j, t in enumerate(test)
                  if j not in ishlatilgan and t[1] == r[1] and abs(t[2] - r[2]) <= FARQ_S]
        if not nomzod:
            tushdi.append(r)
            continue

        def mos(t):
            if r[0] <= maxid or t[0] <= maxid:
                return t[0] == r[0]
            return xarita.get(t[0], r[0]) == r[0]

        nomzod.sort(key=lambda x: (not mos(x[2]), x[0]))
        _, j, t = nomzod[0]
        ishlatilgan.add(j)
        if mos(t):
            if r[0] > maxid:
                xarita[t[0]] = r[0]
            togri.append((r, t))
        else:
            boshqa.append((r, t))
    ortiqcha = [t for j, t in enumerate(test) if j not in ishlatilgan]
    return togri, boshqa, tushdi, ortiqcha


def hm(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--soat", type=float, required=True)
    ap.add_argument("--snapshot", required=True)
    a = ap.parse_args()
    start = datetime.strptime(a.start, "%Y-%m-%d %H:%M:%S").timestamp()
    end = start + a.soat * 3600
    maxid = sqlite3.connect(a.snapshot).execute(
        "SELECT COALESCE(MAX(id), 0) FROM hodimlar WHERE yaratilgan < ?", (start,)).fetchone()[0]

    con = sqlite3.connect(config.DB_PATH)
    ref = [(h, t, ts) for h, t, ts in con.execute(
        "SELECT hodim_id, tur, ts FROM hodisalar WHERE ts >= ? AND ts <= ? ORDER BY ts", (start, end))]
    print(f"jonli xizmat (22 kadr/s): {len(ref)} hodisa, {hm(start)}-{hm(end)}")
    for f in sorted(Path(a.out).glob("fps_*.json"), key=lambda p: -json.loads(p.read_text("utf-8"))["fps"]):
        d = json.loads(f.read_text("utf-8"))
        test = [(e["hodim_id"], e["tur"], e["ts"]) for e in d["hodisalar"] if e["ts"] <= end]
        togri, boshqa, tushdi, ortiq = solishtir(ref, test, maxid)
        print(f"\n== {d['fps']:g} kadr/s: {len(test)} hodisa | to'g'ri {len(togri)} | boshqa odam {len(boshqa)} | "
              f"tushib qoldi {len(tushdi)} | ortiqcha {len(ortiq)} | yangi odam {len(d['yangi_odamlar'])} | "
              f"{d['kadr']} kadr {d['soniya'] / 60:.0f} daq")
        for r, t in boshqa:
            print(f"   boshqa odam: {hm(r[2])} {r[1]} jonli #{r[0]}, sinov #{t[0]}")
        for r in tushdi:
            print(f"   tushib qoldi: {hm(r[2])} {r[1]} #{r[0]}")
        for t in ortiq:
            print(f"   ortiqcha: {hm(t[2])} {t[1]} #{t[0]}")


if __name__ == "__main__":
    main()
