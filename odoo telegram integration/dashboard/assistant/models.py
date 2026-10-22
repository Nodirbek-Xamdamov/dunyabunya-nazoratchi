"""The models an agent can use, and the settings one agent runs with.

A model is usable when its provider's key is in dashboard\\.env. The OpenAI ids below were checked
against the account's model list on 2026-10-20; the Claude ids are Anthropic's current models and
work once ANTHROPIC_API_KEY is set."""

from __future__ import annotations

from dataclasses import dataclass, replace

from core.settings import DEFAULT_MODELS, Settings


@dataclass(frozen=True)
class Model:
    id: str
    provider: str  # openai | anthropic
    tier: int  # 1 cheapest/fastest ... 4 strongest
    label: str  # for the page (Uzbek)
    use: str  # for the agent designer (English): what it is good for


MODELS = (
    Model("gpt-5.4-nano", "openai", 1, "eng tez va eng arzon",
          "very simple lookups and short answers from one tool call"),
    Model("gpt-5.4-mini", "openai", 2, "tez va arzon (standart)",
          "most agents: questions over data, summaries, a few tool calls; the default choice"),
    Model("gpt-5.4", "openai", 3, "kuchli: murakkab tahlil",
          "multi-step analysis that combines several sources, careful reasoning, long instructions"),
    Model("gpt-5.5", "openai", 4, "eng kuchli (OpenAI), qimmatroq",
          "the hardest analysis and planning; use only when the task really needs it"),
    Model("claude-haiku-5-5", "anthropic", 2, "tez va arzon (Claude)",
          "most agents: questions over data, summaries, a few tool calls"),
    Model("claude-sonnet-5-5", "anthropic", 3, "kuchli: murakkab tahlil (Claude)",
          "multi-step analysis that combines several sources"),
    Model("claude-opus-5-5", "anthropic", 4, "eng kuchli (Claude), qimmatroq",
          "the hardest analysis and planning"),
)
BY_ID = {model.id: model for model in MODELS}
BUILDER_DEFAULT = {"openai": "gpt-5.4", "anthropic": "claude-opus-5-5"}


def provider_of(model_id: str) -> str:
    return "anthropic" if model_id.startswith("claude") else "openai"


def has_key(settings: Settings, provider: str) -> bool:
    chat = settings.chat
    return bool(chat.anthropic_api_key if provider == "anthropic" else chat.openai_api_key)


def available(settings: Settings) -> list[Model]:
    """Models whose provider key is set, cheapest first (the default model is always listed)."""
    models = [model for model in MODELS if has_key(settings, model.provider)]
    if settings.chat.model not in {model.id for model in models}:
        models.insert(0, Model(settings.chat.model, settings.chat.provider, 2, "standart (dashboard\\.env)",
                               "the dashboard's default model"))
    return sorted(models, key=lambda model: (model.provider != settings.chat.provider, model.tier))


def label(model_id: str) -> str:
    model = BY_ID.get(model_id)
    return f"{model_id} — {model.label}" if model else model_id


def usable(settings: Settings, model_id: str | None) -> bool:
    """A model from the list above (or the default one) whose provider key is set."""
    known = model_id in BY_ID or model_id == settings.chat.model
    return bool(model_id) and known and has_key(settings, provider_of(model_id))


def for_model(settings: Settings, model_id: str | None) -> Settings:
    """The settings an agent runs with: the same dashboard settings, its own provider and model. A
    model whose provider has no key falls back to the default model."""
    if not usable(settings, model_id) or model_id == settings.chat.model:
        return settings
    return replace(settings, chat=replace(settings.chat, provider=provider_of(model_id), model=model_id))


def builder_model(settings: Settings) -> str:
    chosen = settings.agents.builder_model
    if usable(settings, chosen):
        return chosen
    preferred = BUILDER_DEFAULT.get(settings.chat.provider, DEFAULT_MODELS[settings.chat.provider])
    return preferred if usable(settings, preferred) else settings.chat.model
