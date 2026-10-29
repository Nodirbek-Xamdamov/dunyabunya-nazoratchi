"""Adds the «Yangi kamera» pages to a dashboard folder (stdlib only, run with any Python 3.10+).

    python install.py "D:\\Demos\\odoo telegram integration\\dashboard" --check   # only verify, change nothing
    python install.py "D:\\Demos\\odoo telegram integration\\dashboard"           # copy files + patch app.py
    python install.py "D:\\Demos\\odoo telegram integration\\dashboard" --uninstall

Copies camera/*.py, views/kamera*.py and tests/test_camera.py, and inserts four lines into app.py at
fixed anchors. Every anchor must be found exactly once, otherwise nothing is changed. Running it twice
is harmless. The live dashboard must be stopped while its files change (see LOYIHA_HOLATI.md).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ADDON = Path(__file__).resolve().parent
FILES = [*(f"camera/{p.name}" for p in sorted((ADDON / "camera").glob("*.py"))),
         *(f"views/{p.name}" for p in sorted((ADDON / "views").glob("kamera*.py"))),
         "tests/test_camera.py"]
OBSOLETE = ["camera/prayer.py"]  # files of earlier versions, removed from the dashboard on install

IMPORT_ANCHOR = "from views import agents, categories, groups, overview, own, reports, response, search, staff, system, trends  # noqa: E402\n"
IMPORT_LINE = "from views import kamera  # noqa: E402  «Yangi kamera» section (camera desk time), see views/kamera.py\n"
PAGES_ANCHOR = "nav.set_pages(PAGES)\n"
PAGES_LINES = ("for key, (render, title, icon, url_path) in kamera.PAGES.items():  # «Yangi kamera» pages\n"
               "    PAGES[key] = st.Page(render, title=title, icon=icon, url_path=url_path)\n")
SECTION_ANCHOR = "sections[t(\"nav.custom\")] = [PAGES[item.id] for item in own_pages]\n"
SECTION_LINE = "sections = kamera.with_section(sections, t(\"nav.main\"), [PAGES[key] for key in kamera.PAGES])\n"

PATCHES = [  # (anchor, inserted text, before/after)
    (IMPORT_ANCHOR, IMPORT_LINE, "after"),
    (PAGES_ANCHOR, PAGES_LINES, "before"),
    (SECTION_ANCHOR, SECTION_LINE, "after"),
]


def patched(text: str) -> str:
    for anchor, insert, where in PATCHES:
        if insert in text:
            continue
        if text.count(anchor) != 1:
            raise SystemExit(f"app.py: anchor not found exactly once: {anchor.strip()!r} - nothing changed")
        text = text.replace(anchor, anchor + insert if where == "after" else insert + anchor)
    return text


def unpatched(text: str) -> str:
    for _anchor, insert, _where in PATCHES:
        text = text.replace(insert, "")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dashboard", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    app = args.dashboard / "app.py"
    text = app.read_text(encoding="utf-8")  # universal newlines: the anchors use \n
    newline = "\r\n" if b"\r\n" in app.read_bytes() else "\n"  # written back with the file's own line endings
    if args.uninstall:
        app.write_text(unpatched(text), encoding="utf-8", newline=newline)
        for name in FILES + OBSOLETE:
            (args.dashboard / name).unlink(missing_ok=True)
        print(f"removed: app.py patch and {len(FILES)} files")
        return 0
    new_text = patched(text)
    if args.check:
        print(f"OK: anchors found, {len(FILES)} files to copy, app.py {'already patched' if new_text == text else 'will be patched'}")
        return 0
    for name in FILES:
        target = args.dashboard / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ADDON / name, target)
    for name in OBSOLETE:
        (args.dashboard / name).unlink(missing_ok=True)
    if new_text != text:
        app.write_text(new_text, encoding="utf-8", newline=newline)
    print(f"installed: {len(FILES)} files, app.py {'patched' if new_text != text else 'already patched'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
