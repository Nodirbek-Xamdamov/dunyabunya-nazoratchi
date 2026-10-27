"""W4-01: how many streams the camera has open now and what it allows. ISAPI only, opens no video stream.

Usage: python sessions.py [--http-port 80]
Result: intake/out/sessions_<time>.json
"""
from __future__ import annotations

import argparse
import re
import sys

from camlib import OUT, SecretError, load_camera, now_local, write_json
from probe import tag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http-port", type=int, default=80)
    args = ap.parse_args()
    try:
        cam = load_camera()
    except SecretError as e:
        print(f"ERROR: {e}")
        return 2

    result: dict = {"at": now_local().isoformat(timespec="seconds")}
    try:
        dev = cam.isapi("/ISAPI/System/deviceInfo", http_port=args.http_port)
    except Exception as e:  # noqa: BLE001 - stop on the first failure (no retries against the camera)
        print("ERROR:", cam.mask(f"{type(e).__name__}: {e}"))
        return 1
    result["device"] = {k: tag(dev, k) for k in ("deviceName", "model", "firmwareVersion", "deviceType")}

    try:
        st = cam.isapi("/ISAPI/Streaming/status", http_port=args.http_port)
        sessions = []
        for block in re.findall(r"<StreamingSessionStatus\b.*?</StreamingSessionStatus>", st, re.S):
            sessions.append({"client": tag(block, "ipAddress"), "channel": tag(block, "streamingChannelID")
                             or tag(block, "videoChannel"), "bandwidth_kbit": tag(block, "bandwidth")})
        result["streaming"] = {"total": tag(st, "totalStreamingSessions"), "sessions": sessions}
    except Exception as e:  # noqa: BLE001
        result["streaming"] = {"error": cam.mask(str(e))}

    # Limits are model specific; collect every capability tag that looks like one.
    limits = {}
    for path in ("/ISAPI/System/capabilities", "/ISAPI/Streaming/channels/101/capabilities",
                 "/ISAPI/Streaming/channels/102/capabilities"):
        try:
            xml = cam.isapi(path, http_port=args.http_port)
        except Exception as e:  # noqa: BLE001
            limits[path] = {"error": cam.mask(str(e))}
            continue
        found = {}
        for m in re.finditer(r"<(\w*(?:[Mm]ax|[Ll]imit)\w*)\b([^>]*)>([^<]*)<", xml):
            name, attrs, val = m.groups()
            if re.search(r"(?i)session|connect|link|user|stream|view|bandwidth|channel", name):
                found[name] = val.strip() or attrs.strip()
        limits[path] = found
    result["limits"] = limits

    stamp = now_local().strftime("%Y-%m-%d_%H-%M-%S")
    write_json(OUT / f"sessions_{stamp}.json", result)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
