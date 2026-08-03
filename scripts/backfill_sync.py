#!/usr/bin/env python3
"""One-shot: sync all active bot users to 3x-ui + RU/NL xray (run on NL)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/etc/runaway/xeno.net/bot")

from config import load_settings  # noqa: E402
from db import Database  # noqa: E402
from provision import sync_all, sync_xui_clients  # noqa: E402


def main() -> int:
    s = load_settings()
    db = Database(s.db_path)
    added, updated = sync_xui_clients(db, s)
    print(f"xui added {added} updated {updated}")
    sync_all(db, s)
    print("sync_all OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
