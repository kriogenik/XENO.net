#!/usr/bin/env python3
"""Unit tests for ops_events JSONL writer + digest summary."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

from db import Database  # noqa: E402
from diag.digest import render_digest  # noqa: E402
from ops_events import (  # noqa: E402
    KIND_BOT_UNHANDLED,
    KIND_SMOKE_RESULT,
    KIND_STEAL_WATCH_RESTART,
    KIND_SUB_404,
    KIND_SYNC_ALL_ERROR,
    emit,
    hash_ip,
    iter_events,
    render_stability_section,
    summarize_last_hours,
    truncate_ip,
    truncate_token,
)


def test_truncate_and_hash() -> None:
    assert truncate_ip("203.0.113.45") == "203.0.113.x"
    assert truncate_ip("2001:db8:abcd:0012::1") == "2001:db8:abcd:*"
    assert hash_ip("203.0.113.45") and len(hash_ip("203.0.113.45") or "") == 12
    assert truncate_token("abcdefghij") == "abcd…"
    assert "evil" not in (truncate_token("secret-token-xyz") or "")
    print("OK truncate/hash")


def test_emit_and_summarize() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        log = Path(td) / "events.jsonl"
        now = int(time.time())
        emit(KIND_SMOKE_RESULT, log_path=log, ok=True, summary="OK")
        emit(KIND_SMOKE_RESULT, log_path=log, ok=False, summary="FAIL nl_steal_https")
        emit(
            KIND_STEAL_WATCH_RESTART,
            log_path=log,
            reason="https_9443_no_response",
            action="systemctl_restart_xeno-steal-nl",
        )
        emit(
            KIND_SUB_404,
            log_path=log,
            token_prefix="abcd…",
            ip_trunc="198.51.100.x",
            ip_hash=hash_ip("198.51.100.9"),
        )
        emit(KIND_SYNC_ALL_ERROR, log_path=log, error="RuntimeError", detail="RU xray sync failed")
        emit(KIND_BOT_UNHANDLED, log_path=log, where="update", error="ValueError")

        lines = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 6
        for line in lines:
            obj = json.loads(line)
            assert "ts" in obj and "kind" in obj
            blob = json.dumps(obj)
            assert "198.51.100.9" not in blob  # full IP not stored
            assert "secret" not in blob

        summary = summarize_last_hours(hours=24, path=log, now=now + 10)
        assert summary["smoke_ok"] == 1
        assert summary["smoke_fail"] == 1
        assert summary["steal_restarts"] == 1
        assert summary["sub_404"] == 1
        assert summary["bot_unhandled"] == 1
        assert summary["sync_error_n"] == 1

        md = "\n".join(render_stability_section(summary))
        assert "Stability / security" in md
        assert "Steal watch restarts" in md
        assert "Sub invalid token" in md
        print("OK emit/summarize")


def test_iter_kinds_filter() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        log = Path(td) / "e.jsonl"
        emit(KIND_SMOKE_RESULT, log_path=log, ok=True)
        emit(KIND_SUB_404, log_path=log, ip_trunc="1.2.3.x")
        kinds = [e["kind"] for e in iter_events(path=log, kinds=[KIND_SUB_404])]
        assert kinds == [KIND_SUB_404]
        print("OK iter filter")


def test_digest_includes_stability_section() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db = Database(Path(td) / "t.db")
        body = render_digest(db, kind="daily", day=date(2026, 8, 5))
        assert "Stability / security signals" in body
        assert "events.jsonl" in body
        print("OK digest section")


if __name__ == "__main__":
    test_truncate_and_hash()
    test_emit_and_summarize()
    test_iter_kinds_filter()
    test_digest_includes_stability_section()
    print("ALL ops_events tests OK")
