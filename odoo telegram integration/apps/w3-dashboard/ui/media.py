"""Pictures and GIFs the bot saved from company-related conversations (``MEDIA_DIR``, read-only).

The bot decides what is saved (app/monitor_media.py: only company-related conversations, a daily
limit) and writes one ``media_files`` row per picture. The group feed is a single HTML block, so a
picture goes into it as a data URI: photos are scaled down to PHOTO_SIDE pixels (JPEG) and cached;
GIFs (Telegram sends them as silent MP4) are embedded as they are, up to MAX_GIF_BYTES. Only paths
that stay inside MEDIA_DIR are read. A picture sits in a <details> element: a click shows it over the
whole window, the next click closes it again (CSS only; st.html runs no scripts).
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import streamlit as st
from PIL import Image, UnidentifiedImageError

from core.text import esc

SAVED = "saved"
PHOTO_SIDE = 720
MAX_GIF_BYTES = 3 * 1024 * 1024
_VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm"}


def _file(media_dir: Path, relative: str | None) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    root = media_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


@st.cache_data(max_entries=600, show_spinner=False)
def _photo_uri(path: str, _mtime: float) -> str | None:
    try:
        with Image.open(path) as image:
            image.thumbnail((PHOTO_SIDE, PHOTO_SIDE))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "JPEG", quality=80, optimize=True)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


@st.cache_data(max_entries=40, show_spinner=False)
def _raw_uri(path: str, _mtime: float, media_type: str) -> str:
    return f"data:{media_type};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def html(media_type: str, status: str | None, relative: str | None, media_dir: Path, label: str = "") -> str:
    """Markup for a saved picture or GIF; '' when there is nothing to show (not saved, file gone)."""
    if status != SAVED:
        return ""
    path = _file(media_dir, relative)
    if path is None:
        return ""
    mtime = path.stat().st_mtime
    suffix = path.suffix.lower()
    if media_type == "animation" and suffix in _VIDEO_TYPES:
        if path.stat().st_size > MAX_GIF_BYTES:
            return ""
        uri = _raw_uri(str(path), mtime, _VIDEO_TYPES[suffix])
        return (f'<div class="db-msg-media"><video src="{uri}" autoplay loop muted playsinline '
                f'title="{esc(label)}"></video></div>')
    if suffix == ".gif" and path.stat().st_size <= MAX_GIF_BYTES:  # a real GIF keeps its animation
        uri = _raw_uri(str(path), mtime, "image/gif")
    else:
        uri = _photo_uri(str(path), mtime)
    if uri is None:
        return ""
    return (f'<details class="db-msg-media"><summary title="{esc(label)}"><img src="{uri}" alt="{esc(label)}">'
            f'</summary></details>')
