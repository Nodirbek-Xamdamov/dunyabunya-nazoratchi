"""Yozib olingan videoni berilgan kadr/s da xizmat mantig'idan o'tkazadi (jonli xizmatning o'sha kodi).

    python tools/fps_sinov.py --video X.mkv --db snapshot.db --start "2026-10-30 17:06:27" --fps 5 --out papka

Bazaning nusxasidan boshlaydi (yuzlar, odamlar), start dan keyingi yozuvlarni o'chiradi va video oxirigacha
yurgan kirish/chiqishlarni <out>/fps_<F>.json ga yozadi. Solishtirish: tools/fps_solishtir.py.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("YOLO_OFFLINE", "1")

import cv2  # noqa: E402

from nazorat import config, db  # noqa: E402
from nazorat.xizmat import Kuzatuvchi  # noqa: E402


def tayyorla_db(snapshot: Path, path: Path, start: float):
    shutil.copy(snapshot, path)
    con = sqlite3.connect(path)
    yangi = [r[0] for r in con.execute("SELECT id FROM hodimlar WHERE yaratilgan >= ?", (start,))]
    for jadval in ("hodisalar", "korinish", "yuzlar"):
        con.execute(f"DELETE FROM {jadval} WHERE ts >= ?", (start,))
    for i in yangi:
        for jadval in ("hodisalar", "korinish", "yuzlar"):
            con.execute(f"DELETE FROM {jadval} WHERE hodim_id = ?", (i,))
        con.execute("DELETE FROM hodimlar WHERE id = ?", (i,))
    con.commit()
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", required=True, help="videoning birinchi kadri vaqti, YYYY-MM-DD HH:MM:SS")
    ap.add_argument("--fps", type=float, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    F = a.fps
    out = Path(a.out)
    ish = out / f"fps_{F:g}"
    ish.mkdir(parents=True, exist_ok=True)
    start = datetime.strptime(a.start, "%Y-%m-%d %H:%M:%S").timestamp()
    dbp = ish / "nazorat.db"
    tayyorla_db(Path(a.db), dbp, start)

    config.CLIPS, config.RASMLAR = ish / "kliplar", ish / "rasmlar"
    config.CONFIRM_N = max(2, round(8 * F / 22))
    trk = ish / "bytetrack.yaml"
    trk.write_text("tracker_type: bytetrack\ntrack_high_thresh: 0.35\ntrack_low_thresh: 0.1\n"
                   f"new_track_thresh: 0.45\ntrack_buffer: {max(2, round(2 * F))}\nmatch_thresh: 0.8\n"
                   "fuse_score: True\n", encoding="utf-8")

    from ultralytics import YOLO
    from nazorat.reid import ReID
    from nazorat.yuz import Yuz
    model = YOLO(str(config.YOLO_WEIGHTS))
    reid = ReID(str(config.REID_WEIGHTS))
    yuz = Yuz(str(config.YUZ_DET), str(config.YUZ_REC))
    con = db.connect(dbp)
    k = Kuzatuvchi(con, reid, yuz, start_ts=start)

    cap = cv2.VideoCapture(a.video)
    keyingi, n, t0, oxirgi_hisobot = start, 0, time.time(), start
    ts = start
    while cap.grab():
        ts = start + cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if ts + 1e-6 < keyingi:
            continue
        keyingi = max(keyingi + 1 / F, ts + 0.5 / F)
        ok, frame = cap.retrieve()
        if not ok:
            continue
        small = cv2.resize(frame, (config.FRAME_W, config.FRAME_H))
        r = model.track(small, persist=True, classes=[0], tracker=str(trk), conf=config.DET_CONF,
                        device=0, verbose=False)[0]
        dets = []
        if r.boxes.id is not None:
            for b, t, c in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.id.int().cpu().numpy(),
                               r.boxes.conf.cpu().numpy()):
                box = tuple(float(x) for x in b)
                if box[3] - box[1] >= config.MIN_BOX_H and box[3] >= config.MIN_FOOT_Y:
                    dets.append((int(t), box, float(c)))
        k.kadr(ts, frame, dets)
        n += 1
        if ts - oxirgi_hisobot >= 600:
            oxirgi_hisobot = ts
            print(f"fps {F:g}: video {datetime.fromtimestamp(ts):%H:%M}, {n} kadr, {time.time() - t0:.0f} s",
                  flush=True)
    # kutilayotgan chiqishlar yozilsin
    for _ in range(int(90 * F)):
        ts += 1 / F
        k.kadr(ts, None, [])

    ev = [dict(r) for r in con.execute("SELECT hodim_id, tur, ts, izoh FROM hodisalar WHERE ts >= ? ORDER BY ts",
                                       (start,))]
    odam = {r["id"]: r["tur"] for r in db.hodimlar(con, faqat_faol=False)}
    yangi = sorted(i for i in odam if con.execute("SELECT yaratilgan FROM hodimlar WHERE id=?", (i,)).fetchone()[0]
                   >= start)
    (out / f"fps_{F:g}.json").write_text(json.dumps(
        {"fps": F, "kadr": n, "soniya": time.time() - t0, "hodisalar": ev, "yangi_odamlar": yangi,
         "turlar": {str(i): odam[i] for i in odam}}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"fps {F:g}: tugadi, {len(ev)} hodisa, {len(yangi)} yangi odam, {n} kadr", flush=True)


if __name__ == "__main__":
    main()
