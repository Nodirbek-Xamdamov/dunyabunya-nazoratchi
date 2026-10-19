"""The design agent ("Dizayn agenti"): changes how the dashboard looks -- light/dark, colors, fonts,
corners, text size, chart colors and extra CSS (core/theme.py) -- also from a screenshot of another
site ("shu saytdek qil"). The design is kept in the layout overlay, so «Qaytarish», the history and
«Asl holat» work for it like for any other change. It cannot hide or move anything, and the chat
always keeps its look."""

from __future__ import annotations

from dataclasses import dataclass

from assistant import llm, prompts
from assistant.llm import Tool, ToolError, nullable, obj
from core import state
from core import theme
from core.settings import Settings
from ui import layout
from ui.i18n import t

DEFAULTS = {"mode": "light", "primary_color": "#4F46E5", "background_color": "#FFFFFF", "surface_color": "#F0F2F6",
            "text_color": "#31333F", "font": "Source Sans (Streamlit's own)", "radius": "medium", "font_size": 16,
            "chart_colors": list(theme.LIGHT_CHART_COLORS)}


@dataclass
class DesignResult:
    text: str
    changes: list[str]
    usage: llm.Usage


def _tools(settings: Settings, context: dict, changes: list[str]) -> list[Tool]:
    group_id = context["group_id"]

    def apply(change, source: str = "chat") -> dict:
        summary = state.update_layout(settings, change, group_id=group_id, source=source)
        if summary is None:
            return {"result": "no change -- it was already like that"}
        changes.append(summary)
        return {"result": "done", "summary": summary,
                "note": "Open pages reload themselves within about two seconds and show the new design."}

    def get_design(_args: dict) -> dict:
        tokens = layout.current_overlay().get("theme") or {}
        return {"current_settings": tokens or "none -- the default design", "default_design": DEFAULTS}

    def set_design(args: dict) -> dict:
        tokens = {name: args[name] for name in theme.TOKENS if args.get(name) is not None}
        for name in args.get("reset") or []:
            if name not in theme.TOKENS:
                raise ToolError(f"Unknown setting {name!r} in reset. Settings: {', '.join(theme.TOKENS)}.")
            tokens[name] = None
        if not tokens:
            raise ToolError("Nothing to change: give at least one setting (or reset).")
        return apply(lambda overlay: layout.set_theme(overlay, tokens))

    def reset_design(_args: dict) -> dict:
        return apply(lambda overlay: layout.reset_theme(overlay), source="reset")

    def undo(args: dict) -> dict:
        try:
            steps = max(1, min(10, int(args.get("steps") or 1)))
        except (TypeError, ValueError):
            steps = 1
        summary = state.undo(settings, group_id=group_id, steps=steps)
        if summary is None:
            return {"result": "nothing to undo"}
        changes.append(t("layout.undone", summary=summary))
        return {"result": "done", "reverted": summary}

    color = {"type": "string"}
    settings_schema = {
        "mode": nullable({"type": "string", "enum": list(theme.MODES)}),
        "primary_color": nullable(color, "#RRGGBB accent: buttons, selected items, the chat button"),
        "background_color": nullable(color, "#RRGGBB page background"),
        "surface_color": nullable(color, "#RRGGBB cards, inputs, table headers, pop-ups"),
        "text_color": nullable(color, "#RRGGBB main text"),
        "sidebar_color": nullable(color, "#RRGGBB left panel background"),
        "border_color": nullable(color, "#RRGGBB borders and lines"),
        "link_color": nullable(color, "#RRGGBB links"),
        "font": nullable({"type": "string", "enum": list(theme.FONTS)}),
        "heading_font": nullable({"type": "string", "enum": list(theme.FONTS)}),
        "radius": nullable({"type": "string", "enum": list(theme.RADII)}),
        "font_size": nullable({"type": "integer"}, "Base text size in px, 12-20 (default 16)"),
        "chart_colors": nullable({"type": "array", "items": color}, "2-10 #RRGGBB colors, used by charts in order"),
        "custom_css": nullable({"type": "string"}, "Extra CSS; replaces the previous extra CSS"),
        "reset": nullable({"type": "array", "items": {"type": "string", "enum": list(theme.TOKENS)}},
                          "Settings to put back to the default"),
    }
    return [
        Tool("get_design", "The current design settings (and the extra CSS) and the default design.", obj({}),
             get_design),
        Tool("set_design", "Change design settings; a setting left null stays as it is. All values are checked "
                           "(colors, readable contrast, allowed fonts, safe CSS); an error says what to fix.",
             obj(settings_schema), set_design),
        Tool("reset_design", "Put the whole design back to the default (layout and sections stay).", obj({}),
             reset_design),
        Tool("undo", "Undo the most recent changes (steps = how many earlier CEO requests to undo).",
             obj({"steps": nullable({"type": "integer"}, "Default 1")}), undo),
    ]


def describe_tools(settings: Settings) -> list[Tool]:
    """The tools with a placeholder context, for the «AI agentlar» page (names and descriptions only)."""
    return _tools(settings, {"group_id": "-"}, [])


def run(client, settings: Settings, instruction: str, context: dict, pictures=()) -> DesignResult:
    changes: list[str] = []
    result = llm.run_agent(client, settings, name="design", system=prompts.system("design", settings),
                           messages=[llm.user_message(instruction, pictures)],
                           context=prompts.design_context(context, layout.current_overlay()),
                           tools=_tools(settings, context, changes))
    return DesignResult(result.text, changes, result.usage)
