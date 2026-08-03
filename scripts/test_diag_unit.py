#!/usr/bin/env python3
"""Unit tests for privacy-safe diag parse + digest (no SSH)."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

from db import Database  # noqa: E402
from diag.classify import REALITY_HANDSHAKE, SNI_MISMATCH, classify_error_line  # noqa: E402
from diag.digest import emit_digest, render_digest  # noqa: E402
from diag.parse import mask_ip, parse_access_line, parse_error_line  # noqa: E402
from diag.stats import _parse_stats_output  # noqa: E402


def test_parse_access_strips_dest() -> None:
    line = (
        "2026/08/02 10:11:12.123456 from 203.0.113.10:54321 accepted "
        "tcp:evil.example.com:443 email: tg-10001 [client-in >> nl-exit]"
    )
    ev = parse_access_line(line)
    assert ev is not None
    assert ev.email == "tg-10001"
    assert ev.action == "accepted"
    assert ev.src_ip_masked == "203.0.113.0/24"
    blob = json.dumps(ev.__dict__)
    assert "evil.example.com" not in blob
    assert "tcp:" not in blob
    print("OK access parse strips dest")


def test_parse_hop() -> None:
    line = (
        "2026/08/02 10:11:13.000000 from 203.0.113.20:44444 accepted "
        "tcp:1.1.1.1:443 email: xeno-relay-hop [xeno-relay-in >> direct]"
    )
    ev = parse_access_line(line)
    assert ev and ev.hop and ev.email == "xeno-relay-hop"
    print("OK hop parse")


def test_classify() -> None:
    assert classify_error_line("server name mismatch: www.microsoft.com") == SNI_MISMATCH
    assert classify_error_line("REALITY: handshake did not complete successfully") == REALITY_HANDSHAKE
    print("OK classify")


def test_mask_ip() -> None:
    assert mask_ip("8.8.8.8") == "8.8.8.0/24"
    print("OK mask ip")


def test_digest_render() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Database(Path(tmp) / "t.db")
        db.diag_bump_user(
            day="2026-08-02",
            email="tg-1",
            accepts_ru=3,
            last_seen_ru=1722590000,
            src_ip_masked="1.2.3.0/24",
        )
        db.diag_bump_hop(day="2026-08-02", accepts=10, last_seen=1722590000)
        body = render_digest(db, kind="daily", day=date(2026, 8, 2))
        assert "evil" not in body
        assert "tg-1" in body
        assert "xeno-relay-hop" in body
        assert "Hints" in body
        root = Path(tmp) / "digests"
        path = emit_digest(db, kind="daily", day=date(2026, 8, 2), root=root)
        assert path.exists()
        assert (root / "latest-daily.md").exists()
        print("OK digest render")


def test_stats_parse() -> None:
    text = '''
    {"name": "user>>>tg-1>>>traffic>>>uplink", "value": 100}
    {"name": "user>>>tg-1>>>traffic>>>downlink", "value": 200}
    '''
    d = _parse_stats_output(text)
    assert d["tg-1"]["up"] == 100 and d["tg-1"]["down"] == 200
    print("OK stats parse")


def test_error_line() -> None:
    line = "2026/08/02 10:00:00.1 failed to process for tg-10002: server name mismatch: x"
    ev = parse_error_line(line)
    assert ev and ev.email == "tg-10002" and ev.error_class == SNI_MISMATCH
    assert "mismatch" in (ev.error_class or "")
    print("OK error line")


if __name__ == "__main__":
    test_parse_access_strips_dest()
    test_parse_hop()
    test_classify()
    test_mask_ip()
    test_digest_render()
    test_stats_parse()
    test_error_line()
    print("ALL PASSED")
