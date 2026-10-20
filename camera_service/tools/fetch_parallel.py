"""Download one file over several parallel HTTP range requests and check its SHA-256.

    python tools\\fetch_parallel.py URL OUT_FILE SHA256 [--parts 8]

Used for the model weights: single connections to GitHub run at about 55 KB/s here.
Stdlib only; parts resume after an interrupted run.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import os
import sys
import time
import urllib.request

UA = {"User-Agent": "kamera-setup"}


def total_size(url: str) -> int:
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=UA), timeout=60) as r:
        return int(r.headers["Content-Length"])


def fetch(url: str, part: str, first: int, last: int) -> int:
    need = last - first + 1
    for _attempt in range(8):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have >= need:
            return need
        req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={first + have}-{last}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab") as f:
                while chunk := r.read(1 << 16):
                    f.write(chunk)
        except OSError as exc:
            print(f"  {os.path.basename(part)}: {exc}, retry", flush=True)
            time.sleep(3)
    raise RuntimeError(f"{part}: not complete after retries")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("sha256")
    ap.add_argument("--parts", type=int, default=8)
    args = ap.parse_args()
    size = total_size(args.url)
    step = -(-size // args.parts)
    jobs = [(f"{args.out}.part{i}", i * step, min(size, (i + 1) * step) - 1) for i in range(args.parts)]
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(args.parts) as pool:
        list(pool.map(lambda job: fetch(args.url, *job), jobs))
    digest = hashlib.sha256()
    tmp = args.out + ".tmp"
    with open(tmp, "wb") as out:
        for part, _first, _last in jobs:
            with open(part, "rb") as f:
                while chunk := f.read(1 << 20):
                    out.write(chunk)
                    digest.update(chunk)
    elapsed = time.monotonic() - started
    if digest.hexdigest() != args.sha256.lower():
        print(f"{os.path.basename(args.out)}: SHA-256 MISMATCH, file kept as {tmp}")
        return 1
    os.replace(tmp, args.out)
    for part, _first, _last in jobs:
        os.remove(part)
    print(f"{os.path.basename(args.out)}: {size / 2**20:.1f} MB in {elapsed:.0f} s "
          f"({size / 2**10 / elapsed:.0f} KB/s), sha256 OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
