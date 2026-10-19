"""Model transport and the tool loop shared by the three agents. The provider (CHAT_PROVIDER: openai
or anthropic), model, effort and keys come from dashboard\\.env (core.settings.ChatConfig); everything
provider-specific lives in the two Provider classes below, so the agents never see the difference.

* Every request sends the agent's frozen system prompt (prompt-cached), its tools, the conversation,
  and the dashboard context as a ``system`` message at the end (it changes every turn, so it sits
  after the cached prefix).
* Within one turn the conversation is append-only (assistant turns go back unchanged); between turns
  only plain text is kept, so no reasoning / thinking blocks are ever replayed.
* Failures become ``AgentError`` carrying the text to show the CEO. Logs get agent and tool names,
  token counts and request ids -- never message text or keys.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable

from core import db
from core.settings import Settings
from ui.i18n import t

log = logging.getLogger("dashboard.assistant")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000
TIMEOUT_SECONDS = 45.0

client_factory: Callable[[Settings], object] | None = None  # tests install a scripted fake client here


class AgentError(Exception):
    """A model call failed; ``str(error)`` is the text for the CEO."""


class ToolError(ValueError):
    """Bad tool input; the message goes back to the model so it can correct the call."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], object]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    request_ids: list[str] = field(default_factory=list)

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.request_ids.extend(other.request_ids)


@dataclass
class AgentResult:
    text: str
    usage: Usage
    tool_calls: list[str]


@dataclass(frozen=True)
class ToolUse:
    id: str
    name: str
    input: object


@dataclass
class Turn:
    """One model response in a provider-neutral shape. ``stop``: end | tool_use | max_tokens | refusal."""

    text: str
    uses: list[ToolUse]
    stop: str
    message: object  # what goes back into the conversation (provider format)


def user_message(text: str, pictures=()) -> dict:
    """A user turn in the neutral form the providers convert: text plus optional pictures
    (core.images.Picture)."""
    message = {"role": "user", "content": text}
    if pictures:
        message["pictures"] = list(pictures)
    return message


def _request_id(response) -> str | None:
    value = getattr(response, "_request_id", None)
    return str(value) if value else None


# --------------------------------------------------------------------------- providers


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client, settings: Settings) -> None:
        self.client, self.chat = client, settings.chat

    @staticmethod
    def _message(message: dict) -> dict:
        pictures = message.get("pictures") or []
        if not pictures:
            return {"role": message["role"], "content": message["content"]}
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": picture.media_type,
                                               "data": picture.base64}} for picture in pictures]
        blocks.append({"type": "text", "text": message["content"] or "(rasm)"})
        return {"role": message["role"], "content": blocks}

    def start(self, system: str, messages: list, context: str) -> dict:
        conversation = [self._message(message) for message in messages]
        if context:
            conversation.append({"role": "system", "content": context})
        return {"system": system, "messages": conversation}

    def create(self, conversation: dict, tools: list[Tool], usage: Usage) -> Turn:
        import anthropic

        kwargs = {
            "model": self.chat.model,
            "max_tokens": MAX_TOKENS,
            "system": [{"type": "text", "text": conversation["system"], "cache_control": {"type": "ephemeral"}}],
            "messages": conversation["messages"],
            "output_config": {"effort": self.chat.effort},
        }
        if tools:
            kwargs["tools"] = [{"name": tool.name, "description": tool.description, "input_schema": tool.schema,
                                "strict": True} for tool in tools]
        if self.chat.fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        try:
            response = self.client.beta.messages.create(**kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            log.warning("Claude rejected the key (%s)", type(exc).__name__)
            raise AgentError(t("chat.err.auth")) from exc
        except anthropic.RateLimitError as exc:
            log.warning("Claude rate limit")
            raise AgentError(t("chat.err.rate")) from exc
        except anthropic.BadRequestError as exc:
            log.warning("Claude bad request: %s", str(getattr(exc, "message", "") or "")[:300])
            raise AgentError(t("chat.err.request")) from exc
        except anthropic.APIStatusError as exc:
            log.warning("Claude error %s", exc.status_code)
            raise AgentError(t("chat.err.server") if exc.status_code >= 500 else t("chat.err.request")) from exc
        except anthropic.APIConnectionError as exc:
            log.warning("Claude unreachable (%s)", type(exc).__name__)
            raise AgentError(t("chat.err.network")) from exc
        meta = getattr(response, "usage", None)
        if meta is not None:
            usage.input_tokens += sum(int(getattr(meta, name, 0) or 0) for name in (
                "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            usage.output_tokens += int(getattr(meta, "output_tokens", 0) or 0)
        if _request_id(response):
            usage.request_ids.append(_request_id(response))
        uses = [ToolUse(block.id, block.name, block.input) for block in response.content if block.type == "tool_use"]
        text = "\n\n".join(block.text for block in response.content if block.type == "text").strip()
        stop = {"refusal": "refusal", "max_tokens": "max_tokens"}.get(response.stop_reason,
                                                                     "tool_use" if uses else "end")
        return Turn(text, uses, stop, response.content)

    def add_turn(self, conversation: dict, turn: Turn) -> None:
        conversation["messages"].append({"role": "assistant", "content": turn.message})

    def add_results(self, conversation: dict, results: list[tuple[ToolUse, str, bool]]) -> None:
        blocks = []
        for use, content, is_error in results:
            block = {"type": "tool_result", "tool_use_id": use.id, "content": content}
            if is_error:
                block["is_error"] = True
            blocks.append(block)
        conversation["messages"].append({"role": "user", "content": blocks})


class OpenAIProvider:
    name = "openai"

    def __init__(self, client, settings: Settings) -> None:
        self.client, self.chat = client, settings.chat

    @staticmethod
    def _message(message: dict) -> dict:
        pictures = message.get("pictures") or []
        if not pictures:
            return {"role": message["role"], "content": message["content"]}
        parts = [{"type": "text", "text": message["content"] or "(rasm)"}]
        parts += [{"type": "image_url", "image_url": {"url": f"data:{picture.media_type};base64,{picture.base64}",
                                                      "detail": "high"}} for picture in pictures]
        return {"role": message["role"], "content": parts}

    def start(self, system: str, messages: list, context: str) -> dict:
        conversation = [{"role": "system", "content": system}, *(self._message(message) for message in messages)]
        if context:
            conversation.append({"role": "system", "content": context})
        return {"messages": conversation}

    def _effort(self, with_tools: bool) -> str | None:
        """Reasoning effort for this request. GPT-5.x models reject function tools together with a
        reasoning effort on /v1/chat/completions (400 "Function tools with reasoning_effort are not
        supported ... set reasoning_effort to 'none'"), so a request with tools runs without reasoning."""
        model = self.chat.model
        if model.startswith("gpt-5"):
            return "none" if with_tools else self.chat.effort
        if model.startswith(("o1", "o3", "o4")):
            return self.chat.effort
        return None

    def create(self, conversation: dict, tools: list[Tool], usage: Usage) -> Turn:
        import openai

        kwargs = {"model": self.chat.model, "messages": conversation["messages"],
                  "max_completion_tokens": MAX_TOKENS, "prompt_cache_key": "dashboard-assistant"}
        effort = self._effort(bool(tools))
        if effort:
            kwargs["reasoning_effort"] = effort
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {"name": tool.name, "description": tool.description,
                                                                 "parameters": tool.schema, "strict": True}}
                               for tool in tools]
        try:
            response = self.client.chat.completions.create(**kwargs)
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            log.warning("OpenAI rejected the key (%s)", type(exc).__name__)
            raise AgentError(t("chat.err.auth")) from exc
        except openai.RateLimitError as exc:
            quota = "insufficient_quota" in str(getattr(exc, "code", "") or "") + str(getattr(exc, "message", "") or "")
            log.warning("OpenAI %s", "quota exhausted" if quota else "rate limit")
            raise AgentError(t("chat.err.quota" if quota else "chat.err.rate")) from exc
        except openai.BadRequestError as exc:
            log.warning("OpenAI bad request: %s", str(getattr(exc, "message", "") or "")[:300])
            raise AgentError(t("chat.err.request")) from exc
        except openai.APIStatusError as exc:
            log.warning("OpenAI error %s", exc.status_code)
            raise AgentError(t("chat.err.server") if exc.status_code >= 500 else t("chat.err.request")) from exc
        except openai.APIConnectionError as exc:
            log.warning("OpenAI unreachable (%s)", type(exc).__name__)
            raise AgentError(t("chat.err.network")) from exc
        meta = getattr(response, "usage", None)
        if meta is not None:
            usage.input_tokens += int(getattr(meta, "prompt_tokens", 0) or 0)
            usage.output_tokens += int(getattr(meta, "completion_tokens", 0) or 0)
        if _request_id(response):
            usage.request_ids.append(_request_id(response))
        choice = response.choices[0]
        message = choice.message
        calls = list(getattr(message, "tool_calls", None) or [])
        uses = []
        for call in calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except ValueError:
                arguments = call.function.arguments or ""  # reported back to the model as invalid input
            uses.append(ToolUse(call.id, call.function.name, arguments))
        if getattr(message, "refusal", None) or choice.finish_reason == "content_filter":
            stop = "refusal"
        elif choice.finish_reason == "length":
            stop = "max_tokens"
        else:
            stop = "tool_use" if uses else "end"
        assistant = {"role": "assistant", "content": message.content}
        if calls:
            assistant["tool_calls"] = [{"id": call.id, "type": "function",
                                        "function": {"name": call.function.name, "arguments": call.function.arguments}}
                                       for call in calls]
        return Turn((message.content or "").strip(), uses, stop, assistant)

    def add_turn(self, conversation: dict, turn: Turn) -> None:
        conversation["messages"].append(turn.message)

    def add_results(self, conversation: dict, results: list[tuple[ToolUse, str, bool]]) -> None:
        for use, content, is_error in results:
            conversation["messages"].append({"role": "tool", "tool_call_id": use.id,
                                             "content": f"ERROR: {content}" if is_error else content})


PROVIDERS = {"anthropic": AnthropicProvider, "openai": OpenAIProvider}


def make_client(settings: Settings):
    if client_factory is not None:
        return client_factory(settings)
    chat = settings.chat
    if chat.provider == "anthropic":
        import anthropic

        headers = {"anthropic-workspace-id": chat.workspace_id} if chat.workspace_id else None
        return anthropic.Anthropic(api_key=chat.api_key, timeout=TIMEOUT_SECONDS, max_retries=1,
                                   default_headers=headers)
    import openai

    return openai.OpenAI(api_key=chat.api_key, timeout=TIMEOUT_SECONDS, max_retries=1)


# --------------------------------------------------------------------------- the tool loop


def _execute(tool: Tool | None, use: ToolUse) -> tuple[str, bool]:
    """Runs one tool call; returns (content, is_error)."""
    if tool is None:
        return f"Unknown tool {use.name!r}.", True
    try:
        args = use.input if isinstance(use.input, dict) else json.loads(use.input or "{}")
        result = tool.handler(args)
    except AgentError:
        raise
    except db.QueryTimeout:
        return "The database query took too long; try a shorter period.", True
    except db.DataError:
        return "The monitoring database is not available right now.", True
    except ValueError as exc:  # ToolError, ui.layout.LayoutError, bad JSON
        return str(exc) or "Invalid input.", True
    except Exception as exc:  # noqa: BLE001 -- a broken tool must not break the chat
        log.warning("tool %s failed: %s", tool.name, type(exc).__name__, exc_info=True)
        return "The tool failed unexpectedly.", True
    if isinstance(result, str):
        return result, False
    return json.dumps(result, ensure_ascii=False, default=str), False


def run_agent(client, settings: Settings, *, name: str, system: str, messages: list, context: str = "",
              tools: list[Tool] | tuple = (), max_steps: int | None = None,
              on_tool: Callable[[str], None] | None = None,
              handoff: Callable[[list, list, str], str | None] | None = None) -> AgentResult:
    """The manual tool loop. ``handoff(tool_uses, outputs, text)`` may end the turn right after the
    tools ran by returning the final text (the orchestrator uses it to pass a sub-agent's answer
    through without another model call)."""
    provider = PROVIDERS[settings.chat.provider](client, settings)
    conversation = provider.start(system, messages, context)
    by_name = {tool.name: tool for tool in tools}
    usage, calls = Usage(), []
    for step in range(max_steps or settings.chat.max_steps):
        turn = provider.create(conversation, list(tools), usage)
        if turn.stop == "refusal":
            log.info("agent=%s refused", name)
            return AgentResult(t("chat.err.refusal"), usage, calls)
        if turn.stop == "max_tokens":  # a cut-off answer or tool call is never used
            if turn.text and not turn.uses:
                return AgentResult(turn.text, usage, calls)
            raise AgentError(t("chat.err.steps"))
        if not turn.uses:
            log.info("agent=%s done after %d step(s), tokens in=%d out=%d", name, step + 1, usage.input_tokens,
                     usage.output_tokens)
            return AgentResult(turn.text, usage, calls)
        provider.add_turn(conversation, turn)
        outputs = []
        for use in turn.uses:
            calls.append(use.name)
            if on_tool is not None:
                on_tool(use.name)
            content, is_error = _execute(by_name.get(use.name), use)
            outputs.append((use.name, content, is_error))
        log.info("agent=%s step=%d tools=%s errors=%d", name, step + 1, ",".join(use.name for use in turn.uses),
                 sum(1 for _name, _content, error in outputs if error))
        if handoff is not None:
            final = handoff(turn.uses, outputs, turn.text)
            if final is not None:
                return AgentResult(final, usage, calls)
        provider.add_results(conversation, [(use, content, error) for use, (_n, content, error) in zip(turn.uses, outputs)])
    raise AgentError(t("chat.err.steps"))


def nullable(schema: dict, description: str = "") -> dict:
    """``schema`` or null (strict tool schemas list every property as required)."""
    result: dict = {"anyOf": [schema, {"type": "null"}]}
    if description:
        result["description"] = description
    return result


def obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
