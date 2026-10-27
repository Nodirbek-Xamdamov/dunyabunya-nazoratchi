"""The agent designer behind «Agent qo'shish». From the name and the task the user describes, one model
call proposes the agent: its model, its place in the tree, its tool groups, whether it needs its own
Telegram bot, what is not available, questions when the task is unclear, and the system prompt. The
page shows the proposal; the user changes anything and creates the agent there. Nothing is stored here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from assistant import llm, models, prompts, registry
from assistant.llm import AgentError, Tool, obj
from core.settings import Settings
from ui.i18n import t

MAX_NAME, MAX_TASK = 60, 4000

SYSTEM = """You design AI agents for the "dunyabunya monitoring" dashboard of a company that sells \
construction materials. The dashboard monitors how customers are served in the company's Telegram groups, \
and has an AI assistant made of agents arranged as a tree: «Boshqaruvchi» (the orchestrator) receives the CEO's \
messages in the dashboard chat and hands work to the agents below it; an agent can hand work to its own \
sub-agents (at most three levels).

The user gives a new agent's name and describes its task in their own words (usually Uzbek). Propose the agent \
by calling submit_design once.

What an agent can use -- pick only what the task needs:
- Tool groups (listed in the request).
- Its own Telegram bot (telegram_bot = true): the user creates a new bot; people allowed on the page chat with it \
privately and the agent answers them. It answers messages only: it cannot write to people first, post to groups \
or run on a schedule.
- Every agent is also reachable in the dashboard chat through its parent.

Not available -- put each such need in "missing" (in Uzbek, with why the task needs it), and never pretend the \
agent can do it: writing or changing anything in Odoo; messaging customers or groups, writing first or at a set \
time (daily reports, reminders); changing how the dashboard looks (colors, fonts, light/dark mode); e-mail, SMS, \
phone calls; the internet; files from the user; other systems (1C, banks, MyZvonki ...).

How to choose:
- model: the cheapest model that does the task well (the request lists them with what they suit). \
model_reason in Uzbek, one sentence.
- parent: usually Boshqaruvchi; under another agent only when the new agent extends that agent's own work. \
parent_reason in Uzbek, one sentence.
- tool_groups: the minimum the task needs. "view" changes the dashboard for everyone: only when the task is \
about changing what the dashboard shows. tools_reason in Uzbek.
- telegram_bot: true only when people should talk to the agent in Telegram. telegram_reason in Uzbek.
- routing (English, one sentence): which requests the parent should send to this agent.
- summary (Uzbek, 1-2 sentences): what the agent does, for its card on the page.
- questions (Uzbek, 0-3): only when something essential is missing (who uses it, which data, which period, \
what result). Empty when the task is clear enough.

system_prompt (English, plain text, 150-500 words), the agent's instructions:
- who it is (its name), whom it serves and what it does, step by step, from the task;
- which of its tools to use for what, and which period to use when none is named;
- what a good answer looks like: in the user's language (Uzbek in Latin script unless they write otherwise), \
how long, what first; in Telegram short plain text;
- its limits: numbers only from tools, say what is missing instead of guessing, what it must not do (from the \
task and the "not available" list), and to say plainly when a request needs something it does not have.
Do not describe tool schemas, the dashboard's reference data or any keys -- the dashboard appends the reference \
itself."""


@dataclass
class Draft:
    name: str
    task: str
    summary: str
    routing: str
    model: str
    model_reason: str
    parent: str
    parent_reason: str
    tool_groups: list[str]
    tools_reason: str
    telegram: bool
    telegram_reason: str
    missing: list[dict]
    questions: list[str]
    system_prompt: str
    usage: llm.Usage = field(default_factory=llm.Usage)


def _request(name: str, task: str, agents: dict, available: list, parents: list[str]) -> str:
    groups = "\n".join(f"- {group.id}: {group.about}" for group in registry.TOOL_GROUPS.values())
    model_lines = "\n".join(f"- {model.id}: {model.use}" for model in available)
    parent_lines = "\n".join(f"- {agent_id}: {agents[agent_id].name} -- {agents[agent_id].role}" for agent_id in parents)
    existing = "\n".join(f"- {agent.name}: {agent.role}" for agent in agents.values() if agent.custom) or "- none"
    return (f"New agent's name: {name}\n\nTask, in the user's words:\n{task}\n\nTool groups:\n{groups}\n\n"
            f"Models:\n{model_lines}\n\nWhere it can sit (parent id: name -- role):\n{parent_lines}\n\n"
            f"Agents created earlier (avoid duplicating them):\n{existing}")


def _schema(available: list, parents: list[str]) -> dict:
    text = {"type": "string"}
    return obj({
        "summary": text,
        "routing": text,
        "model": {"type": "string", "enum": [model.id for model in available]},
        "model_reason": text,
        "parent": {"type": "string", "enum": parents},
        "parent_reason": text,
        "tool_groups": {"type": "array", "items": {"type": "string", "enum": list(registry.TOOL_GROUPS)}},
        "tools_reason": text,
        "telegram_bot": {"type": "boolean"},
        "telegram_reason": text,
        "missing": {"type": "array", "items": obj({"need": text, "why": text})},
        "questions": {"type": "array", "items": text},
        "system_prompt": text,
    })


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit] if limit < 1000 else str(value or "").strip()[:limit]


def design(settings: Settings, name: str, task: str, agents: dict | None = None, client=None) -> Draft:
    """One model call. Raises AgentError (text for the page) when the model cannot be used or returns
    nothing usable."""
    name, task = _clean(name, MAX_NAME), str(task or "").strip()[:MAX_TASK]
    agents = agents if agents is not None else registry.load(settings)
    available = models.available(settings)
    parents = registry.parent_options(agents)
    captured: dict = {}

    def submit(args: dict) -> dict:
        captured.clear()
        captured.update(args)
        return {"result": "received"}

    builder_settings = models.for_model(settings, models.builder_model(settings))
    result = llm.run_agent(llm.pool(client).get(builder_settings), builder_settings, name="builder", system=SYSTEM,
                           messages=[llm.user_message(_request(name, task, agents, available, parents))],
                           tools=[Tool("submit_design", "Submit the proposed agent.", _schema(available, parents),
                                       submit)],
                           max_steps=2, force_tool="submit_design",
                           handoff=lambda uses, outputs, text: "ok" if captured else None)
    prompt = str(captured.get("system_prompt") or "").strip()
    if not prompt:
        raise AgentError(t("ag.err.builder"))
    model_ids = {model.id for model in available}
    groups = [group for group in dict.fromkeys(captured.get("tool_groups") or []) if group in registry.TOOL_GROUPS]
    missing = [{"need": _clean(item.get("need"), 200), "why": _clean(item.get("why"), 300)}
               for item in captured.get("missing") or [] if isinstance(item, dict) and item.get("need")][:6]
    return Draft(
        name=name, task=task,
        summary=_clean(captured.get("summary"), 300),
        routing=_clean(captured.get("routing"), 300) or f"Requests about: {name}",
        model=captured.get("model") if captured.get("model") in model_ids else settings.chat.model,
        model_reason=_clean(captured.get("model_reason"), 300),
        parent=captured.get("parent") if captured.get("parent") in parents else registry.ROOT,
        parent_reason=_clean(captured.get("parent_reason"), 300),
        tool_groups=groups, tools_reason=_clean(captured.get("tools_reason"), 400),
        telegram=bool(captured.get("telegram_bot")), telegram_reason=_clean(captured.get("telegram_reason"), 300),
        missing=missing,
        questions=[_clean(question, 300) for question in captured.get("questions") or [] if str(question).strip()][:3],
        system_prompt=prompt[:prompts.MAX_INSTRUCTIONS], usage=result.usage)
