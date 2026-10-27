"""W3 dashboard -- entry point (copy of the prototype dashboard, D-15).

Start it with run_dashboard.ps1 in this folder (it runs ``streamlit run app.py`` inside this folder's .venv).
It only reads the Genesis database through the W0 views (core.db) and never sends anything anywhere.
The AI chat, the agents (D-16) and the old camera pages (D-17) stay in the prototype and are not part of
this app; the camera page here is the new D-11/D-31 one (W3-04, views/camera.py).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import streamlit as st  # noqa: E402

from core.settings import load_settings  # noqa: E402
from ui import auth, layout, nav, overrides, shell  # noqa: E402
from ui import components as c  # noqa: E402
from ui.i18n import t  # noqa: E402
from views import camera, categories, groups, overview, own, reports, response, search, staff, system, trends  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
for noisy in ("streamlit", "urllib3", "watchdog", "httpx", "httpx2", "httpcore", "httpcore2", "anthropic"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

ASSETS = DASHBOARD_DIR / "ui" / "assets"
st.set_page_config(page_title=t("app.title"), page_icon=str(ASSETS / "icon.svg"), layout="wide",
                   initial_sidebar_state="expanded")
st.logo(str(ASSETS / "logo.svg"), size="large", icon_image=str(ASSETS / "icon.svg"))
settings = load_settings()
c.inject_css()
overlay = layout.current_overlay()  # layout changes kept in data/state.db (pages, names, sections)
auth.gate(settings)

SPECS = {  # key: (render, title, icon, url path)
    "overview": (overview.render, "page.overview", ":material/dashboard:", "umumiy"),
    "groups": (groups.render, "page.groups", ":material/groups:", "guruhlar"),
    "categories": (categories.render, "page.categories", ":material/category:", "bolimlar"),
    "camera": (camera.render, "page.camera", ":material/videocam:", "kamera"),
    "response": (response.render, "page.response", ":material/timer:", "javob-tezligi"),
    "staff": (staff.render, "page.staff", ":material/badge:", "xodimlar"),
    "trends": (trends.render, "page.trends", ":material/trending_up:", "dinamika"),
    "search": (search.render, "page.search", ":material/search:", "qidiruv"),
    "reports": (reports.render, "page.reports", ":material/folder_open:", "hisobotlar"),
    "system": (system.render, "page.system", ":material/monitor_heart:", "tizim"),
}
SECTIONS = {
    "nav.main": ("overview", "groups", "categories", "camera"),
    "nav.analysis": ("response", "staff", "trends", "search"),
    "nav.system": ("reports", "system"),
}

hidden = set(layout.hidden_nav_pages(overlay)) | {key for key in SPECS if not auth.page_allowed(key)}  # W3-05
visible = [key for key in SPECS if key not in hidden]
own_pages = layout.custom_pages(overlay)
first = visible[0] if visible else own_pages[0].id
PAGES = {key: st.Page(SPECS[key][0], title=layout.nav_title(key, overlay), icon=SPECS[key][2], url_path=SPECS[key][3],
                      default=key == first) for key in visible}
for item in own_pages:
    PAGES[item.id] = st.Page(own.make(item.id), title=item.title, icon=f":material/{item.icon}:", url_path=item.id,
                             default=item.id == first)
nav.set_pages(PAGES)

sections = {t(section): [PAGES[key] for key in keys if key in PAGES] for section, keys in SECTIONS.items()}
sections[t("nav.custom")] = [PAGES[item.id] for item in own_pages]
navigation = st.navigation({name: pages for name, pages in sections.items() if pages})
current = next((key for key, page in PAGES.items() if page is navigation), None) or next(
    (key for key, page in PAGES.items() if page.url_path == navigation.url_path), first)
overrides.set_page_title(layout.page_title(current, overlay, default="") if current in SPECS else None)
shell.sidebar(settings)
navigation.run()
