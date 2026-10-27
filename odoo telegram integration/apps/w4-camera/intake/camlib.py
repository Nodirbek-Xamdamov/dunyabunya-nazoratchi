"""Shared helpers for the W4-01 camera intake scripts (stdlib only).

The RTSP address holds the camera password. It is read from the secret file
(or the CAM1_RTSP environment variable) and never printed or logged: every
text that may contain it goes through Camera.mask().
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]  # apps/w4-camera
ROOT = APP_DIR.parents[1]  # project root
OUT = APP_DIR / "intake" / "out"
SAMPLES = OUT / "samples"
SECRET_FILE = ROOT / "secrets" / "camera1.txt"
FFMPEG = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg.exe"
FFPROBE = shutil.which("ffprobe") or r"C:\ffmpeg\ffprobe.exe"
SOCKET_TIMEOUT_US = "10000000"  # 10 s without data -> ffmpeg/ffprobe gives up


class SecretError(RuntimeError):
    pass


class _OnceDigest(urllib.request.HTTPDigestAuthHandler):
    """Digest auth that sends the credentials once: a second 401 is raised, not retried
    (urllib retries up to 5 times, and Hikvision locks the account after a few failures)."""

    def http_error_401(self, req, fp, code, msg, headers):
        if getattr(req, "_auth_tried", False):
            raise urllib.error.HTTPError(req.full_url, 401, "credentials rejected (not retried)", headers, fp)
        req._auth_tried = True
        return super().http_error_401(req, fp, code, msg, headers)


class Camera:
    def __init__(self, url: str):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "rtsp" or not parts.hostname:
            raise SecretError("camera address must look like rtsp://user:pass@host:554/Streaming/Channels/102")
        self._url = url
        self.host = parts.hostname
        self.rtsp_port = parts.port or 554
        self.user = urllib.parse.unquote(parts.username or "")
        self.password = urllib.parse.unquote(parts.password or "")
        self._raw_password = parts.password or ""
        m = re.search(r"/Channels/(\d+)", parts.path, re.I)
        self.channel = int(m.group(1)) if m else None

    def rtsp(self, channel: int) -> str:
        """Same address with the Hikvision channel replaced (101 main, 102 sub)."""
        if re.search(r"/Channels/\d+", self._url, re.I):
            return re.sub(r"(/Channels/)\d+", rf"\g<1>{channel}", self._url, flags=re.I)
        return self._url.rstrip("/") + f"/Streaming/Channels/{channel}"

    def mask(self, text: str) -> str:
        if not text:
            return text
        text = re.sub(r"rtsp://\S+", "rtsp://***", text)
        for secret in {self.password, self._raw_password}:
            if secret:
                text = text.replace(secret, "***")
        return text

    def isapi(self, path: str, method: str = "GET", body: bytes | None = None,
              http_port: int = 80, timeout: float = 10) -> str:
        """One ISAPI request with digest auth. Credentials are the RTSP ones."""
        base = f"http://{self.host}:{http_port}"
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, base, self.user, self.password)
        opener = urllib.request.build_opener(_OnceDigest(mgr))
        req = urllib.request.Request(base + path, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/xml")
        with opener.open(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def _read_shared(name: str) -> str | None:
    """The project's secret loader (shared/genesis_common, owned by W0); None if it is not there."""
    shared = ROOT / "shared"
    if not (shared / "genesis_common").is_dir():
        return None
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    try:
        from genesis_common import read_secret
    except ImportError:
        return None
    try:
        return read_secret(name)
    except Exception as e:  # noqa: BLE001 - the loader reports only the secret's name
        raise SecretError(str(e)) from None


def load_camera(secret_file: Path | None = None) -> Camera:
    url = os.environ.get("CAM1_RTSP", "").strip()
    if not url and secret_file is None:
        text = _read_shared(SECRET_FILE.name)
        if text is not None:
            url = _first_line(text)
    if not url:
        path = secret_file or SECRET_FILE
        if not path.exists():
            raise SecretError(f"secret file not found: {path} (write the RTSP address there, one line)")
        url = _first_line(path.read_text(encoding="utf-8-sig"))
    if not url:
        raise SecretError("secret file is empty")
    return Camera(url)


def rtsp_input(url: str) -> list[str]:
    return ["-rtsp_transport", "tcp", "-timeout", SOCKET_TIMEOUT_US, "-i", url]


def run(cmd: list[str], cam: Camera, timeout: float) -> subprocess.CompletedProcess:
    """Run ffmpeg/ffprobe; stdout and stderr come back already masked."""
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    p.stdout = cam.mask(p.stdout)
    p.stderr = cam.mask(p.stderr)
    return p


def snapshot(cam: Camera, channel: int, dest: Path, timeout: float = 40) -> tuple[bool, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", *rtsp_input(cam.rtsp(channel)),
           "-frames:v", "1", "-q:v", "2", "-y", str(dest)]
    try:
        p = run(cmd, cam, timeout)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    ok = p.returncode == 0 and dest.exists() and dest.stat().st_size > 0
    return ok, p.stderr.strip()[-300:]


def now_local() -> datetime:
    return datetime.now().astimezone()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
