"""Synthetic camera frames for the test database (W3-04). No camera, no real frame, no person or face:
each picture is a flat drawing (wall, door, a coloured box as the marker) with the words "SUN'IY KADR".

For every frame path that ``api.v_staff_day`` on genesis_test lists (W0 fixtures), a picture is written
under ``Settings.frames_dir`` with the same D-29 tail (YYYY\\MM\\DD\\<camera>\\<file>). It refuses to run on
the real database or to write under E:\\data.

    cd apps\\w3-dashboard
    .venv\\Scripts\\python.exe -E scripts\\make_fixture_frames.py
"""

from __future__ import annotations

import sys
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent.parent
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

from PIL import Image, ImageDraw  # noqa: E402

from core import camera as cam  # noqa: E402

SIZE = (640, 360)
FORBIDDEN = Path("E:/data")
_MARKERS = {1: (79, 70, 229), 2: (234, 88, 12), 3: (22, 163, 74), 4: (14, 165, 233)}


def draw(staff_no: int, kind: str, at: str) -> Image.Image:
    """A flat synthetic picture: no person, no face."""
    image = Image.new("RGB", SIZE, (203, 213, 225))
    pen = ImageDraw.Draw(image)
    pen.rectangle((0, 250, SIZE[0], SIZE[1]), fill=(148, 163, 184))  # floor
    pen.rectangle((420, 60, 540, 250), fill=(120, 85, 60), outline=(70, 50, 35), width=4)  # door
    x = 300 if kind == "in" else 470
    pen.rectangle((x - 30, 140, x + 30, 250), fill=_MARKERS.get(staff_no, (100, 116, 139)))  # marker box
    pen.rectangle((0, 0, SIZE[0], 34), fill=(15, 23, 42))
    pen.text((12, 10), "SUN'IY KADR (test) - haqiqiy kamera emas", fill=(255, 255, 255))
    word = "kirdi" if kind == "in" else "chiqdi"
    pen.text((12, 300), f"Hodim {staff_no} - {word} {at}", fill=(15, 23, 42))
    return image


def write_frames(frames_dir: Path, items) -> list[Path]:
    """``items``: (stored path, staff number, kind, HH:MM). Writes one picture per usable path."""
    root = Path(frames_dir).resolve()
    if root.is_relative_to(FORBIDDEN.resolve()):
        raise SystemExit(f"refused: {root} is under {FORBIDDEN} (real camera files)")
    written = []
    for stored, staff_no, kind, at in items:
        tail = cam.frame_tail(stored)
        if tail is None:
            continue
        target = root.joinpath(*tail)
        target.parent.mkdir(parents=True, exist_ok=True)
        draw(staff_no, kind, at).save(target, "JPEG", quality=85)
        written.append(target)
    return written


def main() -> int:
    from core import db, queries
    from core.settings import REAL_DATABASE, load_settings

    settings = load_settings()
    if settings.db.database == REAL_DATABASE:
        raise SystemExit("refused: synthetic frames are only for the test database")
    with db.session(settings) as session:
        frame = queries.staff_days(session, "2000-01-01", "2100-01-01")
    items = [(f.path, item.staff_no, f.kind, cam.fmt_time(f.at)) for item in cam.days_from_frame(frame, settings.tz)
             for f in item.frames]
    written = write_frames(settings.frames_dir, items)
    print(f"{len(written)} synthetic frames -> {settings.frames_dir} ({settings.db.label})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
