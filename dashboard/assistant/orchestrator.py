"""The orchestrator ("Boshqaruvchi"): answers general questions itself and hands data questions to the
data agent, changes of what the dashboard shows to the view agent, and anything matching a created
agent below it to that agent (all are tools of its loop). When
exactly one agent was needed, that agent's answer goes straight to the CEO without another model call.
Every agent runs with its own model (chosen on the «AI agentlar» page; default from dashboard\\.env)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from assistant import data_agent, llm, prompts, team, ui_agent
from assistant.llm import AgentError, Tool, obj
from core.settings import Settings
from ui.i18n import t

__all__ = ["AgentError", "Reply", "handle", "history_messages"]

ORCHESTRATOR_STEPS = 4
ERROR_AGENT = "error"  # chat_messages.agents of error replies (left out of the model's context)

_INSTRUCTION = obj({"instruction": {"type": "string", "description": "The CEO's request with the context needed"}})
TOOL_SPECS = (  # (name, description, input schema); the handlers are made per message in handle()
    ("ask_data", "Ask the data agent a question about the monitoring data (numbers, groups, complaints, requests, "
                 "staff, trends, report summaries). It answers in the CEO's language.",
     obj({"question": {"type": "string", "description": "A complete, self-contained question"}})),
    ("change_view", "Ask the view agent to change what the dashboard shows: hide, show, move or rename sections, "
                    "columns and pages; add new sections (KPI cards, tables, lists, charts, notes); create or delete "
                    "own pages; undo, reset.", _INSTRUCTION),
)


@dataclass
class Reply:
    text: str
    agents: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    usage: llm.Usage = field(default_factory=llm.Usage)


def without_repeats(text: str) -> str:
    """Drops a line that repeats the previous non-empty line word for word (models occasionally write
    the same sentence or paragraph twice)."""
    lines, previous = [], None
    for line in text.splitlines():
        key = line.strip()
        if key and key == previous:
            continue
        if key:
            previous = key
        lines.append(line)
    return "\n".join(lines).strip()


def history_messages(history: list[dict], message: str) -> list[dict]:
    """Earlier chat turns as plain text (the changes each answer made are appended to it), then the new
    message. Error replies are left out; the list always starts with a user turn."""
    messages: list[dict] = []
    for item in history:
        if item.get("agents") == ERROR_AGENT:
            continue
        text = str(item.get("text") or "").strip()
        if item.get("files"):
            text = (text + "\n" if text else "") + f"[{len(item['files'])} ta rasm biriktirilgan edi]"
        if item.get("role") == "assistant":
            changes = item.get("changes") or []
            if changes:
                text = (text + "\n" if text else "") + f"[{t('chat.changes')}: {'; '.join(changes)}]"
            if text:
                messages.append({"role": "assistant", "content": text})
        elif text:
            messages.append({"role": "user", "content": text})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    messages.append({"role": "user", "content": message})
    return messages


def describe_tools() -> list[Tool]:
    """The tools without handlers, for the «AI agentlar» page (names and descriptions only)."""
    return [Tool(name, description, schema, lambda _args: None) for name, description, schema in TOOL_SPECS]


def _attach(messages: list[dict], pictures) -> list[dict]:
    if pictures:
        messages[-1] = llm.user_message(messages[-1]["content"], pictures)
    return messages


def handle(settings: Settings, message: str, history: list[dict], context: dict,
           on_step: Callable[[str], None] | None = None, client=None, pictures=()) -> Reply:
    """One CEO message. ``context``: group_id (change group of this message), page_id, period, now.
    ``pictures`` (core.images.Picture, usually screenshots of the dashboard) go to the orchestrator and
    to whichever agent it calls. Raises AgentError (with the text for the CEO) when the model cannot
    be used."""
    call = team.Call(settings, llm.pool(client), context, tuple(pictures), on_step)
    answers = call.answers

    def specialist(agent_id: str, label: str, run, text: str) -> object:
        call.step(label)
        call.used.append(agent_id)
        result = run(call.client_of(agent_id), call.settings_of(agent_id), text, context, pictures,
                     extra_tools=team.child_tools(call, agent_id), agents=call.agents)
        call.usage.add(result.usage)
        return result

    def ask_data(args: dict) -> dict:
        result = specialist("data", t("chat.step.data"), data_agent.run,
                            str(args.get("question") or "").strip() or message)
        answers["ask_data"] = result.text
        return {"answer": result.text}

    def change_view(args: dict) -> dict:
        result = specialist("view", t("chat.step.ui"), ui_agent.run,
                            str(args.get("instruction") or "").strip() or message)
        call.changes.extend(result.changes)
        answers["change_view"] = result.text
        return {"reply": result.text, "changes_made": result.changes or ["none"]}

    def handoff(uses: list, outputs: list, text: str) -> str | None:
        if len(uses) == 1 and not text and not outputs[0][2]:
            return answers.get(uses[0].name) or None
        return None

    handlers = {"ask_data": ask_data, "change_view": change_view}
    tools = [Tool(name, description, schema, handlers[name]) for name, description, schema in TOOL_SPECS]
    tools += team.child_tools(call, "orchestrator")
    call.step(t("chat.step.start"))
    result = llm.run_agent(call.client_of("orchestrator"), call.settings_of("orchestrator"), name="orchestrator",
                           system=prompts.system("orchestrator", settings, call.agents),
                           messages=_attach(history_messages(history, message), pictures),
                           context=prompts.orchestrator_context(context),
                           tools=tools, max_steps=ORCHESTRATOR_STEPS, handoff=handoff)
    call.usage.add(result.usage)
    fallback = (answers.get("change_view") or answers.get("ask_data")
                or next(iter(answers.values()), None) or t("chat.err.internal"))
    return Reply(text=without_repeats(result.text) or without_repeats(fallback), agents=call.used,
                 changes=call.changes, usage=call.usage)
