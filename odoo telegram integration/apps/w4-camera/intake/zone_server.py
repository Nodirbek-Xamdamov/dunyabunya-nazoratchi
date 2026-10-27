"""W4-01: local door-zone tool. Python stdlib only, listens on 127.0.0.1:8765.

- GET  /                 zone_tool.html
- POST /api/capture      ONE frame from channel 102 -> intake/out/live/YYYYMMDD-HHMMSS.jpg (>= 5 s apart)
- GET  /api/frames       captured frame names, newest first
- GET  /live/<name>.jpg  one captured frame
- GET  /api/config       the saved zone_config.json (or {})
- POST /api/save         polygon -> intake/zone_config.json (the previous one is kept in out/zone_config_history/)

The RTSP address is read through the project's secret loader and never goes into a page,
a response or the log. The log (intake/out/zone_tool.log) holds status lines only.
Start: run_zone_tool.bat (double click) or `python zone_server.py [--no-browser]`.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from camlib import OUT, SecretError, load_camera, now_local, snapshot

HERE = Path(__file__).resolve().parent
PAGE = HERE / "zone_tool.html"
LIVE = OUT / "live"
LOG = OUT / "zone_tool.log"
HISTORY = OUT / "zone_config_history"
HOST, PORT = "127.0.0.1", 8765
CHANNEL = 102
MIN_GAP_S = 5.0
FRAME_RE = re.compile(r"^\d{8}-\d{6}\.jpg$")
MAX_BODY = 64 * 1024


def log(msg: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


class Capturer:
    """One capture at a time, at least MIN_GAP_S between the end of one and the start of the next."""

    def __init__(self):
        self.lock = threading.Lock()
        self.last_end = 0.0
        self.cam = None

    def capture(self) -> tuple[int, dict]:
        if not self.lock.acquire(blocking=False):
            return HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": "Kadr olinmoqda, kuting", "wait_s": MIN_GAP_S}
        try:
            wait = MIN_GAP_S - (time.monotonic() - self.last_end)
            if wait > 0:
                return HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": f"{wait:.0f} s kuting", "wait_s": round(wait, 1)}
            if self.cam is None:
                try:
                    self.cam = load_camera()
                except SecretError:
                    log("kadr xato: kamera manzili topilmadi")
                    return HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Kamera manzili topilmadi (secrets)"}
            name = f"{now_local():%Y%m%d-%H%M%S}.jpg"
            try:
                ok, _err = snapshot(self.cam, CHANNEL, LIVE / name)  # ffmpeg text is not passed on
            finally:
                self.last_end = time.monotonic()  # only a real camera attempt restarts the gap
            if not ok:
                log("kadr xato: ffmpeg kadr bermadi")
                return HTTPStatus.BAD_GATEWAY, {"ok": False, "error": "Kameradan kadr olinmadi"}
            log(f"kadr olindi {name}")
            return HTTPStatus.OK, {"ok": True, "file": name}
        finally:
            self.lock.release()


def frames() -> list[str]:
    if not LIVE.is_dir():
        return []
    return sorted((p.name for p in LIVE.iterdir() if FRAME_RE.match(p.name)), reverse=True)


def validate_zone(body: dict, config_path: Path) -> tuple[dict | None, str]:
    frame = body.get("frame")
    if not isinstance(frame, str) or not FRAME_RE.match(frame) or not (LIVE / frame).is_file():
        return None, "Kadr tanlanmagan"
    image = body.get("image")
    if not (isinstance(image, list) and len(image) == 2 and all(isinstance(v, int) and v > 0 for v in image)):
        return None, "Rasm o'lchami noto'g'ri"
    poly = body.get("polygon")
    if not (isinstance(poly, list) and len(poly) >= 4):
        return None, "Zona kamida 4 nuqta bo'lishi kerak"
    clean = []
    for p in poly:
        if not (isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in p)):
            return None, "Koordinatalar 0..1 oralig'ida bo'lishi kerak"
        clean.append([round(float(p[0]), 4), round(float(p[1]), 4)])
    version = 1
    if config_path.is_file():
        try:
            version = int(json.loads(config_path.read_text(encoding="utf-8")).get("version", 0)) + 1
        except (ValueError, json.JSONDecodeError):
            version = 1
    return {"version": version, "camera": "camera1", "channel": CHANNEL, "frame": frame, "image": image,
            "polygon": clean, "saved_at": now_local().isoformat(timespec="seconds")}, ""


def make_handler(capturer: Capturer, config_path: Path, history: Path):
    allowed_hosts = {f"{HOST}:{PORT}", f"localhost:{PORT}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "w4-zone-tool"
        sys_version = ""

        def log_message(self, fmt, *args):  # no request log; status lines go to LOG
            pass

        def _send(self, status: int, body: bytes, ctype: str, cache: bool = False):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self' 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: dict):
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            if self.headers.get("Host") not in allowed_hosts:
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "faqat 127.0.0.1"})
                return False
            return True

        def do_GET(self):
            if not self._host_ok():
                return
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/frames":
                self._json(HTTPStatus.OK, {"frames": frames()})
            elif path == "/api/config":
                data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
                self._json(HTTPStatus.OK, data)
            elif path.startswith("/live/") and FRAME_RE.match(path[6:]) and (LIVE / path[6:]).is_file():
                self._send(HTTPStatus.OK, (LIVE / path[6:]).read_bytes(), "image/jpeg", cache=True)
            else:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "topilmadi"})

        def do_POST(self):
            if not self._host_ok():
                return
            origin = self.headers.get("Origin")
            if origin and origin.split("//", 1)[-1] not in allowed_hosts:
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "boshqa sahifadan so'rov"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "juda katta"})
                return
            raw = self.rfile.read(length) if length else b""
            if self.path == "/api/capture":
                status, data = capturer.capture()
                self._json(status, data)
            elif self.path == "/api/save":
                if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                    self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "error": "JSON kerak"})
                    return
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "JSON buzilgan"})
                    return
                cfg, err = validate_zone(body if isinstance(body, dict) else {}, config_path)
                if cfg is None:
                    self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": err})
                    return
                try:
                    if config_path.is_file():
                        history.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(config_path, history / f"zone_config_v{cfg['version'] - 1}.json")
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = config_path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
                    tmp.replace(config_path)
                except OSError as e:
                    log(f"saqlash xato: {type(e).__name__}")
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Faylga yozib bo'lmadi"})
                    return
                log(f"zona saqlandi v{cfg['version']} ({len(cfg['polygon'])} nuqta, kadr {cfg['frame']})")
                self._json(HTTPStatus.OK, {"ok": True, "version": cfg["version"], "saved_at": cfg["saved_at"]})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "topilmadi"})

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--config", default=str(HERE / "zone_config.json"), help="where Saqlash writes")
    args = ap.parse_args()
    LIVE.mkdir(parents=True, exist_ok=True)
    config = Path(args.config).resolve()
    history = HISTORY if config == (HERE / "zone_config.json").resolve() else config.parent / "zone_config_history"
    try:
        server = ThreadingHTTPServer((HOST, PORT), make_handler(Capturer(), config, history))
    except OSError:
        print(f"Port {PORT} band: vosita allaqachon ochiq bo'lishi mumkin. Brauzerda http://{HOST}:{PORT}/ ni oching.")
        return 1
    url = f"http://{HOST}:{PORT}/"
    log("server ishga tushdi")
    print(f"Eshik zonasi vositasi: {url}  (to'xtatish: shu oynada Ctrl+C yoki oynani yoping)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log("server to'xtadi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
