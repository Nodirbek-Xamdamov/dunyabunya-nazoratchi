"""«Yangi kamera» navigation section: four pages that app.py adds after the main section. They stay outside
the layout engine (ui/layout.py), so the AI chat does not hide, move or rename them."""

from __future__ import annotations

from camera.texts import ct
from views import kamera_bugun, kamera_jonli, kamera_kunlar, kamera_sozlash

SECTION = ct("section")
PAGES = {  # key: (render, title, icon, url path)
    "kamera_bugun": (kamera_bugun.render, ct("page.today"), ":material/today:", "kamera-bugun"),
    "kamera_kunlar": (kamera_kunlar.render, ct("page.days"), ":material/calendar_month:", "kamera-kunlar"),
    "kamera_jonli": (kamera_jonli.render, ct("page.live"), ":material/videocam:", "kamera-jonli"),
    "kamera_sozlash": (kamera_sozlash.render, ct("page.setup"), ":material/tune:", "kamera-sozlash"),
}


def with_section(sections: dict, after: str, pages: list) -> dict:
    """The navigation sections with «Yangi kamera» inserted right after the section named `after`."""
    result = {}
    for name, items in sections.items():
        result[name] = items
        if name == after:
            result[SECTION] = pages
    result.setdefault(SECTION, pages)
    return result
