"""Names the CEO gave in the chat, handed to the drawing code.

``ui.layout`` runs every block inside ``block_title(name)``; the first section heading or KPI label the
block draws (``ui.components.section`` / ``kpi``) takes that name instead of its built-in one. Pages
take their own name through ``page_title`` (set in app.py, read by ``ui.shell.header``).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_BLOCK: ContextVar[str | None] = ContextVar("dashboard_block_title", default=None)
_PAGE: ContextVar[str | None] = ContextVar("dashboard_page_title", default=None)


@contextmanager
def block_title(name: str | None):
    token = _BLOCK.set(name or None)
    try:
        yield
    finally:
        _BLOCK.reset(token)


def take_block_title(default: str) -> str:
    """The block's own name once (later headings of the same block keep theirs)."""
    name = _BLOCK.get()
    if name:
        _BLOCK.set(None)
        return name
    return default


def set_page_title(name: str | None) -> None:
    _PAGE.set(name or None)


def page_title(default: str) -> str:
    return _PAGE.get() or default
