"""Pictures attached to chat messages (usually screenshots of the dashboard).

Every picture is checked with Pillow, scaled down to at most MAX_SIDE pixels on its long side and
stored as PNG (or JPEG when that is much smaller) in ``<STATE_DB_PATH folder>/chat_files/`` under a
random name. Only names matching ``NAME_RE`` are ever read back, so a stored name can never point
outside that folder.
"""

from __future__ import annotations

import base64
import io
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from core.settings import Settings

MAX_FILES = 3
MAX_BYTES = 10 * 1024 * 1024  # per uploaded file, before scaling
MAX_SIDE = 1600
MAX_PIXELS = 40_000_000  # refuses decompression bombs
FILE_TYPES = ("png", "jpg", "jpeg", "webp")
NAME_RE = re.compile(r"^[0-9a-f]{32}\.(png|jpg)$")
_MEDIA = {"png": "image/png", "jpg": "image/jpeg"}


class ImageError(ValueError):
    """The file is not a usable picture; the message is for the CEO."""


@dataclass(frozen=True)
class Picture:
    name: str  # stored file name (NAME_RE)
    media_type: str
    data: bytes

    @property
    def base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


def folder(settings: Settings) -> Path:
    return settings.state_db_path.parent / "chat_files"


def normalize(raw: bytes) -> tuple[bytes, str]:
    """Checked, scaled picture as (bytes, extension)."""
    if not raw:
        raise ImageError("bo'sh fayl")
    if len(raw) > MAX_BYTES:
        raise ImageError(f"fayl {MAX_BYTES // 1024 // 1024} MB dan katta")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
        image = Image.open(io.BytesIO(raw))
        if image.width * image.height > MAX_PIXELS:
            raise ImageError("rasm juda katta")
        image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ImageError("rasm o'qilmadi") from exc
    image = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    png = io.BytesIO()
    image.save(png, format="PNG", optimize=True)
    flat = image.convert("RGB")
    jpeg = io.BytesIO()
    flat.save(jpeg, format="JPEG", quality=85, optimize=True)
    if png.tell() <= 1.5 * jpeg.tell() or png.tell() < 400_000:  # screenshots stay sharp as PNG
        return png.getvalue(), "png"
    return jpeg.getvalue(), "jpg"


def store(settings: Settings, raw: bytes) -> Picture:
    data, extension = normalize(raw)
    name = f"{uuid.uuid4().hex}.{extension}"
    target = folder(settings)
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_bytes(data)
    return Picture(name, _MEDIA[extension], data)


def load(settings: Settings, name: str) -> Picture | None:
    """A stored picture by its name, or None (unknown, missing or not a stored-picture name)."""
    match = NAME_RE.match(str(name or ""))
    if not match:
        return None
    path = folder(settings) / name
    try:
        return Picture(name, _MEDIA[match.group(1)], path.read_bytes())
    except OSError:
        return None
