#!/usr/bin/env python3
"""Soft expiry reminders to users (3d / 1d). Does not touch configs or tokens."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_settings  # noqa: E402
from db import Database  # noqa: E402
import keyboards as kb  # noqa: E402
import messages as msg  # noqa: E402
from messages import days_left  # noqa: E402

log = logging.getLogger("xenonet-expiry")


def _send_html(token: str, chat_id: int, text: str, *, reply_markup: dict | None = None) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("send fail tg=%s: %s", chat_id, exc)
        return False


def run(*, dry_run: bool = False) -> int:
    settings = load_settings()
    db = Database(settings.db_path)
    token = settings.bot_token
    sent = 0
    markup = kb.expiry_nudge_actions()
    for kind in ("d3", "d1"):
        users = db.list_users_for_expiry_nag(nag_kind=kind)
        for user in users:
            left = days_left(user.expires_at)
            text = msg.expiry_nudge(
                days=left, expires_at=user.expires_at, plan=user.plan
            )
            if dry_run:
                print(f"dry {kind} tg={user.tg_id} left={left}")
                continue
            if not token:
                log.error("BOT_TOKEN missing")
                return 1
            if _send_html(token, user.tg_id, text, reply_markup=markup):
                db.mark_expiry_nag(user.tg_id, kind)
                sent += 1
                log.info("nag %s tg=%s left=%s", kind, user.tg_id, left)
    print(f"expiry_nudge sent={sent} dry_run={dry_run}")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="XENO soft expiry nags")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
