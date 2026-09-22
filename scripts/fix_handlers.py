#!/usr/bin/env python3
"""Patch handlers.py to fix two issues."""

path = "/app/app/telegram/handlers.py"

with open(path, "r") as f:
    content = f.read()

# Fix 1: telegram_user_id > 0 instead of isnot(None)
content = content.replace(
    "Employee.telegram_user_id.isnot(None),",
    "Employee.telegram_user_id > 0,"
)

# Fix 2: Replace _notify_general with send to correct topic
old = '''    # General topikga xabar
    await _notify_general(
        bot,
        f"📋 Vazifa #{task_id} → {user_name}\\n📌 {result.title or result.description[:80]}"
    )'''

new = '''    # Guruh topikga xabar — to'g'ri topikka!
    try:
        await bot.send_message(
            chat_id=settings.TELEGRAM_GROUP_ID,
            message_thread_id=reply_thread_id,
            text=f"📋 Vazifa #{task_id} → {user_name}\\n📌 {result.title or result.description[:80]}",
        )
    except Exception as e:
        log.warning("Failed to send topic notification: %s", e)'''

if old in content:
    content = content.replace(old, new)
    print("Fixed notification: now sends to correct topic")
else:
    print("WARNING: notification pattern not found, checking alternatives...")
    # Try to find and replace any _notify_general call after task creation
    import re
    pattern = r'    # General topikga xabar\n    await _notify_general\(\n        bot,\n        f"[^"]*"\n    \)'
    replacement = '''    # Guruh topikga xabar — to'g'ri topikka!
    try:
        await bot.send_message(
            chat_id=settings.TELEGRAM_GROUP_ID,
            message_thread_id=reply_thread_id,
            text=f"📋 Vazifa #{task_id} → {user_name}\\n📌 {result.title or result.description[:80]}",
        )
    except Exception as e:
        log.warning("Failed to send topic notification: %s", e)'''
    content, count = re.subn(pattern, replacement, content)
    print(f"Regex replacement: {count} occurrences")

with open(path, "w") as f:
    f.write(content)

print("Done!")
