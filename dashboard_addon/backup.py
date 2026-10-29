"""Zip a dashboard folder (without .venv / __pycache__ / .pytest_cache) before its files are changed.

    python backup.py "D:\\Demos\\odoo telegram integration\\dashboard" "D:\\...\\backups\\dashboard_before_X.zip"
"""
import os
import sys
import zipfile

src, dst = sys.argv[1], sys.argv[2]
skip_dirs = {".venv", "__pycache__", ".pytest_cache"}
ok = skipped = 0
with zipfile.ZipFile(dst, "x", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            full = os.path.join(root, name)
            try:
                zf.write(full, os.path.join("dashboard", os.path.relpath(full, src)))
                ok += 1
            except OSError as exc:
                skipped += 1
                print("skipped:", full, exc)
print(f"backup: {dst} ({ok} files, {skipped} skipped, {os.path.getsize(dst) / 2**20:.1f} MB)")
