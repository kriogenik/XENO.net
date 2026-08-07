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
        st = None
        for i in range(4):
            st = write_state(HopProbeResult(ok=False, detail=f"hard_fail_{i}"), path=path)
            assert st["consecutive_fail"] == i + 1
            assert not canary_alerting(st)
        st = write_state(HopProbeResult(ok=False, detail="hard_fail_4"), path=path)
        assert st["consecutive_fail"] == 5
        assert canary_alerting(st)
        st = write_state(HopProbeResult(ok=True, detail="canary_ok"), path=path)
        assert st["consecutive_fail"] == 0
        assert not canary_alerting(st)
        print("OK consecutive fails need 5")


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
        print("OK soft skip busy")


def test_transient_curl56_soft_after_ok() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        path = Path(td) / "hop_canary.json"
        write_state(HopProbeResult(ok=True, detail="canary_ok"), path=path)
        s = write_state(
            HopProbeResult(ok=False, detail="curl_rc=56_body=b''"), path=path
        )
        assert s.get("soft_skip") is True
        assert s["ok"] is True
        assert s["consecutive_fail"] == 0
        assert not canary_alerting(s)
        print("OK soft skip curl56")


def main() -> int:
    test_consecutive_fails_then_alert()
    test_stale_state_not_alerting()
    test_soft_skip_does_not_escalate()
    test_transient_curl56_soft_after_ok()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
