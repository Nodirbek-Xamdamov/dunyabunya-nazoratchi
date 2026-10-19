"""Page registry: app.py stores the st.Page objects of the visible pages here so pages can link to each
other (a page hidden from the navigation has no entry, so links to it are simply not shown)."""

from __future__ import annotations

PAGES: dict = {}


def set_pages(pages: dict) -> None:
    global PAGES
    PAGES = dict(pages)  # replaced in one step: other sessions never see a half-filled dict


def page(name: str):
    return PAGES.get(name)
