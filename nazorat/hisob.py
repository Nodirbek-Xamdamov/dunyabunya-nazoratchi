"""Kunlik hisob: keldi, ketdi, chiqib-kirishlar, umumiy vaqt va xonadagi vaqt.

Misol: 09:00 keldi, 13:10 chiqdi, 13:15 qaytdi, 16:36 chiqdi, 17:05 qaytdi, 19:00 ketdi
-> umumiy 10 soat, tashqarida 34 daqiqa, xonada 9 soat 26 daqiqa.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chiqish:
    chiqdi: float
    qaytdi: float | None  # None — qaytmadi (bu ketish)
    klip_chiqish: str | None = None
    klip_qaytish: str | None = None

    @property
    def davomiylik(self) -> float:
        return (self.qaytdi - self.chiqdi) if self.qaytdi is not None else 0.0


@dataclass
class KunHisobi:
    hodim_id: int
    keldi: float
    ketdi: float | None          # None — hali xonada
    oxiri: float                 # hisob shu vaqtgacha (ketdi yoki hozir)
    chiqishlar: list[Chiqish] = field(default_factory=list)
    klip_keldi: str | None = None
    klip_ketdi: str | None = None

    @property
    def umumiy(self) -> float:
        return max(0.0, self.oxiri - self.keldi)

    @property
    def tashqarida(self) -> float:
        return sum(c.davomiylik for c in self.chiqishlar)

    @property
    def xonada(self) -> float:
        return max(0.0, self.umumiy - self.tashqarida)


def kun_hisobi(events, hozir: float) -> dict[int, KunHisobi]:
    """events: bir kunlik hodisalar (hodim_id, tur, ts, klip), vaqt bo'yicha tartiblangan."""
    per: dict[int, list] = {}
    for e in events:
        per.setdefault(e["hodim_id"], []).append(e)
    natija = {}
    for hid, evs in per.items():
        evs = sorted(evs, key=lambda e: e["ts"])
        keldi = evs[0]["ts"]
        klip_keldi = evs[0]["klip"] if evs[0]["tur"] == "kirish" else None
        ichkarida = True
        chiqishlar: list[Chiqish] = []
        for e in evs[1:] if evs[0]["tur"] == "kirish" else evs:
            if e["tur"] == "chiqish" and ichkarida:
                chiqishlar.append(Chiqish(e["ts"], None, klip_chiqish=e["klip"]))
                ichkarida = False
            elif e["tur"] == "kirish" and not ichkarida:
                chiqishlar[-1].qaytdi = e["ts"]
                chiqishlar[-1].klip_qaytish = e["klip"]
                ichkarida = True
        ketdi = klip_ketdi = None
        if not ichkarida:
            oxirgi = chiqishlar.pop()   # oxirgi qaytmagan chiqish — bu ketish
            ketdi, klip_ketdi = oxirgi.chiqdi, oxirgi.klip_chiqish
        natija[hid] = KunHisobi(hid, keldi, ketdi, ketdi if ketdi is not None else max(hozir, evs[-1]["ts"]),
                                chiqishlar, klip_keldi, klip_ketdi)
    return natija


def soat_daqiqa(sek: float) -> str:
    if sek < 60:
        return f"{int(round(sek))} soniya"
    m = int(round(sek / 60))
    h, m = divmod(m, 60)
    if h and m:
        return f"{h} soat {m} daqiqa"
    if h:
        return f"{h} soat"
    return f"{m} daqiqa"
