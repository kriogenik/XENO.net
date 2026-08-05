#!/usr/bin/env python3
"""Hop Reality canary — probe :8443 every few minutes, record state + ops event.

Invoked by xenonet-hop-watch.service. Does not auto-restart xeno-relay
(steal_watch owns :9443 restarts). Alerts read hop_canary.json via diag.alerts.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from diag.hop_probe import probe_hop_reality, write_state  # noqa: E402
from ops_events import KIND_HOP_CANARY, emit  # noqa: E402


def main() -> int:
    result = probe_hop_reality(timeout=20.0)
    state = write_state(result)
    emit(
        KIND_HOP_CANARY,
        ok=bool(result.ok),
        detail=result.detail,
        consecutive_fail=int(state.get("consecutive_fail") or 0),
        elapsed_ms=int(result.elapsed_ms or 0),
    )
    # Always 0 — failure is tracked in state/events/alerts; avoid timer spam.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
