"""SQLite baza: odamlar (xodim / mehmon / noma'lum), yuz va kiyim vektorlari, kirish/chiqish hodisalari."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime

import numpy as np

from . import config

TURLAR = ("xodim", "mehmon", "nomalum")

SCHEMA = """
CREATE TABLE IF NOT EXISTS hodimlar (
    id INTEGER PRIMARY KEY,
    nom TEXT,
    yaratilgan REAL NOT NULL,
    faol INTEGER NOT NULL DEFAULT 1,
    yashirin INTEGER NOT NULL DEFAULT 0,
    tur TEXT NOT NULL DEFAULT 'nomalum',
    rasm TEXT,
    qolda INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS korinish (
    id INTEGER PRIMARY KEY,
    hodim_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    vec BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS korinish_hodim ON korinish(hodim_id);
CREATE TABLE IF NOT EXISTS yuzlar (
    id INTEGER PRIMARY KEY,
    hodim_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    sifat REAL NOT NULL,
    vec BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS yuzlar_hodim ON yuzlar(hodim_id);
CREATE TABLE IF NOT EXISTS hodisalar (
    id INTEGER PRIMARY KEY,
    hodim_id INTEGER NOT NULL,
    tur TEXT NOT NULL CHECK (tur IN ('kirish', 'chiqish')),
    ts REAL NOT NULL,
    sana TEXT NOT NULL,
    klip TEXT,
    izoh TEXT,
    rasm TEXT
);
CREATE INDEX IF NOT EXISTS hodisalar_sana ON hodisalar(sana, ts);
-- odam bir joyda qimirlamay o'tirgan vaqt (kunlik), xodimni avtomatik aniqlash uchun
CREATE TABLE IF NOT EXISTS otirish (
    hodim_id INTEGER NOT NULL,
    sana TEXT NOT NULL,
    soniya REAL NOT NULL,
    PRIMARY KEY (hodim_id, sana)
);
CREATE TABLE IF NOT EXISTS holat (
    kalit TEXT PRIMARY KEY,
    qiymat TEXT,
    ts REAL
);
"""

# eski bazaga qo'shiladigan ustunlar
_MIGRATSIYA = {
    "hodimlar": {"yashirin": "INTEGER NOT NULL DEFAULT 0", "tur": "TEXT NOT NULL DEFAULT 'nomalum'", "rasm": "TEXT",
                 "qolda": "INTEGER NOT NULL DEFAULT 0"},
    "hodisalar": {"rasm": "TEXT"},
}


def connect(path=None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    if str(path) != ":memory:":
        config.DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    for jadval, ustunlar in _MIGRATSIYA.items():
        bor = {r["name"] for r in con.execute(f"PRAGMA table_info({jadval})")}
        if bor:
            for nom, tur in ustunlar.items():
                if nom not in bor:
                    con.execute(f"ALTER TABLE {jadval} ADD COLUMN {nom} {tur}")
    con.executescript(SCHEMA)
    return con


def sana(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def kun_boshi(ts: float) -> float:
    d = datetime.fromtimestamp(ts)
    return datetime(d.year, d.month, d.day).timestamp()


def new_hodim(con, ts: float, tur: str = "nomalum") -> int:
    cur = con.execute("INSERT INTO hodimlar (yaratilgan, tur) VALUES (?, ?)", (ts, tur))
    con.commit()
    return cur.lastrowid


def add_event(con, hodim_id: int, tur: str, ts: float, klip: str | None = None, izoh: str | None = None,
              rasm: str | None = None) -> int:
    cur = con.execute(
        "INSERT INTO hodisalar (hodim_id, tur, ts, sana, klip, izoh, rasm) VALUES (?,?,?,?,?,?,?)",
        (hodim_id, tur, ts, sana(ts), klip, izoh, rasm))
    con.commit()
    return cur.lastrowid


def _vec_save(con, jadval: str, hodim_id: int, ts: float, vecs: np.ndarray, sifat=None, limit=None):
    if jadval == "yuzlar":
        con.executemany("INSERT INTO yuzlar (hodim_id, ts, sifat, vec) VALUES (?,?,?,?)",
                        [(hodim_id, ts, float(q), v.astype(np.float16).tobytes()) for v, q in zip(vecs, sifat)])
        # eng sifatlilarini saqlaymiz
        con.execute("""DELETE FROM yuzlar WHERE hodim_id = ? AND id NOT IN
                       (SELECT id FROM yuzlar WHERE hodim_id = ? ORDER BY sifat DESC LIMIT ?)""",
                    (hodim_id, hodim_id, limit))
    else:
        con.executemany("INSERT INTO korinish (hodim_id, ts, vec) VALUES (?,?,?)",
                        [(hodim_id, ts, v.astype(np.float16).tobytes()) for v in vecs])
        con.execute("""DELETE FROM korinish WHERE hodim_id = ? AND id NOT IN
                       (SELECT id FROM korinish WHERE hodim_id = ? ORDER BY ts DESC LIMIT ?)""",
                    (hodim_id, hodim_id, limit))
    con.commit()


def add_vectors(con, hodim_id: int, ts: float, vecs: np.ndarray) -> None:
    _vec_save(con, "korinish", hodim_id, ts, vecs, limit=config.GALLERY_MAX)


def add_faces(con, hodim_id: int, ts: float, vecs: np.ndarray, sifat) -> None:
    _vec_save(con, "yuzlar", hodim_id, ts, vecs, sifat=sifat, limit=config.YUZ_MAX)


def _load(con, jadval: str, hodim_id: int, since: float = 0.0) -> np.ndarray:
    rows = con.execute(f"SELECT vec FROM {jadval} WHERE hodim_id = ? AND ts >= ? ORDER BY ts",
                       (hodim_id, since)).fetchall()
    if not rows:
        return np.zeros((0, 512), dtype=np.float32)
    return np.stack([np.frombuffer(r["vec"], dtype=np.float16).astype(np.float32) for r in rows])


def load_vectors(con, hodim_id: int, since: float = 0.0) -> np.ndarray:
    return _load(con, "korinish", hodim_id, since)


def load_faces(con, hodim_id: int) -> np.ndarray:
    return _load(con, "yuzlar", hodim_id)


def events_for_day(con, day: str):
    return con.execute("SELECT * FROM hodisalar WHERE sana = ? ORDER BY ts, id", (day,)).fetchall()


def hodimlar(con, faqat_faol=True):
    q = "SELECT * FROM hodimlar" + (" WHERE faol = 1" if faqat_faol else "") + " ORDER BY id"
    return con.execute(q).fetchall()


def yashirinlar(con):
    return [r["id"] for r in con.execute("SELECT id FROM hodimlar WHERE yashirin = 1")]


def set_holat(con, kalit: str, qiymat: str) -> None:
    con.execute("INSERT INTO holat (kalit, qiymat, ts) VALUES (?,?,?) "
                "ON CONFLICT(kalit) DO UPDATE SET qiymat = excluded.qiymat, ts = excluded.ts",
                (kalit, qiymat, time.time()))
    con.commit()


def get_holat(con, kalit: str):
    r = con.execute("SELECT qiymat, ts FROM holat WHERE kalit = ?", (kalit,)).fetchone()
    return (r["qiymat"], r["ts"]) if r else (None, None)


def bump_version(con) -> None:
    """Panel odamlarni o'zgartirsa (tur, birlashtirish, olib tashlash) — xizmat qayta yuklaydi."""
    v, _ = get_holat(con, "versiya")
    set_holat(con, "versiya", str(int(v or 0) + 1))


def add_otirish(con, hodim_id: int, kun: str, soniya: float) -> None:
    con.execute("INSERT INTO otirish (hodim_id, sana, soniya) VALUES (?,?,?) "
                "ON CONFLICT(hodim_id, sana) DO UPDATE SET soniya = soniya + excluded.soniya",
                (hodim_id, kun, soniya))
    con.commit()


def otirish(con, hodim_id: int, kun: str) -> float:
    r = con.execute("SELECT soniya FROM otirish WHERE hodim_id = ? AND sana = ?", (hodim_id, kun)).fetchone()
    return r["soniya"] if r else 0.0


def otirish_kun(con, kun: str) -> dict[int, float]:
    return {r["hodim_id"]: r["soniya"] for r in con.execute("SELECT * FROM otirish WHERE sana = ?", (kun,))}


def avto_xodim(con, hodim_id: int) -> None:
    """Stolda uzoq o'tirgan noma'lum odam — xonaning xodimi (qo'lda belgilanganlar o'zgarmaydi)."""
    con.execute("UPDATE hodimlar SET tur = 'xodim' WHERE id = ? AND tur = 'nomalum' AND qolda = 0", (hodim_id,))
    con.commit()


def merge(con, manba: int, nishon: int, bump: bool = True) -> None:
    """manba odamni nishon odamga qo'shadi: hodisalar, yuz va kiyim vektorlari ko'chadi."""
    for jadval in ("hodisalar", "korinish", "yuzlar"):
        con.execute(f"UPDATE {jadval} SET hodim_id = ? WHERE hodim_id = ?", (nishon, manba))
    for r in con.execute("SELECT sana, soniya FROM otirish WHERE hodim_id = ?", (manba,)).fetchall():
        con.execute("INSERT INTO otirish (hodim_id, sana, soniya) VALUES (?,?,?) "
                    "ON CONFLICT(hodim_id, sana) DO UPDATE SET soniya = soniya + excluded.soniya",
                    (nishon, r["sana"], r["soniya"]))
    con.execute("DELETE FROM otirish WHERE hodim_id = ?", (manba,))
    r = con.execute("SELECT rasm FROM hodimlar WHERE id = ?", (manba,)).fetchone()
    if r and r["rasm"]:
        con.execute("UPDATE hodimlar SET rasm = COALESCE(rasm, ?) WHERE id = ?", (r["rasm"], nishon))
    con.execute("UPDATE hodimlar SET faol = 0 WHERE id = ?", (manba,))
    con.commit()
    if bump:
        bump_version(con)


def rename(con, hodim_id: int, nom: str) -> None:
    con.execute("UPDATE hodimlar SET nom = ? WHERE id = ?", (nom.strip() or None, hodim_id))
    con.commit()


def set_tur(con, hodim_id: int, tur: str, nom: str | None = None) -> None:
    assert tur in TURLAR
    con.execute("UPDATE hodimlar SET tur = ?, qolda = 1 WHERE id = ?", (tur, hodim_id))
    if nom is not None:
        con.execute("UPDATE hodimlar SET nom = ? WHERE id = ?", (nom.strip() or None, hodim_id))
    con.commit()
    bump_version(con)


def set_rasm(con, hodim_id: int, rasm: str) -> None:
    con.execute("UPDATE hodimlar SET rasm = ? WHERE id = ?", (rasm, hodim_id))
    con.commit()


def hide(con, hodim_id: int) -> None:
    """Ro'yxatdan olib tashlaydi (xato aniqlangan narsa). Ko'rinishi saqlanadi: xizmat uni keyin
    taniydi va e'tiborsiz qoldiradi."""
    con.execute("UPDATE hodimlar SET faol = 0, yashirin = 1 WHERE id = ?", (hodim_id,))
    con.commit()
    bump_version(con)
