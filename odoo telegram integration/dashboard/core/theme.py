"""The dashboard's design (colors, fonts, corners, light/dark, extra CSS) chosen in the chat.

The design is a small set of validated tokens kept in the layout overlay (core.state), so undo, history
and reset work for it like for any other change. It is applied in two ways:

* Streamlit's own theme: ``config.toml`` (THEME_CONFIG_PATH) is rewritten from DEFAULT_CONFIG plus a
  [theme] part built from the tokens. Streamlit watches that file and reloads it; every open page
  reloads itself once (app.py) and then shows the new design everywhere, tables and charts included.
* Extra CSS (``custom_css``) for things the theme does not cover -- checked so that it cannot hide or
  move anything (hiding is done with the layout tools) and followed by rules that always keep the
  chat button visible.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MARKER = "# dashboard theme:"
_LOCK = threading.Lock()

FONTS = {  # name -> Google Fonts family parameter
    "Inter": "Inter:wght@400;500;600;700", "Roboto": "Roboto:wght@400;500;700",
    "Open Sans": "Open+Sans:wght@400;600;700", "Montserrat": "Montserrat:wght@400;500;600;700",
    "Poppins": "Poppins:wght@400;500;600;700", "Nunito": "Nunito:wght@400;600;700",
    "Lato": "Lato:wght@400;700", "Source Sans 3": "Source+Sans+3:wght@400;600;700",
    "IBM Plex Sans": "IBM+Plex+Sans:wght@400;500;600;700", "Rubik": "Rubik:wght@400;500;600;700",
    "Manrope": "Manrope:wght@400;500;600;700", "Work Sans": "Work+Sans:wght@400;500;600;700",
    "DM Sans": "DM+Sans:wght@400;500;700", "Raleway": "Raleway:wght@400;500;600;700",
    "Ubuntu": "Ubuntu:wght@400;500;700", "Noto Sans": "Noto+Sans:wght@400;600;700",
    "PT Sans": "PT+Sans:wght@400;700", "Merriweather": "Merriweather:wght@400;700",
    "Playfair Display": "Playfair+Display:wght@400;600;700", "Lora": "Lora:wght@400;600;700",
}
LIGHT_CHART_COLORS = ("#4F46E5", "#EA580C", "#16A34A", "#DC2626", "#94A3B8", "#0EA5E9", "#A855F7", "#EAB308")
DARK_CHART_COLORS = ("#818CF8", "#FB923C", "#4ADE80", "#F87171", "#94A3B8", "#38BDF8", "#C084FC", "#FACC15")
RADII = {"none": "none", "small": "small", "medium": "0.75rem", "large": "large", "full": "full"}
MODES = ("light", "dark")
COLOR_TOKENS = ("primary_color", "background_color", "surface_color", "text_color", "sidebar_color",
                "border_color", "link_color")
TOKENS = ("mode", *COLOR_TOKENS, "font", "heading_font", "radius", "font_size", "chart_colors", "custom_css")
MAX_CSS = 20_000
# CSS may restyle (colors, fonts, sizes, spacing, borders, shadows, backgrounds) but never hide or move
_FORBIDDEN_CSS = ("<", "@import", "url(", "expression(", "javascript:", "behavior:", "-moz-binding", "@charset")
_CHAT_TARGETS = ("chat", "popover", "db-msg", "db-change", "db-tl", "stdialog")  # the chat keeps its own look
_FORBIDDEN_PROPERTIES = re.compile(
    r"(^|[;{\s])(display|visibility|opacity|content-visibility|clip|clip-path|transform|position|z-index|"
    r"pointer-events|inset|top|left|right|bottom|width|height|max-height|max-width|overflow)\s*:", re.IGNORECASE)

DEFAULT_THEME_TOML = """[theme]
baseRadius = "0.75rem"

[theme.light]
primaryColor = "#4F46E5"
chartCategoricalColors = ["#4F46E5", "#EA580C", "#16A34A", "#DC2626", "#94A3B8", "#0EA5E9", "#A855F7", "#EAB308"]

[theme.dark]
primaryColor = "#818CF8"
chartCategoricalColors = ["#818CF8", "#FB923C", "#4ADE80", "#F87171", "#94A3B8", "#38BDF8", "#C084FC", "#FACC15"]
"""

DEFAULT_CONFIG = """# Streamlit settings of the dashboard (run_dashboard.ps1 starts Streamlit from the dashboard folder,
# so this file is used). Address/port can be overridden from dashboard\\.env (DASHBOARD_HOST/PORT).
# The [theme] part below is written by the dashboard (core/theme.py) from the design chosen in the chat;
# edit the rest by hand if needed. runOnSave makes open pages rerun (and pick up a new design) when this
# file changes; with fileWatcherType = "none" nothing else is watched.

[server]
headless = true
address = "127.0.0.1"
port = 8502
runOnSave = true
fileWatcherType = "none"

[browser]
gatherUsageStats = false

[client]
toolbarMode = "minimal"
showErrorDetails = "none"

[logger]
level = "warning"

"""

PROTECT_CHAT_CSS = """
/* the chat button always stays (added after any custom CSS) */
.st-key-chat_fab { display: block !important; visibility: visible !important; opacity: 1 !important;
  position: fixed !important; right: 28px !important; bottom: 28px !important; z-index: 999990 !important;
  transform: none !important; pointer-events: auto !important; }
.st-key-chat_fab [data-testid="stPopoverButton"] { display: flex !important; visibility: visible !important;
  opacity: 1 !important; width: 60px !important; height: 60px !important; }
/* general rules of the custom CSS (button { ... }) do not restyle the chat panel's own small buttons */
[data-testid="stPopoverBody"]:has(.st-key-chat-box) [data-testid="stBaseButton-tertiary"],
[data-testid="stDialog"] [data-testid="stBaseButton-tertiary"] { background: transparent !important;
  color: inherit !important; border: none !important; box-shadow: none !important; border-radius: 0.5rem !important; }
"""


class ThemeError(ValueError):
    """An invalid design value; the message (read by the AI agent) says why."""


def _hex(value, name: str) -> str:
    text = str(value or "").strip()
    if not HEX_RE.match(text):
        raise ThemeError(f"{name} must be a color like #1A2B3C, not {value!r}.")
    return text.upper()


def _luminance(color: str) -> float:
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def check_css(css: str) -> str:
    css = str(css or "").strip()
    if len(css) > MAX_CSS:
        raise ThemeError(f"custom_css is longer than {MAX_CSS} characters.")
    lowered = css.lower()
    for bad in _FORBIDDEN_CSS:
        if bad in lowered:
            raise ThemeError(f"custom_css may not contain {bad!r}.")
    if any(target in lowered for target in _CHAT_TARGETS):
        raise ThemeError("custom_css may not style the chat (its button, panel and history stay as they are).")
    found = _FORBIDDEN_PROPERTIES.search(css)
    if found:
        raise ThemeError(f"custom_css may not set {found.group(2)!r}: CSS only restyles (colors, fonts, sizes of "
                         f"text, spacing, borders, shadows, backgrounds); sections are hidden or moved with the "
                         f"layout tools.")
    if css.count("{") != css.count("}"):
        raise ThemeError("custom_css has unbalanced braces.")
    return css


def validate(tokens: dict, base: dict | None = None) -> dict:
    """``base`` updated with ``tokens`` (a value of None removes that token), checked as a whole."""
    result = dict(base or {})
    for name, value in (tokens or {}).items():
        if name not in TOKENS:
            raise ThemeError(f"Unknown design setting {name!r}. Settings: {', '.join(TOKENS)}.")
        if value is None or value == "" or value == []:
            result.pop(name, None)
            continue
        if name == "mode":
            if value not in MODES:
                raise ThemeError("mode must be light or dark.")
            result[name] = value
        elif name in COLOR_TOKENS:
            result[name] = _hex(value, name)
        elif name in ("font", "heading_font"):
            if value not in FONTS:
                raise ThemeError(f"{name} must be one of: {', '.join(FONTS)}.")
            result[name] = value
        elif name == "radius":
            if value not in RADII:
                raise ThemeError(f"radius must be one of: {', '.join(RADII)}.")
            result[name] = value
        elif name == "font_size":
            try:
                size = int(value)
            except (TypeError, ValueError) as exc:
                raise ThemeError("font_size must be a whole number of pixels (12-20).") from exc
            if not 12 <= size <= 20:
                raise ThemeError("font_size must be between 12 and 20 pixels.")
            result[name] = size
        elif name == "chart_colors":
            if not isinstance(value, list) or not 2 <= len(value) <= 10:
                raise ThemeError("chart_colors must be a list of 2-10 colors.")
            result[name] = [_hex(color, "chart_colors item") for color in value]
        elif name == "custom_css":
            result[name] = check_css(value)
    background = result.get("background_color") or ("#0E1117" if result.get("mode") == "dark" else "#FFFFFF")
    text = result.get("text_color") or ("#FAFAFA" if result.get("mode") == "dark" else "#31333F")
    if contrast(background, text) < 4.0:
        raise ThemeError(f"text_color {text} on background_color {background} is hard to read (contrast "
                         f"{contrast(background, text):.1f}, needs at least 4). Pick colors with more contrast.")
    if "surface_color" in result and contrast(result["surface_color"], text) < 3.0:
        raise ThemeError(f"text_color {text} on surface_color {result['surface_color']} is hard to read.")
    return result


def fingerprint(tokens: dict | None) -> str:
    if not tokens:
        return "default"
    return hashlib.sha1(json.dumps(tokens, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _font_value(name: str) -> str:
    return f"{name}:https://fonts.googleapis.com/css2?family={FONTS[name]}&display=swap, sans-serif"


def theme_toml(tokens: dict | None) -> str:
    """The [theme] part of config.toml for these tokens (the built-in design when there are none)."""
    if not tokens:
        return DEFAULT_THEME_TOML
    mode = tokens.get("mode", "light")
    lines = ["[theme]", f'base = "{mode}"', f'baseRadius = "{RADII[tokens.get("radius", "medium")]}"']
    names = {"primary_color": "primaryColor", "background_color": "backgroundColor",
             "surface_color": "secondaryBackgroundColor", "text_color": "textColor", "border_color": "borderColor",
             "link_color": "linkColor"}
    for token, option in names.items():
        if token in tokens:
            lines.append(f'{option} = "{tokens[token]}"')
    if "primary_color" not in tokens:
        lines.append(f'primaryColor = "{"#818CF8" if mode == "dark" else "#4F46E5"}"')
    if "font" in tokens:
        lines.append(f'font = "{_font_value(tokens["font"])}"')
    if "heading_font" in tokens:
        lines.append(f'headingFont = "{_font_value(tokens["heading_font"])}"')
    if "font_size" in tokens:
        lines.append(f"baseFontSize = {tokens['font_size']}")
    colors = tokens.get("chart_colors") or (DARK_CHART_COLORS if mode == "dark" else LIGHT_CHART_COLORS)
    lines.append("chartCategoricalColors = [" + ", ".join(f'"{color}"' for color in colors) + "]")
    if "sidebar_color" in tokens:
        lines += ["", "[theme.sidebar]", f'backgroundColor = "{tokens["sidebar_color"]}"']
    return "\n".join(lines) + "\n"


def config_text(tokens: dict | None) -> str:
    return DEFAULT_CONFIG + f"{MARKER} {fingerprint(tokens)}\n" + theme_toml(tokens)


def sync_config(path: Path, tokens: dict | None) -> bool:
    """Writes config.toml when it does not carry this design yet. Returns True when it was written."""
    wanted = f"{MARKER} {fingerprint(tokens)}"
    with _LOCK:
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if wanted in current.splitlines():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(config_text(tokens), encoding="utf-8")
        temporary.replace(path)
        return True


def css(tokens: dict | None) -> str:
    """Extra CSS for this design: the chat button in the primary color, the custom CSS, then the rules
    that keep the chat button visible whatever the custom CSS says."""
    tokens = tokens or {}
    parts = []
    primary = tokens.get("primary_color")
    if primary:
        parts.append(f'.st-key-chat_fab [data-testid="stPopoverButton"] {{ background: {primary}; }}')
    if tokens.get("custom_css"):
        parts.append(tokens["custom_css"])
    parts.append(PROTECT_CHAT_CSS)
    return "\n".join(parts)


TOKEN_NAMES = {  # Uzbek names for the change summaries
    "mode": "yorug'/qorong'i", "primary_color": "asosiy rang", "background_color": "fon rangi",
    "surface_color": "kartalar rangi", "text_color": "matn rangi", "sidebar_color": "chap panel rangi",
    "border_color": "chegara rangi", "link_color": "havola rangi", "font": "shrift", "heading_font": "sarlavha shrifti",
    "radius": "burchaklar", "font_size": "matn o'lchami", "chart_colors": "grafik ranglari", "custom_css": "qo'shimcha uslub",
}


def changed_names(before: dict | None, after: dict | None) -> list[str]:
    before, after = before or {}, after or {}
    return [TOKEN_NAMES[name] for name in TOKENS if before.get(name) != after.get(name)]
