"""Speed check on the full demo database (not part of the pytest run):

    .venv\\Scripts\\python.exe tests\\bench_pages.py [--presets last7 last30]

Every page is rendered with an empty cache and then again with a warm cache (Streamlit AppTest).
Targets: < 8 s without cache, < 2-3 s with cache."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASHBOARD_DIR))
os.environ["DASHBOARD_DEMO_MODE"] = "1"
os.environ["DASHBOARD_DEMO_DB_PATH"] = str(DASHBOARD_DIR / "demo" / "demo_monitor.db")
os.environ["DASHBOARD_PASSWORD"] = ""

import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from tests.test_views import PAGE_SCRIPT, PAGES  # noqa: E402


def render(name: str, preset: str) -> tuple[float, bool]:
    app = AppTest.from_string(PAGE_SCRIPT.format(root=str(DASHBOARD_DIR), name=name), default_timeout=180)
    app.query_params["p"] = preset
    started = time.perf_counter()
    app.run()
    return time.perf_counter() - started, bool(app.exception or app.error)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--presets", nargs="+", default=["last7", "last30"])
    parser.add_argument("--pages", nargs="+", default=PAGES)
    args = parser.parse_args()
    print(f"{'page':12} {'preset':8} {'cold s':>7} {'warm s':>7}")
    for preset in args.presets:
        for name in args.pages:
            st.cache_data.clear()
            cold, failed = render(name, preset)
            warm, failed_again = render(name, preset)
            flag = "  ERROR" if failed or failed_again else ""
            print(f"{name:12} {preset:8} {cold:7.2f} {warm:7.2f}{flag}")


if __name__ == "__main__":
    main()
