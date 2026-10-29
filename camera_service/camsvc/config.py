"""Camera connection from the project .env (stdlib only, so tools can use it too).

The login and password live only in D:\\Demos\\Kamera\\.env. Anything that may
contain the RTSP address must go through mask() before it is printed or logged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote

PROJECT_DIR = Path(__file__).resolve().parents[2]
SERVICE_DIR = PROJECT_DIR / "camera_service"
ENV_FILE = PROJECT_DIR / ".env"
CLIPS_DIR = PROJECT_DIR / "test_clips"
LOGS_DIR = SERVICE_DIR / "logs"

CHANNEL_MAIN = 101  # Hikvision main stream
CHANNEL_SUB = 102  # Hikvision sub stream (lighter)

_RTSP_AUTH = re.compile(r"(rtsps?://)[^\s/@]*@", re.IGNORECASE)


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Camera:
    host: str
    port: int
    user: str
    password: str

    def url(self, channel: int) -> str:
        auth = ""
        if self.user or self.password:
            auth = f"{quote(self.user, safe='')}:{quote(self.password, safe='')}@"
        return f"rtsp://{auth}{self.host}:{self.port}/Streaming/Channels/{channel}"

    def masked_url(self, channel: int) -> str:
        return mask(self.url(channel), self)

    def secrets(self) -> list[str]:
        """Strings that must never appear in output (raw and URL-encoded forms)."""
        found = set()
        for value in (self.user, self.password):
            if len(value) >= 3:
                found.update({value, quote(value, safe="")})
        return sorted(found, key=len, reverse=True)


def mask(text: str, camera: Camera | None = None) -> str:
    text = _RTSP_AUTH.sub(r"\1***:***@", text)
    if camera is not None:
        for secret in camera.secrets():
            text = text.replace(secret, "***")
    return text


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """KEY=VALUE lines; '#' starts a comment only at the start of a line (passwords may hold '#')."""
    if not path.exists():
        return {}
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:  # Notepad saved it in the ANSI code page
        text = data.decode("mbcs", errors="replace")
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _parse_rtsp_url(url: str) -> tuple[str, str, str, str]:
    rest = url.split("://", 1)[1] if "://" in url else url
    creds, at, hostpart = rest.rpartition("@")
    user = password = ""
    if at:
        user, _, password = creds.partition(":")
        user, password = unquote(user), unquote(password)
    host, _, port = hostpart.split("/", 1)[0].partition(":")
    return host, port, user, password


def camera_from_env(values: dict[str, str] | None = None) -> Camera:
    v = load_env() if values is None else values
    host = v.get("CAMERA_IP", "").strip()
    port = v.get("CAMERA_PORT", "").strip()
    user = v.get("CAMERA_USER", "").strip()
    password = v.get("CAMERA_PASSWORD", "")
    url = v.get("CAMERA_RTSP_URL", "").strip()
    if not host and url:
        host, url_port, user, password = _parse_rtsp_url(url)
        port = url_port or port
    if not host:
        raise ConfigError(f"{ENV_FILE}: CAMERA_IP (yoki CAMERA_RTSP_URL) to'ldirilmagan")
    try:
        port_number = int(port or 554)
    except ValueError as exc:
        raise ConfigError(f"{ENV_FILE}: CAMERA_PORT raqam bo'lishi kerak") from exc
    return Camera(host=host, port=port_number, user=user, password=password)
