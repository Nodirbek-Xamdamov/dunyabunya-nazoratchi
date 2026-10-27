"""W2-04 acceptance set: 20 synthetic group conversations (no real data), labelled by hand.

`truth` is how a person reading the chat would split it into conversations, not what the rules give.
Each row: (local time, sender, truth label or None = not part of any episode, options).
Options: reply=<row index>, kind=<kind>, thread=<id>, id=<tg id>, day=+1 (next day), text=<only for the reader>.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from w2analyst.segmenter import TZ, Msg

DAY = date(2026, 10, 20)
PEOPLE = {"C1": (101, "client"), "C2": (102, "client"), "C3": (103, "client"),
          "E1": (201, "employee"), "E2": (202, "employee"), "BOT": (900, "unknown"), "SYS": (None, None)}

S = {
    "S01 tez savol-javob": [
        ("10:00", "C1", "A", {"text": "Assalomu alaykum, armatura 12 bormi?"}),
        ("10:03", "E1", "A", {"text": "Bor, 4.3 mln/tonna"}),
        ("10:04", "C1", "A", {"text": "2 tonna yozib qo'ying"})],
    "S02 2 soat oraliqli ikki mavzu": [
        ("09:10", "C1", "A", {}), ("09:15", "E1", "A", {}),
        ("11:30", "C1", "B", {"text": "Kecha olgan sementimiz namlangan"}), ("11:40", "E1", "B", {})],
    "S03 aniq 30 daqiqa": [
        ("10:00", "C1", "A", {"text": "Mashina chiqdimi?"}), ("10:30", "E1", "A", {"text": "Ha, yo'lda"})],
    "S04 31 daqiqadan keyin boshqa mijoz yangi savol": [
        ("10:00", "C1", "A", {}), ("10:02", "E1", "A", {}),
        ("10:33", "C2", "B", {"text": "Gipsokarton narxi qancha?"})],
    "S05 3 soatdan keyin reply bilan javob": [
        ("09:00", "C1", "A", {"text": "Zakaz qachon keladi?"}),
        ("10:15", "C2", "B", {"text": "Hisob-faktura yuboring"}), ("10:20", "E2", "B", {}),
        ("12:00", "E1", "A", {"reply": 0, "text": "Ertaga 10:00 da"})],
    "S06 mijoz 50 daqiqada qaytadi": [
        ("10:00", "C1", "A", {}), ("10:05", "E1", "A", {"text": "Tekshirib aytaman"}),
        ("10:55", "C1", "A", {"text": "Xo'sh, nima bo'ldi?"})],
    "S07 hodimning ikki alohida e'loni": [
        ("10:00", "E1", "A", {"text": "Bugun ombor 15:00 gacha"}),
        ("10:55", "E1", "B", {"text": "Yangi narx ro'yxati ilovada"})],
    "S08 hodim 45 daqiqada reply'siz javob beradi": [
        ("10:00", "C1", "A", {"text": "Profil 60x27 narxi?"}), ("10:45", "E1", "A", {"text": "28 000 so'm"})],
    "S09 servis va bot xabarlari": [
        ("10:00", "C1", "A", {}), ("10:01", "SYS", None, {"kind": "service"}),
        ("10:02", "BOT", None, {}), ("10:03", "E1", "A", {})],
    "S10 yarim tun (hisobot kuni chegarasi)": [
        ("23:50", "C1", "A", {}), ("23:55", "E1", "A", {}),
        ("00:05", "C1", "B", {"day": 1, "text": "Rahmat, ertaga kutamiz"})],
    "S11 forum mavzulari aralash": [
        ("10:00", "C1", "A", {"thread": 5}), ("10:01", "C2", "B", {"thread": 7}),
        ("10:02", "E1", "A", {"thread": 5}), ("10:03", "E2", "B", {"thread": 7})],
    "S12 mavzu ildiziga 'reply' (haqiqiy reply emas)": [
        ("08:00", "E1", "A", {"thread": 5000, "id": 5000, "text": "Dastavka mavzusi"}),
        ("15:00", "C1", "B", {"thread": 5000, "reply": 0, "text": "Mashina kelmadi"})],
    "S13 30 daqiqa ichida ikki parallel suhbat": [
        ("10:00", "C1", "A", {"text": "Shifer bormi?"}), ("10:05", "C2", "B", {"text": "To'lovni o'tkazdim"}),
        ("10:06", "E1", "A", {"reply": 0}), ("10:07", "E2", "B", {"reply": 1})],
    "S14 voice va rasm albomi": [
        ("10:00:00", "C1", "A", {"kind": "voice"}), ("10:00:05", "C1", "A", {"kind": "photo"}),
        ("10:00:05", "C1", "A", {"kind": "photo"}), ("10:10", "E1", "A", {})],
    "S15 eski suhbatga reply va qayta jonlanish": [
        ("09:00", "C1", "A", {"text": "Kafel siniq keldi"}), ("09:05", "E1", "A", {}),
        ("10:00", "C2", "B", {"text": "Dastavka bormi bugun?"}), ("10:10", "E2", "B", {}),
        ("10:15", "C1", "A", {"reply": 0, "text": "Hali almashtirilmadi"}),
        ("10:16", "E1", "A", {"text": "Bugun almashtiramiz"}), ("10:20", "C2", "B", {"text": "Soat nechada?"})],
    "S16 o'chirilgan va tahrirlangan xabar": [
        ("10:00", "C1", "A", {}), ("10:01", "C1", "A", {"text": "(o'chirilgan)"}), ("10:02", "E1", "A", {})],
    "S17 40 daqiqadan keyin stiker bilan rahmat": [
        ("10:00", "C1", "A", {}), ("10:05", "E1", "A", {"text": "Yuborildi"}),
        ("10:44", "C1", "A", {"kind": "sticker"})],
    "S18 ertalabki salom va 3 soatdan keyin buyurtma": [
        ("08:00", "E1", "A", {"text": "Xayrli tong!"}), ("11:00", "C1", "B", {"text": "50 qop sement"}),
        ("11:05", "E1", "B", {})],
    "S19 uzoq reply zanjiri": [
        ("09:00", "C1", "A", {}), ("09:10", "E1", "A", {"text": "Narxni hisoblab beraman"}),
        ("13:00", "C1", "A", {"reply": 1, "text": "Hisobladingizmi?"}), ("13:05", "E1", "A", {})],
    "S20 boshqa chatdan forward va muhokama": [
        ("14:00", "C1", "A", {"text": "[forward] Prorab xabari"}), ("14:04", "E1", "A", {}),
        ("14:08", "C1", "A", {})],
}


def build(name: str) -> tuple[list[Msg], list[str | None]]:
    """Messages and truth labels of one scenario; ids 1000+row (or opt id), source = scenario number."""
    src = int(name[1:3])
    rows = S[name]
    ids = [opt.get("id", 1000 + i) for i, (_, _, _, opt) in enumerate(rows)]
    msgs, truth = [], []
    for i, (hhmm, who, label, opt) in enumerate(rows):
        h, mi, *sec = (int(x) for x in hhmm.split(":"))
        at = datetime(DAY.year, DAY.month, DAY.day, h, mi, sec[0] if sec else 0, tzinfo=TZ) + timedelta(days=opt.get("day", 0))
        uid, role = PEOPLE[who]
        reply = opt.get("reply")
        msgs.append(Msg(source_id=src, tg_message_id=ids[i], sent_at=at, sender_tg_id=uid, sender_is_bot=who == "BOT",
                        kind=opt.get("kind", "text"), reply_to=ids[reply] if reply is not None else None,
                        thread_id=opt.get("thread"), sender_role=role))
        truth.append(label)
    return msgs, truth


def partition(labels: list[str | None]) -> set[frozenset[int]]:
    groups: dict[str, set[int]] = {}
    for i, lab in enumerate(labels):
        if lab is not None:
            groups.setdefault(lab, set()).add(i)
    return {frozenset(g) for g in groups.values()}
