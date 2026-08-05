#!/usr/bin/env python3
"""Unit tests for hop Reality canary state machine."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

from diag.hop_probe import (  # noqa: E402
    HopProbeResult,
    canary_alerting,
    write_state,
)


def test_consecutive_fails_then_alert() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        path = Path(td) / "hop_canary.json"
        s1 = write_state(HopProbeResult(ok=False, detail="fail1"), path=path)
        assert s1["consecutive_fail"] == 1
        assert not canary_alerting(s1)
        s2 = write_state(HopProbeResult(ok=False, detail="fail2"), path=path)
        assert s2["consecutive_fail"] == 2
        assert canary_alerting(s2)
        s3 = write_state(HopProbeResult(ok=True, detail="http_200"), path=path)
        assert s3["consecutive_fail"] == 0
        assert not canary_alerting(s3)
        print("OK consecutive fails")


def test_stale_state_not_alerting() -> None:
    st = {
        "ok": False,
        "consecutive_fail": 5,
        "last_check_at": int(time.time()) - 3600,
        "detail": "old",
    }
    assert not canary_alerting(st, max_age_sec=15 * 60)
    print("OK stale ignored")


def test_soft_skip_does_not_escalate() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        path = Path(td) / "hop_canary.json"
        write_state(HopProbeResult(ok=True, detail="canary_ok"), path=path)
        s = write_state(HopProbeResult(ok=False, detail="canary_busy"), path=path)
        assert s.get("soft_skip") is True
        assert s["ok"] is True
        assert s["consecutive_fail"] == 0
        assert not canary_alerting(s)
        print("OK soft skip")


def main() -> int:
    test_consecutive_fails_then_alert()
    test_stale_state_not_alerting()
    test_soft_skip_does_not_escalate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
