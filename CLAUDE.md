# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A FastAPI backend for an OpenAI-powered sales chatbot for "Dunya Bunya", a construction-materials store. It answers customer questions about products (price, availability, site link) from an in-memory copy of the site's product catalog, loaded from the site's public products API, and enforces strict business rules (Uzbek-only replies, exact out-of-stock wording, no upselling when out of stock, no leaking internal DB columns) via the system prompt in `app/ai_service.py`.

## Commands

Local setup and run:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8003 --reload
```

Docker (matches production deploy — `network_mode: host`, reads `.env`):
```bash
docker compose up --build
```

Unit tests (from the repo root): `python -m unittest tests.test_catalog tests.test_pii_mask -v`. There is no linter configuration in this repo.

## Environment

Configured via `.env` (see `.env.example`), loaded through `app/config.py` (pydantic-settings):
- `PRODUCTS_API_URL` (default `https://demo.dunyabunya.uz/auth-api/api/products`, no key), `PRODUCTS_REFRESH_SECONDS` (default 300): where the product catalog comes from and how often it is reloaded into memory. `SITE_BASE_URL` (default `https://demo.dunyabunya.uz`) is used for the customer-facing link `{SITE_BASE_URL}/{uz|ru}/catalog/{id}`.
- `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-4o`) — OpenAI access. `gpt-4o` was picked over `gpt-4o-mini` after measuring both directly on this account: `gpt-4o-mini` was consistently *slower* here (~1586ms avg vs ~1211ms for a plain call, `gpt-4.1-mini` slower still) — don't assume the "mini" variant is faster without re-measuring on the account in use.
- `OPENAI_TIMEOUT_SECONDS` (default 20), `MAX_RESPONSE_TOKENS` (default 500) — applied to the `AsyncOpenAI` client / each `chat.completions.create` call.
- `COMPANY_NAME`, `COMPANY_PHONE`, `COMPANY_ADDRESS`, `COMPANY_WORK_HOURS` — fill `{KOMPANIYA_NOMI}/{TELEFON}/{MANZIL}/{ISH_VAQTI}` placeholders in `app/system_prompt.txt`. `COMPANY_PHONE`/`ADDRESS`/`WORK_HOURS` are empty by default — until set, the bot will show a blank value where the phone/address/hours should be (e.g. in the nasiya-savdo and out-of-scope replies).
- `HISTORY_MAX_MESSAGES` (default 10) — only the last N messages of a conversation are sent to OpenAI, not the full history.
- `RATE_LIMIT_PER_MINUTE` (default 10), `SESSION_MESSAGE_LIMIT` (default 50) — in-memory, per-`conversation_id` abuse guard enforced in `POST /api/chatbot/ask` only (not in the poller path below); see `app/rate_limit.py`.
- `CORS_ORIGINS` — comma-separated allowed origins for the web widget calling this API (`*` = open). Parsed via `settings.cors_origins_list` in `app/config.py`.

## Architecture

**Request flow** (`app/main.py`): `POST /api/chatbot/ask` saves the incoming user message to `chat_store`, loads the full conversation history back out, passes it to `get_answer()` in `app/ai_service.py`, then saves the bot's reply. `chat_store` (`app/store.py`) is a plain in-memory dict keyed by `conversation_id` — it is **not** persisted anywhere and is wiped on every restart/redeploy. There is no database. `GET /health` returns `{"status": "ok"}` with no dependency checks (it doesn't verify that the catalog loaded or that OpenAI is reachable).

**AI layer** (`app/ai_service.py`, using the official `openai` SDK's async Chat Completions API): only the last `settings.history_max_messages` entries of `chat_store`'s history are sent (not the full history) — `history` roles of `"bot"` map to `"assistant"`, `"user"` stays `"user"` and is run through `mask_pii()` first, and `SYSTEM_INSTRUCTION` (loaded from `app/system_prompt.txt` at import time, with `{KOMPANIYA_NOMI}/{TELEFON}/{MANZIL}/{ISH_VAQTI}` substituted from `settings`) is always prepended as the `system` message. Tools are declared via `app/tools/` — an MCP-tool-shaped registry (`Tool(name, description, parameters, handler)` in `app/tools/base.py`, `TOOLS_REGISTRY` in `app/tools/__init__.py`) rather than a hardcoded OpenAI schema; `get_answer()` builds the `tools` param and dispatches `tool_calls` generically by name (`TOOLS_BY_NAME`), so a real MCP client can later replace a `handler` without touching the dispatch loop. `search_products_tool` (`app/tools/search_products.py`) is the one entry today, wrapping `search_products()`. When the model returns a matching `tool_calls` entry, `get_answer()` parses the JSON arguments, calls `tool.handler()`, appends the assistant's tool-call message (`message.model_dump(exclude_none=True)`) plus a `role: "tool"` message carrying the JSON result keyed by `tool_call_id`, and makes a second Chat Completions call to get the final reply. Both Chat Completions calls are wrapped in `try/except`: on any OpenAI-side failure (timeout, network, API error) `get_answer()` returns a fixed apology in the client's language instead of raising — language is picked by a cheap heuristic (`_detect_language`, looks for Russian-only Cyrillic letters), not real language detection. Every successful reply is run through `_finalize()`, which also strips `**bold**`, `#headers`, `` `code` `` and `|` via `_strip_markdown()` (the UI can't render markdown) and falls back to a fixed "couldn't produce an answer" message if the model returns empty text.

Not-found handling is enforced in code, not just prompted: if `search_products()` returns nothing, `get_answer()` short-circuits and returns the fixed `NOT_FOUND_MESSAGE` without a second round-trip to OpenAI. A product that is found but out of stock goes to the model, which states its price, says "hozircha sotuvda yo'q" and offers the alternatives (prompt rules 5-6). The backend's acceptance test requires this: "Adapter PPR VERO" is out of stock and must still be answered with its price.

**Reply format and the site widget**: the site's chat widget (demo.dunyabunya.uz) renders the bot's text as a plain `<div>` with no `whitespace-pre-line`, so newlines collapse into spaces, and URLs are not clickable. The prompt (TIL VA FORMAT) therefore asks for replies that still read correctly on one line: full sentences, a numbered "1) " list instead of "- ", and each product written as "Nomi — narx, sotuvda bor." followed by its link on its own line. `_finalize()` also swaps the spaces inside prices ("248 880 so'm") for non-breaking spaces so a price never wraps. The proper fix is on the frontend: add `whitespace-pre-line break-words` to the bot bubble and linkify URLs.

**Catalog layer** (`app/catalog.py`): until 2026-09-29 the catalog was read from a Supabase Postgres `Products` table. The site database has since moved to the company's own server, and the Supabase copy is no longer updated, so the only source now is the site's products API (`GET ?page=N&limit=200`; 200 is the API's maximum, and pages are fetched until `total` is reached). It lists only products that are visible on the site. `refresh_products()` runs once at startup (a failure there does not block startup), and `refresh_loop()` repeats it every `PRODUCTS_REFRESH_SECONDS`, or every 30s while the list is still empty. On failure the last good list is kept. Requests never call the API. `search_products()` splits the query into words and requires each word to match the name, `nameUz` or brand (case-insensitive substring), rather than matching the phrase as one substring. This is deliberate, since the LLM frequently reorders words relative to how they're stored (e.g. product stored as "AVR CHINT (...)" but the model asks for "CHINT AVR"). Each word is expanded through `app/aliases.py`. Direct hits (max 5) get up to 3 same-category alternatives, and with no direct hit the words are matched against the category path (max 8). In both cases in-stock items come first. The results carry `link` instead of the internal `id`, and the API's `price: 0` is returned as `null` so the bot never says "0 so'm". The tool's `lang` argument (`uz`/`ru`) picks the link language and the name (`nameUz` when present for `uz`). Unit tests: `tests/test_catalog.py`.

**Request/response contracts** (`app/models.py`): internal field names (`role`, `content`, `conversation_id`) are decoupled from the wire format via Pydantic aliases — `SaveMessageRequest` expects `conversationId`/`sender`/`text` on the wire (matching an external frontend's existing naming), while `AskRequest`/`AskResponse`/`ChatHistoryResponse` use `conversationId`. All routes use `response_model_by_alias=True`, so responses serialize using these aliases too.

**PII masking** (`app/pii_mask.py`): `mask_pii()` replaces passport numbers (2 letters + 7 digits), JSHSHIR (14 digits) and card numbers (16 digits, with or without spaces/dashes) with placeholders. Applied only to `role == "user"` content inside `get_answer()`, right before it's sent to OpenAI — `chat_store` itself still holds the raw text (masking happens at the OpenAI-call boundary, not at storage/log time, since nothing currently logs message content). Unit tests: `tests/test_pii_mask.py` (`python -m unittest tests.test_pii_mask -v`).

**Rate/session limiting** (`app/rate_limit.py`): `SessionLimiter` is an in-memory, per-process, per-`conversation_id` limiter (sliding 60s window + a lifetime total), checked at the top of `POST /api/chatbot/ask` before anything is persisted or sent to OpenAI. Resets on process restart; not applied to the `app/poller.py` path.

**Known inconsistency**: the hardcoded `NOT_FOUND_MESSAGE` in `app/ai_service.py` (code-level short-circuit for "no results", kept deliberately non-LLM for reliability per the original design) is Uzbek-only and does not match rule 2 of `app/system_prompt.txt` ("mahsulot haqida bilmasligingni ayt ... {TELEFON}") — the two were written independently and never reconciled, and the code-level message always wins since it returns before the model sees rule 2. Flagged for the developer to reconcile; not changed here since it's user-facing copy.

**Manual test scenarios**: `tests/manual_scenarios.py` — hits a running local server (`http://127.0.0.1:8003`) with the scenarios from the phase-1 spec (capabilities question in uz/ru, off-topic, prompt injection, fake-discount claim, PII, rate-limit flood, etc.) and prints each answer plus the flood-test's per-message outcome.
