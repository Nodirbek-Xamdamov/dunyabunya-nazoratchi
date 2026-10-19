"""Own pages the CEO creates in the chat: a name, an icon and the blocks added there (custom sections
from ui/custom.py and portable blocks of the other pages)."""

from __future__ import annotations

import streamlit as st

from ui import layout, shell
from ui.i18n import t
from views.common import PageData


def make(page_id: str):
    """The render function of one own page (st.navigation needs one callable per page)."""

    def render() -> None:
        overlay = layout.current_overlay()
        try:
            page = layout.get_page(page_id, overlay)
        except layout.LayoutError:  # deleted in another tab a moment ago
            st.info(t("own.gone"))
            return
        ctx = shell.header(page.title)
        if all(hidden for _place, hidden in layout.arranged(page_id, overlay)):
            st.info(t("own.empty"))
            return
        layout.render_page(page, PageData(ctx), overlay)

    render.__name__ = render.__qualname__ = "own_" + page_id.replace("-", "_")
    return shell.guard(render)
