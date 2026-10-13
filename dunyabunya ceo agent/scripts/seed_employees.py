#!/usr/bin/env python3
"""
Seed Odoo employees into the local file-based employee catalog
(storage/employees.json). Run this script to sync Odoo users locally.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import employee_store
from app.services.odoo import OdooClient


async def main():
    """Main function to seed employees."""
    print("🚀 Starting employee seeding...")

    # Fetch users from Odoo
    print("📡 Fetching users from Odoo...")
    client = OdooClient()
    users = await client.execute_kw(
        "res.users",
        "search_read",
        [[["active", "=", True]], ["id", "name"]]
    )
    print(f"   Found {len(users)} users in Odoo")

    # Seed into the local file-based catalog
    print("💾 Seeding employees into local catalog file...")
    added = await employee_store.seed_employees_from_odoo(users)

    print(f"✅ Added {added} new employees to catalog")

    # Show some examples
    print("\n📋 Sample employees:")
    for user in users[:10]:
        print(f"   - {user['name']} (Odoo ID: {user['id']})")


if __name__ == "__main__":
    asyncio.run(main())
