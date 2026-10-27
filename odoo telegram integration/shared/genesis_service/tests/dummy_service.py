"""Test program for run_service_tests.py: prints a tick every second, starts one grandchild (to prove the
whole tree is handled), exits on GENESIS_STOP_FILE, or crashes with code 3 after DUMMY_EXIT_AFTER seconds."""
import os
import subprocess
import sys
import time
from pathlib import Path

stop = os.environ.get("GENESIS_STOP_FILE")
exit_after = float(os.environ.get("DUMMY_EXIT_AFTER", "0"))
pid_file = os.environ.get("DUMMY_PID_FILE")
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
if pid_file:
    Path(pid_file).write_text(f"{os.getpid()} {grandchild.pid}", encoding="utf-8")
print(f"dummy started pid={os.getpid()} grandchild={grandchild.pid} token=abc password=hunter2", flush=True)
t0 = time.monotonic()
n = 0
while True:
    if stop and os.path.exists(stop):
        print("dummy: stop file seen, exiting", flush=True)
        grandchild.kill()
        sys.exit(0)
    if exit_after and time.monotonic() - t0 >= exit_after:
        print("dummy: simulated crash", file=sys.stderr, flush=True)
        grandchild.kill()
        sys.exit(3)
    n += 1
    print(f"tick {n}", flush=True)
    time.sleep(1)
