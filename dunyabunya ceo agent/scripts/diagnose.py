#!/usr/bin/env python3
"""
Comprehensive diagnostic script — checks all components step by step.
Run: docker compose exec -T app python3 scripts/diagnose.py
"""

import asyncio
import json
import sys
import xmlrpc.client
from datetime import date

# ─── Step 1: Config & .env ────────────────────────────────────────────────

print("=" * 60)
print("STEP 1: Configuration (.env)")
print("=" * 60)

try:
    from app.config import settings
    print(f"  ✅ TELEGRAM_BOT_TOKEN: ...{settings.TELEGRAM_BOT_TOKEN[-8:]}")
    print(f"  ✅ TELEGRAM_GROUP_ID:  {settings.TELEGRAM_GROUP_ID}")
    print(f"  ✅ CEO_TELEGRAM_USER_ID:  {settings.CEO_TELEGRAM_USER_ID}")
    print(f"  ✅ CEO_TELEGRAM_USERNAME: {settings.CEO_TELEGRAM_USERNAME or '(not set)'}")
    print(f"  ✅ ANTHROPIC_API_KEY:  ...{settings.ANTHROPIC_API_KEY[-8:]}")
    print(f"  ✅ ANTHROPIC_MODEL:    {settings.ANTHROPIC_MODEL}")
    print(f"  ✅ ANTHROPIC_WORKSPACE_ID: {settings.ANTHROPIC_WORKSPACE_ID or '(not set)'}")
    print(f"  ✅ GEMINI_API_KEY:     ...{settings.GEMINI_API_KEY[-8:]}")
    print(f"  ✅ GEMINI_MODEL:       {settings.GEMINI_MODEL}")
    print(f"  ✅ DATABASE_URL:       ...{settings.DATABASE_URL.split('@')[-1]}")
    print(f"  ✅ ODOO_URL:           {settings.ODOO_URL}")
    print(f"  ✅ ODOO_DB:            {settings.ODOO_DB}")
    print(f"  ✅ ODOO_LOGIN:         {settings.ODOO_LOGIN}")
    print(f"  ✅ ENCRYPTION_KEY:     ...{settings.ENCRYPTION_KEY[-8:]}")
    print()
except Exception as e:
    print(f"  ❌ Config load failed: {e}")
    sys.exit(1)


# ─── Step 2: Database ──────────────────────────────────────────────────────

print("=" * 60)
print("STEP 2: Database (PostgreSQL)")
print("=" * 60)

async def check_db():
    try:
        from app.db.session import SessionLocal, init_db
        from app.db.repo import list_topics
        await init_db()
        async with SessionLocal() as session:
            topics = await list_topics(session)
            print(f"  ✅ Connected to PostgreSQL")
            print(f"  ✅ Topics table has {len(topics)} rows:")
            for t in topics:
                print(f"     - {t.name} (topic_id={t.telegram_topic_id}, odoo_project={t.odoo_project_id})")
    except Exception as e:
        print(f"  ❌ Database error: {e}")

asyncio.run(check_db())
print()


# ─── Step 3: Gemini API ────────────────────────────────────────────────────

print("=" * 60)
print("STEP 3: Gemini API (Triage + Transcription)")
print("=" * 60)

async def check_gemini():
    try:
        from google import genai
        from app.config import settings

        client = genai.Client(api_key=settings.GEMINI_API_KEY)

        # Simple test prompt
        interaction = client.interactions.create(
            model=settings.GEMINI_MODEL,
            input="Respond with exactly: {\"is_task\": true, \"confidence\": 0.9}",
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": {
                    "type": "object",
                    "properties": {
                        "is_task": {"type": "boolean"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["is_task", "confidence"],
                },
            },
        )
        print(f"  ✅ Gemini API responded: {interaction.output_text[:100]}")
    except Exception as e:
        print(f"  ❌ Gemini API error: {e}")

asyncio.run(check_gemini())
print()


# ─── Step 4: Anthropic (Claude) API ────────────────────────────────────────

print("=" * 60)
print("STEP 4: Anthropic (Claude) API")
print("=" * 60)

async def check_claude():
    try:
        import anthropic
        from app.config import settings

        extra_headers = {}
        if settings.ANTHROPIC_WORKSPACE_ID:
            extra_headers["anthropic-workspace-id"] = settings.ANTHROPIC_WORKSPACE_ID

        client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            default_headers=extra_headers or None,
        )

        # Simple test message
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": "Say hello in one word."}],
        )
        text = response.content[0].text if response.content else "(empty)"
        print(f"  ✅ Claude API responded ({settings.ANTHROPIC_MODEL}): {text}")
    except Exception as e:
        err_str = str(e)
        if "not_found_error" in err_str:
            print(f"  ❌ Model '{settings.ANTHROPIC_MODEL}' not found!")
            print(f"     Fix: update ANTHROPIC_MODEL in .env to a valid model ID.")
            print(f"     Current active models: claude-sonnet-4-6, claude-opus-4-6, claude-haiku-4-5")
        elif "invalid_request_error" in err_str and "workspace" in err_str.lower():
            print(f"  ❌ Workspace ID required but not set correctly.")
            print(f"     Current ANTHROPIC_WORKSPACE_ID: {settings.ANTHROPIC_WORKSPACE_ID}")
        else:
            print(f"  ❌ Claude API error: {e}")

asyncio.run(check_claude())
print()


# ─── Step 5: Odoo XML-RPC ─────────────────────────────────────────────────

print("=" * 60)
print("STEP 5: Odoo XML-RPC Connection")
print("=" * 60)

def check_odoo():
    from app.config import settings
    try:
        url = settings.ODOO_URL.rstrip("/")
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        uid = common.authenticate(settings.ODOO_DB, settings.ODOO_LOGIN, settings.ODOO_API_KEY, {})
        if uid:
            print(f"  ✅ Odoo authenticated: uid={uid}")
            obj = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
            # List project.project records
            projects = obj.execute_kw(
                settings.ODOO_DB, uid, settings.ODOO_API_KEY,
                "project.project", "search_read",
                [[], ["id", "name"]],
                {"limit": 10},
            )
            print(f"  ✅ Found {len(projects)} Odoo projects:")
            for p in projects:
                print(f"     - id={p['id']}: {p['name']}")
        else:
            print(f"  ❌ Odoo authenticate() returned {uid} — wrong login/API key?")
    except Exception as e:
        print(f"  ❌ Odoo XML-RPC error: {e}")

check_odoo()
print()


# ─── Step 6: Telegram Bot Info ─────────────────────────────────────────────

print("=" * 60)
print("STEP 6: Telegram Bot API Check")
print("=" * 60)

async def check_telegram():
    import httpx
    from app.config import settings

    token = settings.TELEGRAM_BOT_TOKEN
    async with httpx.AsyncClient() as client:
        # getMe
        r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        data = r.json()
        if data.get("ok"):
            bot = data["result"]
            print(f"  ✅ Bot: @{bot['username']} (id={bot['id']}, name={bot['first_name']})")
        else:
            print(f"  ❌ getMe failed: {data}")

        # getWebhookInfo
        r = await client.get(f"https://api.telegram.org/bot{token}/getWebhookInfo")
        data = r.json()
        if data.get("ok"):
            info = data["result"]
            if info["url"]:
                print(f"  ⚠️  Webhook is SET to: {info['url']}")
                print(f"     Polling won't work while webhook is active! Delete it first.")
            else:
                print(f"  ✅ No webhook set — polling mode is correct")
            print(f"     Pending updates: {info['pending_update_count']}")

asyncio.run(check_telegram())
print()


# ─── Step 7: Telegram Group Membership ─────────────────────────────────────

print("=" * 60)
print("STEP 7: Telegram Group Membership & Permissions")
print("=" * 60)

async def check_group():
    import httpx
    from app.config import settings

    token = settings.TELEGRAM_BOT_TOKEN
    group_id = settings.TELEGRAM_GROUP_ID
    async with httpx.AsyncClient() as client:
        # getChat
        r = await client.get(f"https://api.telegram.org/bot{token}/getChat", params={"chat_id": group_id})
        data = r.json()
        if data.get("ok"):
            chat = data["result"]
            print(f"  ✅ Group: {chat.get('title', 'N/A')} (id={chat['id']}, type={chat['type']})")
        else:
            print(f"  ❌ Cannot access group {group_id}: {data.get('description', data)}")
            return

        # getChatMemberCount
        r = await client.get(f"https://api.telegram.org/bot{token}/getChatMemberCount", params={"chat_id": group_id})
        data = r.json()
        if data.get("ok"):
            print(f"  ✅ Bot is in the group (member count: {data['result']})")
        else:
            print(f"  ❌ Bot cannot get member count: {data}")

asyncio.run(check_group())
print()


# ─── Step 8: Topic ID Mapping ─────────────────────────────────────────────

print("=" * 60)
print("STEP 8: Topic ID Mapping (DB vs Actual)")
print("=" * 60)

async def check_topics():
    from app.config import settings
    import httpx

    token = settings.TELEGRAM_BOT_TOKEN
    group_id = settings.TELEGRAM_GROUP_ID

    # Get topics from DB
    from app.db.session import SessionLocal
    from app.db.repo import list_topics
    async with SessionLocal() as session:
        topics = await list_topics(session)

    db_topic_ids = {t.telegram_topic_id for t in topics}

    # Get topics from Telegram API (forum topic info)
    async with httpx.AsyncClient() as client:
        # List forum topics via getForumTopicIconStickers (just to verify it's a forum)
        for t in topics:
            r = await client.get(
                f"https://api.telegram.org/bot{token}/getChat",
                params={"chat_id": group_id},
            )
            chat = r.json().get("result", {})
            is_forum = chat.get("is_forum", False)
            if is_forum:
                print(f"  ✅ Group is a FORUM (topics enabled)")
            else:
                print(f"  ❌ Group is NOT a forum! Topics won't work.")
                print(f"     Fix: Enable Topics in group settings.")
            break

    print(f"\n  DB topics:")
    for t in topics:
        status = "✅" if t.telegram_topic_id in db_topic_ids else "❌"
        print(f"    {status} {t.name}: topic_id={t.telegram_topic_id}, odoo_project={t.odoo_project_id}")

asyncio.run(check_topics())
print()


# ─── Step 9: Encryption Key ────────────────────────────────────────────────

print("=" * 60)
print("STEP 9: Encryption Key Test")
print("=" * 60)

try:
    from app.services.crypto import encrypt, decrypt
    test = "test_api_key_12345"
    encrypted = encrypt(test)
    decrypted = decrypt(encrypted)
    assert decrypted == test
    print(f"  ✅ Fernet encrypt/decrypt works correctly")
except Exception as e:
    print(f"  ❌ Encryption error: {e}")
print()


# ─── Summary ───────────────────────────────────────────────────────────────

print("=" * 60)
print("DIAGNOSIS SUMMARY")
print("=" * 60)
print("""
If bot is NOT receiving messages, the most likely causes are:

1. ROGUE PROCESS: Another process using the same bot token is polling.
   Run: ps aux | grep "app.main" | grep -v grep | grep -v docker
   If found outside Docker → kill it with sudo.

2. PRIVACY MODE: Bot must be in the group with privacy mode OFF.
   Send /setprivacy to @BotFather → Disable

3. TOPIC IDs: The telegram_topic_id in DB must match actual topic IDs.
   Send a message in each topic and check getUpdates to see the IDs.

4. MESSAGE THREAD: Bot only processes messages in specific topics,
   not in "General" (unless on_main_group_general_message is triggered).
""")
