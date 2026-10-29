"""Check that the camera login/password never reached a log, report or review file.

    .venv\\Scripts\\python.exe tools\\check_secrets.py

Prints only file names and counts, never the secret itself.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camsvc.config import PROJECT_DIR, camera_from_env  # noqa: E402

TEXT_SUFFIXES = {".log", ".json", ".txt", ".md", ".csv", ".yaml", ".yml", ".py", ".ps1", ".bat"}
SKIP_PARTS = {".venv", ".git", "wheels", "__pycache__"}


def main() -> int:
    camera = camera_from_env()
    # the password (raw and URL-encoded); the login is masked too, but "admin" is an ordinary word
    secrets = [s for s in camera.secrets() if camera.password and s in (camera.password, quote(camera.password, safe=""))]
    if not secrets:
        print("Parol .env da yo'q yoki juda qisqa, tekshiruv o'tkazilmadi.")
        return 0
    checked = hits = 0
    for path in PROJECT_DIR.rglob("*"):
        if not path.is_file() or path.name.startswith(".env") or SKIP_PARTS & set(path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        checked += 1
        found = sum(text.count(s) for s in secrets)
        if found:
            hits += found
            print(f"TOPILDI: {path.relative_to(PROJECT_DIR)} ({found} marta)")
    print(f"tekshirildi: {checked} fayl, parol/login topildi: {hits} marta")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
