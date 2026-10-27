"""W4-01: CHANGES the camera: sets the sub-stream fps over ISAPI and measures the result.

Run only with the user's OK. The first run saves the original channel settings to
intake/out/isapi_<ch>_original.xml; --restore puts them back.
Usage: python set_fps.py --fps 5 --yes      (then --fps 1 --yes, then --restore --yes)
"""
from __future__ import annotations

import argparse
import re
import sys
import time

from camlib import OUT, SecretError, load_camera, now_local, write_json
from probe import measure_packets, opt, tag


def put(cam, ch: int, xml: str, http_port: int) -> str:
    resp = cam.isapi(f"/ISAPI/Streaming/channels/{ch}", method="PUT", body=xml.encode("utf-8"), http_port=http_port)
    return f"statusCode={tag(resp, 'statusCode')} {tag(resp, 'statusString')} {tag(resp, 'subStatusCode')}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", type=int, default=102)
    ap.add_argument("--fps", type=float)
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--yes", action="store_true", help="confirm that the camera setting may be changed")
    ap.add_argument("--seconds", type=int, default=30, help="measurement after the change")
    ap.add_argument("--http-port", type=int, default=80)
    args = ap.parse_args()
    if not args.yes or (args.fps is None) == (not args.restore):
        print("Give --fps N or --restore, plus --yes (this changes the camera).")
        return 2
    try:
        cam = load_camera()
    except SecretError as e:
        print(f"ERROR: {e}")
        return 2

    ch = args.channel
    original = OUT / f"isapi_{ch}_original.xml"
    current = cam.isapi(f"/ISAPI/Streaming/channels/{ch}", http_port=args.http_port)
    if not original.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        original.write_text(current, encoding="utf-8")

    if args.restore:
        xml = original.read_text(encoding="utf-8")
        target = int(tag(xml, "maxFrameRate")) / 100
    else:
        value = int(round(args.fps * 100))  # Hikvision keeps fps x 100
        cap = cam.isapi(f"/ISAPI/Streaming/channels/{ch}/capabilities", http_port=args.http_port)
        allowed = [int(v) for v in (opt(cap, "maxFrameRate") or "").split(",") if v.strip().isdigit()]
        if allowed and value not in allowed:
            print(f"fps {args.fps} is not offered by the camera; allowed (x100): {allowed}")
            return 1
        xml = re.sub(r"(<maxFrameRate\b[^>]*>)\d+(</maxFrameRate>)", rf"\g<1>{value}\g<2>", current, count=1)
        target = args.fps

    status = put(cam, ch, xml, args.http_port)
    time.sleep(5)
    after = cam.isapi(f"/ISAPI/Streaming/channels/{ch}", http_port=args.http_port)
    measured = measure_packets(cam, ch, args.seconds)
    result = {
        "at": now_local().isoformat(timespec="seconds"), "channel": ch, "target_fps": target,
        "put_status": status, "camera_says_maxFrameRate": tag(after, "maxFrameRate"),
        "GovLength": tag(after, "GovLength"), "measured": measured,
    }
    name = "restore" if args.restore else f"fps{args.fps:g}"
    write_json(OUT / f"set_{ch}_{name}.json", result)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
