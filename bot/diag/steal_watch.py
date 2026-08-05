#!/usr/bin/env python3
"""SelfSteal HTTPS healthcheck — restart + ops event if :9443 wedged.

Invoked by xenonet-steal-watch.service every ~2 min.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Allow import when WorkingDirectory is bot/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ops_events import KIND_STEAL_WATCH_RESTART, emit  # noqa: E402

LISTEN = "127.0.0.1:9443"
UNIT = "xeno-steal-nl"


def https_ok(*, timeout: float = 5.0) -> bool:
    try:
        r = subprocess.run(
            ["curl", "-sk", "--max-time", str(int(timeout)), f"https://{LISTEN}/"],
            capture_output=True,
            timeout=timeout + 2,
        )
        return r.returncode == 0 and bool((r.stdout or b"").strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def main() -> int:
    if https_ok():
        return 0
    emit(
        KIND_STEAL_WATCH_RESTART,
        reason="https_9443_no_response",
        action=f"systemctl_restart_{UNIT}",
        listen=LISTEN,
    )
    try:
        subprocess.run(["systemctl", "restart", UNIT], check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        emit(
            KIND_STEAL_WATCH_RESTART,
            reason="restart_failed",
            action=f"systemctl_restart_{UNIT}",
            detail=str(exc)[:120],
            listen=LISTEN,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
