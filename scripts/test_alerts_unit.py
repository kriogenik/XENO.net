#!/usr/bin/env python3
"""Unit tests for alert state machine (stable fingerprints, no spam)."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot"
sys.path.insert(0, str(BOT))

from config import Settings  # noqa: E402
from db import Database  # noqa: E402
from diag import alerts as al  # noqa: E402


def _settings(db_path: Path) -> Settings:
    # Minimal settings object via load is heavy; build stub-like through env + load
    import os

    os.environ.setdefault("BOT_TOKEN", "1:test")
    os.environ["ADMIN_IDS"] = "111"
    os.environ.setdefault("SUB_PUBLIC_BASE", "https://example.test:2080")
    os.environ.setdefault("DB_PATH", str(db_path))
    # load_settings needs many fields — use Database only + monkeypatch settings
    from types import SimpleNamespace

    return SimpleNamespace(  # type: ignore[return-value]
        bot_token="1:test",
        admin_ids=frozenset({111}),
        db_path=db_path,
    )


def test_sni_fingerprint_stable_across_rising_count() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db = Database(Path(td) / "t.db")
        day = time.strftime("%Y-%m-%d", time.gmtime())
        db.diag_ensure_user_day(day, "tg-1")
        call_n = {"n": 0}

        def users(_a, _b):
            call_n["n"] += 1
            # evaluate_alerts may call list_user_days twice (cascade + sni)
            count = 55 if call_n["n"] <= 2 else 90
            return [{"email": "tg-1", "error_classes": json.dumps({"sni_mismatch": count})}]

        import json

        with patch.object(al, "_unit_active", return_value=True), patch.object(
            al, "_steal_https_ok", return_value=True
        ), patch.object(al, "_disk_pct", return_value=10.0), patch.object(
            db, "diag_list_user_days", side_effect=users
        ), patch.object(db, "diag_smoke_recent", return_value=[]), patch.object(
            db, "diag_get_hop_days", return_value=[{"last_seen": int(time.time())}]
        ), patch(
            "diag.hop_probe.canary_alerting", return_value=False
        ), patch(
            "diag.hop_probe.read_state", return_value={"ok": True}
        ):
            a1 = al.evaluate_alerts(db, _settings(Path(td) / "t.db"))
            a2 = al.evaluate_alerts(db, _settings(Path(td) / "t.db"))
        sni1 = [x for x in a1 if x.key == "sni_spike"][0]
        sni2 = [x for x in a2 if x.key == "sni_spike"][0]
        assert sni1.fingerprint == sni2.fingerprint == f"day:{day}"
        print("OK sni fingerprint stable")


def test_maybe_send_dedupes_same_fingerprint() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_path = Path(td) / "t.db"
        db = Database(db_path)
        settings = _settings(db_path)
        sent_texts: list[str] = []

        def fake_broadcast(s, text: str) -> None:
            sent_texts.append(text)

        alert = al.Alert(key="disk", fingerprint="high", text="XENO alert · disk 95% used on NL root.")
        with patch.object(al, "evaluate_alerts", return_value=[alert]), patch.object(
            al, "_broadcast", side_effect=fake_broadcast
        ):
            a1 = al.maybe_send_alerts(db, settings, remind_sec=99999)
            a2 = al.maybe_send_alerts(db, settings, remind_sec=99999)
        assert a1 == ["open:disk"]
        assert a2 == []
        assert len(sent_texts) == 1
        print("OK alert dedupe")


def test_recovery_message_once() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_path = Path(td) / "t.db"
        db = Database(db_path)
        settings = _settings(db_path)
        sent_texts: list[str] = []

        def fake_broadcast(s, text: str) -> None:
            sent_texts.append(text)

        open_alert = al.Alert(key="steal_https", fingerprint="hung", text="XENO alert · steal hung")
        with patch.object(al, "evaluate_alerts", return_value=[open_alert]), patch.object(
            al, "_broadcast", side_effect=fake_broadcast
        ):
            al.maybe_send_alerts(db, settings, remind_sec=99999)
        with patch.object(al, "evaluate_alerts", return_value=[]), patch.object(
            al, "_broadcast", side_effect=fake_broadcast
        ):
            actions = al.maybe_send_alerts(db, settings, remind_sec=99999)
        assert "recover:steal_https" in actions
        assert any("recover" in t for t in sent_texts)
        print("OK recovery once")


def test_smoke_needs_two_fails() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_path = Path(td) / "t.db"
        db = Database(db_path)
        now = int(time.time())
        with patch.object(al, "_unit_active", return_value=True), patch.object(
            al, "_steal_https_ok", return_value=True
        ), patch.object(al, "_disk_pct", return_value=10.0), patch.object(
            db, "diag_list_user_days", return_value=[]
        ), patch.object(
            db, "diag_get_hop_days", return_value=[{"last_seen": now}]
        ), patch.object(
            db,
            "diag_smoke_recent",
            return_value=[{"ok": 0, "summary": "FAIL x", "created_at": now}],
        ), patch(
            "diag.hop_probe.canary_alerting", return_value=False
        ), patch(
            "diag.hop_probe.read_state", return_value={"ok": True}
        ):
            one = al.evaluate_alerts(db, _settings(db_path))
        assert not any(a.key == "smoke_fail" for a in one)
        with patch.object(al, "_unit_active", return_value=True), patch.object(
            al, "_steal_https_ok", return_value=True
        ), patch.object(al, "_disk_pct", return_value=10.0), patch.object(
            db, "diag_list_user_days", return_value=[]
        ), patch.object(
            db, "diag_get_hop_days", return_value=[{"last_seen": now}]
        ), patch.object(
            db,
            "diag_smoke_recent",
            return_value=[
                {"ok": 0, "summary": "FAIL x", "created_at": now},
                {"ok": 0, "summary": "FAIL x", "created_at": now - 60},
            ],
        ), patch(
            "diag.hop_probe.canary_alerting", return_value=False
        ), patch(
            "diag.hop_probe.read_state", return_value={"ok": True}
        ):
            two = al.evaluate_alerts(db, _settings(db_path))
        assert any(a.key == "smoke_fail" and a.fingerprint == "down" for a in two)
        print("OK smoke needs two fails")


def main() -> int:
    test_sni_fingerprint_stable_across_rising_count()
    test_maybe_send_dedupes_same_fingerprint()
    test_recovery_message_once()
    test_smoke_needs_two_fails()
    print("ALL ALERT TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
