"""Take every desk and employee out of use in camera.db, backup first. Same as the dashboard's «Stolni o'chirish»:
rows are marked inactive (desk zone emptied), not deleted, so they can be brought back with active = 1.

    python tools\\clear_desks.py data\\camera.db --backup data\\camera_before_clear.db
"""

import sqlite3
import sys
import time

path = sys.argv[1]
backup = sys.argv[sys.argv.index("--backup") + 1] if "--backup" in sys.argv else None
conn = sqlite3.connect(path, timeout=10)
conn.execute("PRAGMA busy_timeout=10000")
if backup:
    with sqlite3.connect(backup) as dst:
        conn.backup(dst)
    print("backup:", backup)
now = time.time()
with conn:
    desks = conn.execute("UPDATE cam_desks SET active = 0, zone = '[]', updated_at = ? WHERE active = 1", (now,)).rowcount
    employees = conn.execute("UPDATE cam_employees SET active = 0, updated_at = ? WHERE active = 1", (now,)).rowcount
print(f"taken out of use: {desks} desks, {employees} employees")
conn.close()
