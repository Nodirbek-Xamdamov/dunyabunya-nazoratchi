"""dunyabunya monitoring dashboard -- entry point.

Start it with ``powershell -ExecutionPolicy Bypass -File dashboard\\run_dashboard.ps1`` (it runs
``streamlit run app.py`` inside dashboard\\.venv). It only reads the monitoring database and never
sends anything to Telegram or Odoo. The chat button in the bottom-right corner (ui.chat) talks to the AI
and changes only the dashboard's own layout, pages and design (stored in dashboard\\data\\state.db).
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
from ui import auth, chat, design, layout, nav, overrides, shell  # noqa: E402
from ui import components as c  # noqa: E402
from ui.i18n import t  # noqa: E402
from views import agents, categories, groups, overview, own, reports, response, search, staff, system, trends  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
for noisy in ("streamlit", "urllib3", "watchdog", "httpx", "httpx2", "httpcore", "httpcore2", "anthropic"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

ASSETS = DASHBOARD_DIR / "ui" / "assets"
st.set_page_config(page_title=t("app.title"), page_icon=str(ASSETS / "icon.svg"), layout="wide",
                   initial_sidebar_state="expanded")
st.logo(str(ASSETS / "logo.svg"), size="large", icon_image=str(ASSETS / "icon.svg"))
settings = load_settings()
c.inject_css()
overlay = layout.current_overlay()  # what the CEO changed from the chat: pages, names, sections, design
design.apply(settings, overlay)
auth.gate(settings)

SPECS = {  # key: (render, title, icon, url path)
    "overview": (overview.render, "page.overview", ":material/dashboard:", "umumiy"),
    "groups": (groups.render, "page.groups", ":material/groups:", "guruhlar"),
    "categories": (categories.render, "page.categories", ":material/category:", "bolimlar"),
    "agents": (agents.render, "page.agents", ":material/smart_toy:", "ai-agentlar"),
    "response": (response.render, "page.response", ":material/timer:", "javob-tezligi"),
    "staff": (staff.render, "page.staff", ":material/badge:", "xodimlar"),
    "trends": (trends.render, "page.trends", ":material/trending_up:", "dinamika"),
    "search": (search.render, "page.search", ":material/search:", "qidiruv"),
    "reports": (reports.render, "page.reports", ":material/folder_open:", "hisobotlar"),
    "system": (system.render, "page.system", ":material/monitor_heart:", "tizim"),
}
SECTIONS = {
    "nav.main": ("overview", "groups", "categories", "agents"),
    "nav.analysis": ("response", "staff", "trends", "search"),
    "nav.system": ("reports", "system"),
}

hidden = set(layout.hidden_nav_pages(overlay))
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
ctx = shell.sidebar(settings)
chat.panel(settings, ctx, current)
navigation.run()
