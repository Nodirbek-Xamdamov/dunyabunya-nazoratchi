"""Secrets typed on the «AI agentlar» page (an Odoo API key, a Telegram bot token) are stored in
dashboard\\data\\state.db encrypted with Windows DPAPI (no extra package needed).

The machine scope is used on purpose: the dashboard and the Telegram bots of the agents may run under a
scheduled task with an S4U logon, where the user-scope DPAPI key is not available. A copied state.db
cannot be decrypted on another computer. Secrets are never logged, never shown again in full on the
page and never sent to a model."""

from __future__ import annotations

import ctypes
import sys

_ENTROPY = b"dunyabunya-dashboard-connections"
_UI_FORBIDDEN = 0x01
_LOCAL_MACHINE = 0x04


class SecretsUnavailable(RuntimeError):
    """Encryption is not possible here (not Windows) or the stored value cannot be decrypted."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _run(function_name: str, data: bytes) -> bytes:
    if sys.platform != "win32":
        raise SecretsUnavailable("Kalitlarni saqlash faqat Windows'da ishlaydi (DPAPI).")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    source, _keep = _blob(data)
    entropy, _keep_entropy = _blob(_ENTROPY)
    result = _Blob()
    flags = _UI_FORBIDDEN | (_LOCAL_MACHINE if function_name == "CryptProtectData" else 0)
    function = getattr(crypt32, function_name)
    # (data in, description, entropy, reserved, prompt, flags, data out) -- the same order for both calls
    if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, flags, ctypes.byref(result)):
        raise SecretsUnavailable("Kalitni shifrlab/ochib bo'lmadi (boshqa kompyuterda saqlanganmi?).")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def protect(text: str) -> bytes:
    return _run("CryptProtectData", text.encode("utf-8"))


def unprotect(blob: bytes) -> str:
    return _run("CryptUnprotectData", bytes(blob)).decode("utf-8")


def mask(text: str) -> str:
    """'••••1a2b' -- enough to recognise a key without showing it."""
    text = text or ""
    return "••••" + text[-4:] if len(text) > 8 else "••••"
