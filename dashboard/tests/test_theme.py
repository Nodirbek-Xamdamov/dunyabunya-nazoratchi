"""The design (core/theme.py): token checks, readable contrast, safe extra CSS, the [theme] part of
config.toml and writing it only when the design changed."""

from __future__ import annotations

import tomllib

import pytest

from core import theme


def test_tokens_are_checked_and_merged():
    tokens = theme.validate({"mode": "dark", "primary_color": "#22c55e", "font": "Inter", "font_size": "15",
                             "chart_colors": ["#111111", "#eeeeee"]})
    assert tokens == {"mode": "dark", "primary_color": "#22C55E", "font": "Inter", "font_size": 15,
                      "chart_colors": ["#111111", "#EEEEEE"]}
    assert theme.validate({"font": None, "chart_colors": []}, base=tokens) == {
        "mode": "dark", "primary_color": "#22C55E", "font_size": 15}
    for bad, message in (({"nope": 1}, "Unknown design setting"), ({"mode": "blue"}, "light or dark"),
                         ({"primary_color": "red"}, "#1A2B3C"), ({"font": "Comic Sans"}, "must be one of"),
                         ({"radius": "huge"}, "radius"), ({"font_size": 40}, "between 12 and 20"),
                         ({"chart_colors": ["#111111"]}, "2-10"), ({"text_color": "#FFFFFF"}, "hard to read"),
                         ({"surface_color": "#333333"}, "surface_color")):
        with pytest.raises(theme.ThemeError, match=message):
            theme.validate(bad)
    assert theme.contrast("#000000", "#FFFFFF") == pytest.approx(21.0)


@pytest.mark.parametrize("css", [
    "[data-testid=\"stMetric\"] { box-shadow: 0 2px 8px rgba(0,0,0,.1); border-radius: 14px; }",
    "h1 { font-weight: 800; letter-spacing: -0.02em; } .db-card { padding-top: 12px; border-width: 2px; }",
    ".stApp { background: linear-gradient(180deg, #F8FAFC, #EEF2FF); }",
])
def test_css_that_restyles_is_allowed(css):
    assert theme.check_css(css) == css


@pytest.mark.parametrize("css, message", [
    (".stApp { display: none }", "display"), ("h1{visibility:hidden}", "visibility"),
    (".db-card { opacity: 0 }", "opacity"), ("div { position: absolute; }", "position"),
    ("@keyframes x { to { opacity: 0 } }", "opacity"), ("p { max-width: 10px }", "max-width"),
    (".stApp { background: url(https://x.example/a.png) }", "url("), ("@import 'x.css';", "@import"),
    ("</style><script>alert(1)</script>", "'<'"), ("h1 { color: red", "unbalanced"),
    (".st-key-chat_fab { color: red }", "chat"), ("[data-testid=\"stPopoverBody\"] { color: red }", "chat"),
])
def test_css_that_hides_moves_or_loads_things_is_refused(css, message):
    with pytest.raises(theme.ThemeError, match=message.replace("(", "\\(")):
        theme.check_css(css)


def test_theme_toml_is_valid_and_complete():
    assert theme.theme_toml({}) == theme.DEFAULT_THEME_TOML
    tokens = theme.validate({"mode": "dark", "background_color": "#0B1020", "text_color": "#E5E7EB",
                             "sidebar_color": "#111827", "font": "Poppins", "radius": "large", "font_size": 15})
    parsed = tomllib.loads(theme.theme_toml(tokens))["theme"]
    assert parsed["base"] == "dark" and parsed["baseRadius"] == "large" and parsed["baseFontSize"] == 15
    assert parsed["backgroundColor"] == "#0B1020" and parsed["primaryColor"] == "#818CF8"
    assert parsed["font"].startswith("Poppins:https://fonts.googleapis.com/css2?family=Poppins")
    assert parsed["sidebar"] == {"backgroundColor": "#111827"}
    assert parsed["chartCategoricalColors"] == list(theme.DARK_CHART_COLORS)
    config = tomllib.loads(theme.config_text(tokens))
    assert config["server"]["runOnSave"] is True and config["server"]["port"] == 8502


def test_config_is_written_only_when_the_design_changes(tmp_path):
    path = tmp_path / ".streamlit" / "config.toml"
    assert theme.sync_config(path, {}) is True
    assert f"{theme.MARKER} default" in path.read_text(encoding="utf-8")
    assert theme.sync_config(path, {}) is False
    tokens = {"mode": "dark"}
    assert theme.sync_config(path, tokens) is True and theme.sync_config(path, tokens) is False
    assert theme.fingerprint(tokens) in path.read_text(encoding="utf-8")
    assert theme.fingerprint(tokens) == theme.fingerprint(dict(tokens)) != theme.fingerprint({})


def test_extra_css_keeps_the_chat_visible():
    css = theme.css({"primary_color": "#22C55E", "custom_css": "h1 { color: #111111; }"})
    assert css.index("h1 { color: #111111; }") < css.index(".st-key-chat_fab { display: block !important")
    assert "#22C55E" in css and css.rstrip().endswith("}")
    assert theme.css({}).strip() == theme.PROTECT_CHAT_CSS.strip()
    assert theme.changed_names({}, {"mode": "dark", "font": "Inter"}) == ["yorug'/qorong'i", "shrift"]
