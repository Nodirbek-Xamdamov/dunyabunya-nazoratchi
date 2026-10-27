"""The data agent ("Savol-javob agenti"): answers questions about the monitoring data with the read-only
tools of assistant.data_tools. It has no tool that changes anything."""

from __future__ import annotations

from assistant import data_tools, llm, prompts
from core.settings import Settings


def describe_tools(settings: Settings) -> list[llm.Tool]:
    """The tools with a placeholder context, for the «AI agentlar» page (names and descriptions only)."""
    return data_tools.build(settings, {"group_id": "-"})


def run(client, settings: Settings, question: str, context: dict, pictures=(), extra_tools=(),
        agents: dict | None = None) -> llm.AgentResult:
    """``extra_tools``: the tools that call the created agents below this one."""
    return llm.run_agent(client, settings, name="data", system=prompts.system("data", settings, agents),
                         messages=[llm.user_message(question, pictures)], context=prompts.data_context(context),
                         tools=[*data_tools.build(settings, context), *extra_tools])
