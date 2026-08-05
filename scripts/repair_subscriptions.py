#!/usr/bin/env python3
"""Rewrite all Happ subs (plain vless://) and sync UUIDs to RU/NL.

Run after routing/Reality/sub-format changes so every active user gets a fresh
file without rotating tokens.

  python scripts/repair_subscriptions.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot"
if str(BOT) not in sys.path:
    sys.path.insert(0, str(BOT))

from config import load_settings  # noqa: E402
from db import Database  # noqa: E402
from provision import sync_all  # noqa: E402


def main() -> int:
    settings = load_settings(require_token=False)
    db = Database(settings.db_path)
    n_users = len(db.list_active_users())
    n_extra = len(db.list_active_extra_links())
    n_issued = len(db.list_active_issued())
    print(f"repair: active users={n_users} extra_links={n_extra} issued={n_issued}")
    sync_all(db, settings, rewrite_subs=True)
    print("repair: sync_all(rewrite_subs=True) OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
