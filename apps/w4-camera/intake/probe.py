"""W4-01: read camera stream parameters (codec, size, fps, GOP, bitrate) and ISAPI settings.

Read-only: nothing on the camera is changed.
Usage: python probe.py [--channels 102] [--isapi-channels 102 101] [--seconds 20] [--http-port 80]
Result: intake/out/probe.json (no secrets inside).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys

from camlib import FFPROBE, OUT, SecretError, load_camera, now_local, rtsp_input, run, write_json


def stream_info(cam, channel: int) -> dict:
    cmd = [FFPROBE, "-v", "error", *rtsp_input(cam.rtsp(channel)), "-select_streams", "v:0",
           "-show_entries", "stream=codec_name,profile,width,height,pix_fmt,r_frame_rate,avg_frame_rate",
           "-of", "json"]
    p = run(cmd, cam, timeout=40)
    if p.returncode != 0:
        return {"error": p.stderr.strip()[-300:] or f"ffprobe exit {p.returncode}"}
    streams = json.loads(p.stdout or "{}").get("streams") or [{}]
    return streams[0]


def measure_packets(cam, channel: int, seconds: int) -> dict:
    """Real fps / GOP / bitrate from packet timestamps (camera clock), no decoding."""
    cmd = [FFPROBE, "-v", "error", *rtsp_input(cam.rtsp(channel)), "-select_streams", "v:0",
           "-read_intervals", f"%+{seconds}", "-show_entries", "packet=pts_time,size,flags", "-of", "json"]
    p = run(cmd, cam, timeout=seconds + 40)
    if p.returncode != 0:
        return {"error": p.stderr.strip()[-300:] or f"ffprobe exit {p.returncode}"}
    pkts = [x for x in json.loads(p.stdout or "{}").get("packets", []) if x.get("pts_time") not in (None, "N/A")]
    if len(pkts) < 3:
        return {"error": f"only {len(pkts)} packets"}
    t = [float(x["pts_time"]) for x in pkts]
    dur = t[-1] - t[0]
    gaps = [b - a for a, b in zip(t, t[1:])]
    keys = [i for i, x in enumerate(pkts) if "K" in x.get("flags", "")]
    gop = [b - a for a, b in zip(keys, keys[1:])]
    return {
        "seconds": round(dur, 2),
        "packets": len(pkts),
        "fps": round((len(pkts) - 1) / dur, 2) if dur > 0 else None,
        "gap_median_ms": round(statistics.median(gaps) * 1000, 1),
        "gap_max_ms": round(max(gaps) * 1000, 1),
        "keyframes": len(keys),
        "gop_frames": statistics.median(gop) if gop else None,
        "kbit_s": round(sum(int(x["size"]) for x in pkts) * 8 / dur / 1000, 1) if dur > 0 else None,
    }


def tag(xml: str, name: str) -> str | None:
    m = re.search(rf"<{name}\b[^>]*>([^<]*)</{name}>", xml)
    return m.group(1).strip() if m else None


def opt(xml: str, name: str) -> str | None:
    m = re.search(rf"<{name}\b[^>]*\bopt=\"([^\"]*)\"", xml)
    return m.group(1) if m else None


def isapi_info(cam, channels: list[int], http_port: int) -> dict:
    info: dict = {}
    try:
        dev = cam.isapi("/ISAPI/System/deviceInfo", http_port=http_port)
    except Exception as e:  # noqa: BLE001 - report and continue with RTSP-only data
        return {"error": cam.mask(f"{type(e).__name__}: {e}")}
    info["device"] = {k: tag(dev, k) for k in ("deviceName", "model", "firmwareVersion", "firmwareReleasedDate")}
    try:
        tm = cam.isapi("/ISAPI/System/time", http_port=http_port)
        info["time"] = {"timeMode": tag(tm, "timeMode"), "localTime": tag(tm, "localTime"),
                        "timeZone": tag(tm, "timeZone"), "server_time": now_local().isoformat(timespec="seconds")}
    except Exception as e:  # noqa: BLE001
        info["time"] = {"error": cam.mask(str(e))}
    for ch in channels:
        entry: dict = {}
        try:
            xml = cam.isapi(f"/ISAPI/Streaming/channels/{ch}", http_port=http_port)
            entry["current"] = {k: tag(xml, k) for k in (
                "videoCodecType", "videoResolutionWidth", "videoResolutionHeight", "maxFrameRate",
                "GovLength", "videoQualityControlType", "constantBitRate", "vbrUpperCap", "SmartCodec")}
        except Exception as e:  # noqa: BLE001
            entry["current"] = {"error": cam.mask(str(e))}
        try:
            cap = cam.isapi(f"/ISAPI/Streaming/channels/{ch}/capabilities", http_port=http_port)
            entry["allowed"] = {k: opt(cap, k) for k in (
                "videoCodecType", "videoResolutionWidth", "videoResolutionHeight", "maxFrameRate", "GovLength")}
        except Exception as e:  # noqa: BLE001
            entry["allowed"] = {"error": cam.mask(str(e))}
        info[f"channel_{ch}"] = entry
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", type=int, nargs="+", default=[102], help="RTSP streams to open")
    ap.add_argument("--isapi-channels", type=int, nargs="+", default=[102, 101], help="settings read over ISAPI only")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--http-port", type=int, default=80)
    args = ap.parse_args()
    try:
        cam = load_camera()
    except SecretError as e:
        print(f"ERROR: {e}")
        return 2

    result = {"probed_at": now_local().isoformat(timespec="seconds"), "host": cam.host, "streams": {}}
    first = True
    for ch in args.channels:
        try:
            s = {"info": stream_info(cam, ch)}
        except subprocess.TimeoutExpired:
            s = {"info": {"error": "timeout"}}
        if first and "error" in s["info"]:
            # Stop on the first failure: a wrong password must not be retried (Hikvision lockout).
            result["streams"][ch] = s
            write_json(OUT / "probe.json", result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
        first = False
        try:
            s["measured"] = measure_packets(cam, ch, args.seconds)
        except subprocess.TimeoutExpired:
            s["measured"] = {"error": "timeout"}
        result["streams"][ch] = s
    result["isapi"] = isapi_info(cam, args.isapi_channels, args.http_port)
    write_json(OUT / "probe.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
