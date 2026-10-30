"""Kuzatuv xizmati: kamera -> YOLO + ByteTrack (GPU) -> odamni tanish -> kirish/chiqish hodisalari.

Odamni tanish (aniqligi bo'yicha tartibda):
1. Yuz (YuNet + ArcFace, CPU): kunlar orasida ham ishlaydi. Xonaning xodimlari, ma'lum mehmonlar
   (boshqa xona xodimlari) va noma'lumlar yuz to'plamlari bilan solishtiriladi.
2. Kiyim/gavda (OSNet, GPU): faqat shu kun ichida — yuz ko'rinmaganda izlarni bog'lash uchun.
3. Hech kimga o'xshamasa — yangi "noma'lum" odam. Keyin yuzi ko'rinib, ma'lum odam bo'lib chiqsa,
   yozuvlari o'sha odamga o'tkaziladi (kech aniqlash).

Xodim kim: stolida qimirlamay o'tirgan vaqti bir kunda XODIM_OTIRISH_S dan oshgan noma'lum odam
avtomatik "xodim" bo'ladi. Farrosh, qorovul, boshqa xonadan kelganlar yuradi yoki qisqa turadi.

Chiqish: iz eshik zonasida yo'qolsa va MIN_ABSENCE_S ichida qaytmasa. Har kirish va chiqish uchun
5 soniyalik klip va bitta kadr (rasm) saqlanadi; odam ramkada o'z raqami bilan belgilanadi.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import cv2
import numpy as np

from . import config, db

log = logging.getLogger("nazorat")


# ---------------------------------------------------------------- geometriya va o'xshashlik

def zona(box) -> str | None:
    """Quti eshik zonasidami? Oyoq nuqtasi yoki markaz zona ichida bo'lsa — zona nomi."""
    x1, y1, x2, y2 = box
    nuqtalar = [((x1 + x2) / 2, y2), ((x1 + x2) / 2, (y1 + y2) / 2)]
    for nom, poly in config.ESHIK_ZONALARI.items():
        cnt = np.array(poly, dtype=np.float32)
        for p in nuqtalar:
            if cv2.pointPolygonTest(cnt, (float(p[0]), float(p[1])), False) >= 0:
                return nom
    return None


def _overlap(a, b) -> float:
    """a qutining qancha qismi b bilan ustma-ust (0..1)."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1.0, (a[2] - a[0]) * (a[3] - a[1]))
    return ix * iy / area


def sim(gallery: np.ndarray, v: np.ndarray) -> float:
    if len(gallery) == 0:
        return 0.0
    s = gallery @ v
    k = min(3, len(s))
    return float(np.sort(s)[-k:].mean())


def _norm(vs, w=None) -> np.ndarray:
    v = np.average(np.stack(vs), axis=0, weights=w)
    return v / (np.linalg.norm(v) + 1e-9)


def _imwrite(path: Path, img) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, b = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if ok:
        path.write_bytes(b.tobytes())


# ---------------------------------------------------------------- holatlar

@dataclass
class Iz:
    tid: int
    born: float
    born_box: tuple
    last: float
    last_box: tuple
    n: int = 0
    conf_sum: float = 0.0
    embs: list = field(default_factory=list)       # kiyim vektorlari (aniqlashgacha)
    new_vecs: list = field(default_factory=list)   # kiyim vektorlari (aniqlangandan keyin)
    last_crop: float = 0.0
    yuzlar: list = field(default_factory=list)     # YuzNatija
    yuz_qoshilgan: int = 0
    last_yuz: float = 0.0
    yuz_fail: int = 0
    hodim: int | None = None
    usul: str = ""            # yuz / kiyim / yangi
    kiyim_sim: float = 0.0
    kirish_id: int | None = None
    yangi_odam: bool = False

    @property
    def conf(self) -> float:
        return self.conf_sum / max(1, self.n)


@dataclass
class Hodim:
    id: int
    tur: str
    nom: str | None
    gallery: np.ndarray        # kiyim (faqat bugungi)
    yuzlar: np.ndarray         # yuz (barcha kunlar)
    ichkarida: bool = False
    iz: int | None = None
    last_seen: float | None = None
    kutilgan_chiqish: dict | None = None  # {"ts", "klip", "rasm", "zona"}
    rasm_sifat: float = -1.0
    qolda: bool = False        # turini odam (panelda) belgilagan
    otirgan: float = 0.0       # bugun joyida o'tirgan vaqt, s
    yozilmagan: float = 0.0    # bazaga hali yozilmagan qismi
    joy: tuple | None = None   # (cx, cy, boshlangan, oxirgi_ts, kutilayotgan_s)

    @property
    def yorliq(self) -> str:
        return f"#{self.id} {self.nom}" if self.nom else f"Hodim {self.id}"


@dataclass
class KlipIsh:
    start: float
    end: float
    tid: int
    hodim: int
    path: Path
    rasm: Path
    rasm_ts: float
    rang: tuple


# ---------------------------------------------------------------- klip yozuvchi

def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg.exe"


class KlipYozuvchi(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.q: queue.Queue = queue.Queue()

    def run(self):
        while True:
            path, frames, fps = self.q.get()
            try:
                self._write(path, frames, fps)
            except Exception:
                log.exception("klip yozilmadi: %s", path)

    def _write(self, path: Path, frames, fps: float):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.mp4")
        h, w = frames[0].shape[:2]
        cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps:.2f}", "-i", "-",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(tmp)]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for f in frames:
            p.stdin.write(f.tobytes())
        p.stdin.close()
        p.wait(timeout=60)
        if p.returncode == 0:
            os.replace(tmp, path)
            log.info("klip: %s (%d kadr)", path.name, len(frames))
        else:
            log.error("ffmpeg xato %s: %s", p.returncode, path)


# ---------------------------------------------------------------- kamera o'quvchi

class Oquvchi(threading.Thread):
    """Kameradan kadrlarni uzluksiz o'qiydi; tahlil sekinlashsa, eskilari tashlanadi."""

    def __init__(self, url: str):
        super().__init__(daemon=True)
        self.url = url
        self.q: queue.Queue = queue.Queue(maxsize=4)
        self.ulandi = False

    def run(self):
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        while True:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                log.warning("kameraga ulanib bo'lmadi: %s", config.mask(self.url))
                self.ulandi = False
                time.sleep(5)
                continue
            log.info("kameraga ulandi: %s", config.mask(self.url))
            self.ulandi = True
            fails = 0
            while fails < 50:
                ok, f = cap.read()
                if not ok:
                    fails += 1
                    time.sleep(0.05)
                    continue
                fails = 0
                ts = time.time()
                if self.q.full():
                    try:
                        self.q.get_nowait()
                    except queue.Empty:
                        pass
                self.q.put((ts, f))
            log.warning("kamera oqimi uzildi, qayta ulanadi")
            self.ulandi = False
            cap.release()
            time.sleep(2)


# ---------------------------------------------------------------- asosiy mantiq

class Kuzatuvchi:
    """Kadrlar ketma-ketligidan hodisalarni chiqaradi. Kamera va modelsiz ham sinash mumkin."""

    def __init__(self, con, reid, yuz=None, start_ts: float | None = None):
        self.con = con
        self.reid = reid
        self.yuz = yuz
        self.izlar: dict[int, Iz] = {}
        self.hodimlar: dict[int, Hodim] = {}
        self.yashirin: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self.buf: deque = deque()
        self.ishlar: list[KlipIsh] = []
        self.yozuvchi: KlipYozuvchi | None = None
        self.start_ts = start_ts if start_ts is not None else time.time()
        self.versiya = db.get_holat(con, "versiya")[0]
        self.kun = db.sana(self.start_ts)
        self.yukla(self.start_ts)

    # --- bazadan holatni yuklash
    def yukla(self, ts: float):
        eski = self.hodimlar
        kun_oldin = getattr(self, "kun", None)
        self.hodimlar = {}
        bugun, boshi = db.sana(ts), db.kun_boshi(ts)
        self.kun = bugun
        for r in db.hodimlar(self.con):
            hid = r["id"]
            oxirgi = self.con.execute(
                "SELECT tur, ts FROM hodisalar WHERE hodim_id = ? AND sana = ? ORDER BY ts DESC, id DESC LIMIT 1",
                (hid, bugun)).fetchone()
            h = Hodim(hid, r["tur"], r["nom"], db.load_vectors(self.con, hid, boshi), db.load_faces(self.con, hid))
            h.ichkarida = bool(oxirgi and oxirgi["tur"] == "kirish")
            h.qolda = bool(r["qolda"])
            h.otirgan = db.otirish(self.con, hid, bugun)
            if hid in eski:
                e = eski[hid]
                h.iz, h.last_seen, h.kutilgan_chiqish, h.rasm_sifat = e.iz, e.last_seen, e.kutilgan_chiqish, e.rasm_sifat
                if e.yozilmagan and kun_oldin == bugun:
                    db.add_otirish(self.con, hid, bugun, e.yozilmagan)
                    h.otirgan += e.yozilmagan
                h.joy = e.joy if kun_oldin == bugun else None
            else:
                h.last_seen = oxirgi["ts"] if oxirgi else None
                if h.ichkarida:
                    h.last_seen = ts
            self.hodimlar[hid] = h
        self.yashirin = {i: (db.load_vectors(self.con, i), db.load_faces(self.con, i))
                         for i in db.yashirinlar(self.con)}
        # o'chirilgan/birlashtirilgan odamlarning izlari qaytadan aniqlanadi
        for iz in self.izlar.values():
            if iz.hodim is not None and iz.hodim >= 0 and iz.hodim not in self.hodimlar:
                iz.hodim = None
        log.info("odamlar yuklandi: %s", {h.id: f"{h.tur}/{'ichkarida' if h.ichkarida else 'tashqarida'}"
                                         f"/yuz {len(h.yuzlar)}/o'tirdi {h.otirgan / 60:.0f} daq"
                                         for h in self.hodimlar.values()})

    # --- bitta kadr
    def kadr(self, ts: float, frame: np.ndarray | None, dets: list[tuple[int, tuple, float]]):
        """frame: to'liq o'lchamli kadr; dets: (iz_id, (x1,y1,x2,y2), ishonch) FRAME_W x FRAME_H da."""
        if frame is not None:
            small = cv2.resize(frame, (config.CLIP_W, config.CLIP_H))
            self.buf.append((ts, small, [(t, b) for t, b, _ in dets]))
            while self.buf and ts - self.buf[0][0] > config.BUFFER_S:
                self.buf.popleft()

        kesmalar = []
        for tid, box, conf in dets:
            iz = self.izlar.get(tid)
            if iz is None:
                iz = self.izlar[tid] = Iz(tid, ts, box, ts, box)
            iz.last, iz.last_box = ts, box
            iz.n += 1
            iz.conf_sum += conf
            kerak = ((iz.hodim is None and len(iz.embs) < 8 and ts - iz.last_crop >= 0.12)
                     or (iz.hodim is not None and ts - iz.last_crop >= 3.0))
            if kerak and frame is not None:
                majburiy = iz.hodim is None and ts - iz.born >= 4.0 and not iz.embs
                if majburiy or self._yaxshi(box, [b for t, b, _ in dets if t != tid]):
                    kesmalar.append((iz, self._kesma(frame, box)))
        if kesmalar:
            vecs = self.reid.embed([c for _, c in kesmalar])
            for (iz, _), v in zip(kesmalar, vecs):
                (iz.embs if iz.hodim is None else iz.new_vecs).append(v)
                iz.last_crop = ts

        if frame is not None and self.yuz is not None:
            self._yuz_qidir(ts, frame, dets)

        korinayotgan = {tid: box for tid, box, _ in dets}
        for iz in list(self.izlar.values()):
            if iz.hodim is None and iz.last == ts and self._tayyor(iz, ts):
                ustma = any(_overlap(iz.last_box, b) > 0.6 for t, b in korinayotgan.items()
                            if t != iz.tid and (self.izlar[t].hodim or -1) >= 0)
                if ustma and ts - iz.born < 10:
                    continue  # bitta odamning ikkinchi qutisi bo'lishi mumkin — kutamiz
                self._aniqla(iz, ts, frame)
            elif iz.hodim is not None and iz.hodim >= 0 and len(iz.new_vecs) >= 3:
                h = self.hodimlar.get(iz.hodim)
                if h is not None:
                    vv = np.stack(iz.new_vecs)
                    h.gallery = np.concatenate([h.gallery, vv])[-config.GALLERY_MAX:]
                    db.add_vectors(self.con, h.id, ts, vv)
                iz.new_vecs = []
            if ts - iz.last > config.LOST_S:
                self._yoqoldi(iz)
                del self.izlar[iz.tid]

        for tid, box, _ in dets:
            iz = self.izlar.get(tid)
            if iz is not None and iz.hodim is not None and iz.hodim >= 0 and iz.hodim in self.hodimlar:
                self._otirish(self.hodimlar[iz.hodim], box, ts)

        for h in self.hodimlar.values():
            k = h.kutilgan_chiqish
            if k and ts - k["ts"] >= config.MIN_ABSENCE_S:
                db.add_event(self.con, h.id, "chiqish", k["ts"], k["klip"], k["zona"], k["rasm"])
                log.info("CHIQDI %s (%s) %s", h.yorliq, k["zona"], _hm(k["ts"]))
                h.ichkarida, h.kutilgan_chiqish = False, None
            elif (h.ichkarida and h.iz is None and not k and h.last_seen
                  and ts - h.last_seen >= config.LOST_TIMEOUT_S):
                db.add_event(self.con, h.id, "chiqish", h.last_seen, None, "ko'rinmay qoldi")
                log.info("CHIQDI %s (ko'rinmay qoldi) %s", h.yorliq, _hm(h.last_seen))
                h.ichkarida = False

        for ish in [i for i in self.ishlar if ts >= i.end]:
            self.ishlar.remove(ish)
            self._klip(ish)

    # --- xodimmi: joyida o'tirish
    def _otirish(self, h: Hodim, box, ts: float):
        x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        r = max(config.OTIRISH_R, 0.3 * (y2 - y1))
        j = h.joy
        if j is None or np.hypot(cx - j[0], cy - j[1]) > r:
            h.joy = (cx, cy, ts, ts, 0.0)   # yangi joy — qaytadan kutamiz
            return
        jx, jy, bosh, oxirgi, kutish = j
        if ts - oxirgi > 60:                # uzoq ko'rinmadi — boshqa safar
            h.joy = (cx, cy, ts, ts, 0.0)
            return
        dt = min(ts - oxirgi, 2.0)
        if ts - bosh < config.OTIRISH_OYNA_S:
            h.joy = (jx, jy, bosh, ts, kutish + dt)
            return
        h.joy = (jx, jy, bosh, ts, 0.0)
        qosh = kutish + dt
        h.otirgan += qosh
        h.yozilmagan += qosh
        if h.yozilmagan >= 30:
            db.add_otirish(self.con, h.id, db.sana(ts), h.yozilmagan)
            h.yozilmagan = 0.0
        if h.tur == "nomalum" and not h.qolda and h.otirgan >= config.XODIM_OTIRISH_S:
            if h.yozilmagan:
                db.add_otirish(self.con, h.id, db.sana(ts), h.yozilmagan)
                h.yozilmagan = 0.0
            db.avto_xodim(self.con, h.id)
            h.tur = "xodim"
            log.info("XODIM: %s bugun joyida %.0f daqiqa o'tirdi — xonaning xodimi deb belgilandi",
                     h.yorliq, h.otirgan / 60)

    # --- yuz
    def _yuz_qidir(self, ts, frame, dets):
        fh, fw = frame.shape[:2]
        sx, sy = fw / config.FRAME_W, fh / config.FRAME_H
        navbat = []
        for tid, box, _ in dets:
            iz = self.izlar[tid]
            if (iz.hodim is not None and iz.hodim < 0) or len(iz.yuzlar) >= config.YUZ_IZ_MAX:
                continue
            oraliq = config.YUZ_ORALIQ if iz.yuz_fail < 15 else 2.0
            if ts - iz.last_yuz >= oraliq:
                navbat.append(iz)
        navbat.sort(key=lambda i: i.last_yuz)
        for iz in navbat[:2]:  # bir kadrda ko'pi bilan 2 ta — tezlik uchun
            iz.last_yuz = ts
            x1, y1, x2, y2 = iz.last_box
            r = self.yuz.top(frame, (x1 * sx, y1 * sy, x2 * sx, y2 * sy))
            if r is None:
                iz.yuz_fail += 1
                continue
            iz.yuz_fail = 0
            iz.yuzlar.append(r)
            if iz.hodim is not None and iz.hodim >= 0:
                self._yuz_tekshir(iz, ts)

    def _yuz_vec(self, iz: Iz) -> np.ndarray:
        return _norm([r.vec for r in iz.yuzlar], [max(0.05, r.sifat) for r in iz.yuzlar])

    def _bosh(self, h: Hodim, ts: float) -> bool:
        """Odam yangi izga bog'lanishi mumkinmi: izi yo'q yoki eski izi shu kadrda ko'rinmayapti
        (tracker odamga yangi raqam berib yuborgan — ID almashuvi)."""
        if h.iz is None:
            return True
        eski = self.izlar.get(h.iz)
        return eski is None or eski.last < ts

    def _yuz_nomzodlar(self, v, istisno=None, ts=None):
        out = []
        for h in self.hodimlar.values():
            if h.id == istisno or len(h.yuzlar) == 0 or not self._bosh(h, ts if ts is not None else float("inf")):
                continue
            out.append((sim(h.yuzlar, v), h))
        out.sort(key=lambda x: -x[0])
        return out

    def _yuz_qosh(self, iz: Iz, h: Hodim, ts: float):
        yangi = iz.yuzlar[iz.yuz_qoshilgan:]
        if not yangi:
            return
        if iz.usul == "kiyim" and iz.kiyim_sim < 0.6 and len(h.yuzlar) == 0:
            return  # kiyim bo'yicha ishonch past — birovning yuzini yozib qo'ymaylik
        v = _norm([r.vec for r in yangi])
        if len(h.yuzlar) and sim(h.yuzlar, v) < config.YUZ_QOSHISH:
            return
        iz.yuz_qoshilgan = len(iz.yuzlar)
        vv = np.stack([r.vec for r in yangi])
        h.yuzlar = np.concatenate([h.yuzlar, vv])[-config.YUZ_MAX:]
        db.add_faces(self.con, h.id, ts, vv, [r.sifat for r in yangi])
        eng = max(yangi, key=lambda r: r.sifat)
        if eng.sifat > h.rasm_sifat + 0.05:
            self._rasm(h, eng.rasm, eng.sifat)

    def _yuz_tekshir(self, iz: Iz, ts: float):
        """Aniqlangan izda yangi yuz chiqdi: boshqa ma'lum odam bo'lib chiqsa — qayta bog'laymiz."""
        h = self.hodimlar.get(iz.hodim)
        if h is None or len(iz.yuzlar) < 2:
            return
        v = self._yuz_vec(iz)
        oz = sim(h.yuzlar, v) if len(h.yuzlar) else None
        nom = self._yuz_nomzodlar(v, istisno=h.id, ts=ts)
        if nom:
            s, p = nom[0]
            ikkinchi = max(nom[1][0] if len(nom) > 1 else 0.0, oz or 0.0)
            if s >= config.YUZ_T and s - ikkinchi >= config.YUZ_FARQ and (oz is None or oz < config.YUZ_QOSHISH):
                self._qayta_bogla(iz, h, p, s, ts)
                return
        self._yuz_qosh(iz, h, ts)

    # --- yordamchilar
    @staticmethod
    def _yaxshi(box, boshqalar) -> bool:
        x1, y1, x2, y2 = box
        if y2 - y1 < 140 or x1 < 2 or y1 < 2 or x2 > config.FRAME_W - 2:
            return False
        return all(_overlap(box, b) < 0.3 for b in boshqalar)

    @staticmethod
    def _kesma(frame, box):
        fh, fw = frame.shape[:2]
        sx, sy = fw / config.FRAME_W, fh / config.FRAME_H
        x1, y1, x2, y2 = box
        return frame[max(0, int(y1 * sy)):int(y2 * sy), max(0, int(x1 * sx)):int(x2 * sx)]

    @staticmethod
    def _tayyor(iz: Iz, ts: float) -> bool:
        yosh = ts - iz.born
        if iz.conf < config.CONFIRM_MIN_CONF:
            return False
        if yosh >= config.CONFIRM_S and iz.n >= config.CONFIRM_N and len(iz.embs) >= 3:
            return True
        return yosh >= 4.0 and len(iz.embs) >= 1

    def _rasm(self, h: Hodim, img, sifat: float):
        rel = f"hodim_{h.id}.jpg"
        _imwrite(config.RASMLAR / rel, img)
        db.set_rasm(self.con, h.id, rel)
        h.rasm_sifat = sifat

    # --- aniqlash
    def _aniqla(self, iz: Iz, ts: float, frame):
        v = _norm(iz.embs)
        # olib tashlangan (xato narsa) — e'tiborsiz
        for i, (g, fz) in self.yashirin.items():
            if sim(g, v) >= config.SIM_QAYTDI or (iz.yuzlar and sim(fz, self._yuz_vec(iz)) >= config.YUZ_T):
                iz.hodim = -1
                log.info("iz %d olib tashlangan #%d ga o'xshaydi — e'tiborsiz", iz.tid, i)
                return

        # 1) yuz
        if iz.yuzlar:
            nom = self._yuz_nomzodlar(self._yuz_vec(iz), ts=ts)
            log.info("iz %d yuz: %s", iz.tid, ", ".join(f"#{h.id}={s:.2f}" for s, h in nom[:3]) or "to'plam yo'q")
            if nom:
                s, h = nom[0]
                ikkinchi = nom[1][0] if len(nom) > 1 else 0.0
                if s >= config.YUZ_T and s - ikkinchi >= config.YUZ_FARQ:
                    self._bogla(iz, h, "yuz", s, ts)
                    return

        # 2) kiyim (faqat bugungi)
        nomzodlar = []
        for h in self.hodimlar.values():
            if not self._bosh(h, ts) or len(h.gallery) == 0:
                continue
            chegara = config.SIM_DAVOM if h.ichkarida else config.SIM_QAYTDI
            nomzodlar.append((sim(h.gallery, v), h, chegara))
        nomzodlar.sort(key=lambda x: -x[0])
        log.info("iz %d kiyim: %s", iz.tid,
                 ", ".join(f"#{h.id}={s:.2f}{'*' if s >= c else ''}" for s, h, c in nomzodlar[:4]) or "nomzod yo'q")
        mos = next(((s, h) for s, h, c in nomzodlar if s >= c), None)
        if mos:
            self._bogla(iz, mos[1], "kiyim", mos[0], ts)
            return

        # 3) yangi noma'lum odam
        hid = db.new_hodim(self.con, ts)
        h = self.hodimlar[hid] = Hodim(hid, "nomalum", None, np.zeros((0, 512), np.float32),
                                       np.zeros((0, 512), np.float32))
        iz.yangi_odam = True
        if frame is not None:
            self._rasm(h, self._kesma(frame, iz.last_box), 0.0)
        self._bogla(iz, h, "yangi", 0.0, ts)

    def _uz(self, h: Hodim):
        """Odamning eski izi (ID almashuvidan qolgan) endi unga tegishli emas."""
        eski = self.izlar.get(h.iz) if h.iz is not None else None
        if eski is not None:
            eski.hodim = -2
            log.info("iz %d -> yangi izga o'tdi (%s)", eski.tid, h.yorliq)

    def _bogla(self, iz: Iz, h: Hodim, usul: str, s: float, ts: float):
        self._uz(h)
        iz.hodim, iz.usul, h.iz = h.id, usul, iz.tid
        if usul == "kiyim":
            iz.kiyim_sim = s
        if h.ichkarida:
            k = h.kutilgan_chiqish
            if k:
                self._bekor(k)
                log.info("%s qisqa chiqib qaytdi (%.0f s) — hisobga olinmadi", h.yorliq, ts - k["ts"])
                h.kutilgan_chiqish = None
            else:
                log.info("iz %d = %s (davomi, %s %.2f)", iz.tid, h.yorliq, usul, s)
        else:
            self._kirish(h, iz, f"{usul} {s:.2f}" if usul != "yangi" else "yangi odam")
        vv = np.stack(iz.embs)
        h.gallery = np.concatenate([h.gallery, vv])[-config.GALLERY_MAX:]
        db.add_vectors(self.con, h.id, ts, vv)
        h.last_seen = ts
        self._yuz_qosh(iz, h, ts)

    def _qayta_bogla(self, iz: Iz, a: Hodim, p: Hodim, s: float, ts: float):
        """Iz aslida p ekan (yuzidan bilindi): a ga yozilganlar p ga o'tadi."""
        log.info("KECH ANIQLASH: iz %d %s emas, %s ekan (yuz %.2f)", iz.tid, a.yorliq, p.yorliq, s)
        if p.kutilgan_chiqish:
            self._bekor(p.kutilgan_chiqish)
            p.kutilgan_chiqish = None
        if iz.yangi_odam:
            db.merge(self.con, a.id, p.id, bump=False)
            p.gallery = np.concatenate([p.gallery, a.gallery])[-config.GALLERY_MAX:]
            p.otirgan += a.otirgan
            p.yozilmagan += a.yozilmagan
            del self.hodimlar[a.id]
            if p.ichkarida and iz.kirish_id:   # p xonada edi — bu kirish ortiqcha
                self.con.execute("DELETE FROM hodisalar WHERE id = ?", (iz.kirish_id,))
                self.con.commit()
                iz.kirish_id = None
        else:
            a.iz = None
            if iz.kirish_id:
                if p.ichkarida:
                    self.con.execute("DELETE FROM hodisalar WHERE id = ?", (iz.kirish_id,))
                    iz.kirish_id = None
                else:
                    self.con.execute("UPDATE hodisalar SET hodim_id = ? WHERE id = ?", (p.id, iz.kirish_id))
                self.con.commit()
                a.ichkarida = False
            elif not p.ichkarida:
                iz.kirish_id = db.add_event(self.con, p.id, "kirish", iz.born, None, f"yuz {s:.2f} (kech aniqlandi)")
        p.ichkarida = True
        self._uz(p)
        iz.hodim, iz.usul, p.iz = p.id, "yuz", iz.tid
        iz.yangi_odam = False
        for ish in self.ishlar:
            if ish.tid == iz.tid:
                ish.hodim = p.id
        iz.yuz_qoshilgan = 0
        self._yuz_qosh(iz, p, ts)

    def _kirish(self, h: Hodim, iz: Iz, izoh: str):
        if iz.born - self.start_ts < 5:
            izoh = "xizmat boshlanganda xonada edi"
        else:
            z = zona(iz.born_box)
            izoh += f", {z}" if z else ", xona ichida paydo bo'ldi"
        klip, rasm = self._ish(h.id, iz.tid, "kirish", iz.born, iz.born - config.CLIP_OLDIN,
                               iz.born + config.CLIP_KEYIN, iz.born + 0.8)
        iz.kirish_id = db.add_event(self.con, h.id, "kirish", iz.born, klip, izoh, rasm)
        log.info("KIRDI %s %s (%s)", h.yorliq, _hm(iz.born), izoh)
        h.ichkarida, h.kutilgan_chiqish = True, None

    def _yoqoldi(self, iz: Iz):
        if iz.hodim is None or iz.hodim < 0:
            return
        h = self.hodimlar.get(iz.hodim)
        if h is None:
            return
        if h.iz == iz.tid:
            h.iz = None
        h.last_seen = iz.last
        z = zona(iz.last_box)
        log.info("iz %d (%s) yo'qoldi%s", iz.tid, h.yorliq, f" — {z} zonasida" if z else " — xona ichida")
        if h.ichkarida and z and not h.kutilgan_chiqish:
            klip, rasm = self._ish(h.id, iz.tid, "chiqish", iz.last, iz.last - config.EXIT_OLDIN,
                                   iz.last + config.EXIT_KEYIN, iz.last - 0.6)
            h.kutilgan_chiqish = {"ts": iz.last, "klip": klip, "rasm": rasm, "zona": z}

    # --- klip va kadr
    def _ish(self, hid, tid, tur, ts, start, end, rasm_ts) -> tuple[str, str]:
        d = datetime.fromtimestamp(ts)
        nom = f"hodim_{hid}_{tur}_{d.strftime('%H-%M-%S')}"
        papka = Path(d.strftime("%Y-%m-%d"))
        rang = (0, 200, 0) if tur == "kirish" else (0, 0, 230)
        self.ishlar.append(KlipIsh(start, end, tid, hid, config.CLIPS / papka / f"{nom}.mp4",
                                   config.CLIPS / papka / f"{nom}.jpg", rasm_ts, rang))
        return (papka / f"{nom}.mp4").as_posix(), (papka / f"{nom}.jpg").as_posix()

    def _bekor(self, k: dict):
        paths = [config.CLIPS / k["klip"], config.CLIPS / k["rasm"]]
        self.ishlar = [i for i in self.ishlar if i.path != paths[0]]
        for p in paths:
            p.unlink(missing_ok=True)
        # yozuvchi navbatida bo'lsa — keyin ham o'chiriladi
        t = threading.Timer(20, lambda: [p.unlink(missing_ok=True) for p in paths])
        t.daemon = True
        t.start()

    def _chiz(self, f, dets, ish: KlipIsh):
        sx = config.CLIP_W / config.FRAME_W
        sy = config.CLIP_H / config.FRAME_H
        h = self.hodimlar.get(ish.hodim)
        yorliq = h.yorliq if h else f"Hodim {ish.hodim}"
        f = f.copy()
        for tid, (x1, y1, x2, y2) in dets:
            p1, p2 = (int(x1 * sx), int(y1 * sy)), (int(x2 * sx), int(y2 * sy))
            if tid == ish.tid:
                cv2.rectangle(f, p1, p2, ish.rang, 3)
                cv2.putText(f, yorliq, (p1[0], max(20, p1[1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, ish.rang, 2)
            else:
                cv2.rectangle(f, p1, p2, (160, 160, 160), 1)
        return f

    def _klip(self, ish: KlipIsh):
        tanlangan = [(ts, f, d) for ts, f, d in self.buf if ish.start <= ts <= ish.end]
        if not tanlangan:
            log.warning("klip uchun kadr yo'q: %s", ish.path.name)
            return
        # kadr: rasm_ts ga eng yaqin, odam ko'ringan kadr
        bor = [x for x in tanlangan if any(t == ish.tid for t, _ in x[2])] or tanlangan
        _, f_r, d_r = min(bor, key=lambda x: abs(x[0] - ish.rasm_ts))
        _imwrite(ish.rasm, self._chiz(f_r, d_r, ish))
        if self.yozuvchi is None or len(tanlangan) < 5:
            return
        frames = [self._chiz(f, d, ish) for _, f, d in tanlangan]
        fps = (len(frames) - 1) / max(0.5, tanlangan[-1][0] - tanlangan[0][0])
        self.yozuvchi.q.put((ish.path, frames, fps))

    def versiya_tekshir(self, ts: float):
        v = db.get_holat(self.con, "versiya")[0]
        if v != self.versiya or db.sana(ts) != self.kun:
            self.versiya = v
            self.yukla(ts)


def eski_kliplarni_ochir(bugun: datetime | None = None) -> list[str]:
    """KLIP_SAQLASH_KUN dan eski kunlarning klip papkalarini o'chiradi (baza yozuvlari qoladi)."""
    bugun = bugun or datetime.now()
    ochdi = []
    if not config.CLIPS.exists():
        return ochdi
    for p in config.CLIPS.iterdir():
        try:
            kun = datetime.strptime(p.name, "%Y-%m-%d")
        except ValueError:
            continue
        if p.is_dir() and (bugun - kun).days > config.KLIP_SAQLASH_KUN:
            shutil.rmtree(p, ignore_errors=True)
            ochdi.append(p.name)
    if ochdi:
        log.info("eski kliplar o'chirildi: %s", ", ".join(sorted(ochdi)))
    return ochdi


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


# ---------------------------------------------------------------- ishga tushirish

def _setup_logging():
    config.LOGS.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(config.LOGS / "xizmat.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.addHandler(logging.StreamHandler())
    log.setLevel(logging.INFO)


def _tracker_yaml() -> Path:
    p = config.DATA / "bytetrack.yaml"
    p.write_text("tracker_type: bytetrack\ntrack_high_thresh: 0.35\ntrack_low_thresh: 0.1\n"
                 "new_track_thresh: 0.45\ntrack_buffer: 45\nmatch_thresh: 0.8\nfuse_score: True\n",
                 encoding="utf-8")
    return p


def main():
    _setup_logging()
    os.environ.setdefault("YOLO_OFFLINE", "1")
    os.environ.setdefault("YOLO_CONFIG_DIR", str(config.DATA / "ultralytics"))
    from ultralytics import YOLO
    from .reid import ReID
    from .yuz import Yuz

    config.STOP_FILE.unlink(missing_ok=True)
    url = config.rtsp_url()
    model = YOLO(str(config.YOLO_WEIGHTS))
    reid = ReID(str(config.REID_WEIGHTS))
    yuz = Yuz(str(config.YUZ_DET), str(config.YUZ_REC))
    tracker = str(_tracker_yaml())
    con = db.connect()
    k = Kuzatuvchi(con, reid, yuz)
    k.yozuvchi = KlipYozuvchi()
    k.yozuvchi.start()
    oq = Oquvchi(url)
    oq.start()
    log.info("xizmat boshlandi (YOLO va kiyim: %s, yuz: CPU)", reid.device)

    n, t_fps, oxirgi_holat, oxirgi_tozalash = 0, time.time(), 0.0, 0.0
    while not config.STOP_FILE.exists():
        try:
            ts, frame = oq.q.get(timeout=2)
        except queue.Empty:
            ts, frame = time.time(), None
        dets = []
        if frame is not None:
            small = cv2.resize(frame, (config.FRAME_W, config.FRAME_H))
            r = model.track(small, persist=True, classes=[0], tracker=tracker, conf=config.DET_CONF,
                            device=0, verbose=False)[0]
            if r.boxes.id is not None:
                for b, t, c in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.id.int().cpu().numpy(),
                                   r.boxes.conf.cpu().numpy()):
                    box = tuple(float(x) for x in b)
                    if box[3] - box[1] >= config.MIN_BOX_H and box[3] >= config.MIN_FOOT_Y:
                        dets.append((int(t), box, float(c)))
            n += 1
        try:
            k.kadr(ts, frame, dets)
        except Exception:
            log.exception("kadrni qayta ishlashda xato")
        now = time.time()
        if now - oxirgi_holat >= 5:
            fps = n / (now - t_fps)
            n, t_fps, oxirgi_holat = 0, now, now
            db.set_holat(con, "xizmat", json.dumps({
                "fps": round(fps, 1), "kamera": oq.ulandi,
                "ichkarida": sorted(h.id for h in k.hodimlar.values() if h.ichkarida),
                "korinmoqda": sorted(h.id for h in k.hodimlar.values() if h.iz is not None)}))
            k.versiya_tekshir(now)
        if now - oxirgi_tozalash >= 3600:
            oxirgi_tozalash = now
            eski_kliplarni_ochir()
    log.info("STOP fayli topildi — xizmat to'xtadi")
    time.sleep(2)  # navbatdagi kliplar yozib bo'linsin


if __name__ == "__main__":
    main()
