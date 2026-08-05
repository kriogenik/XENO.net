#!/usr/bin/env python3
"""SelfSteal HTTPS healthcheck — restart + ops event if :9443 wedged.

Invoked by xenonet-steal-watch.service every ~2 min.

Product SelfSteal is dedicated nginx on ``127.0.0.1:9443`` (not Python).
If HTTPS probe fails, restart ``xeno-steal-nl``; if still dead, free the port
and start again (covers leftover listeners after a bad migrate).
"""
from __future__ import annotations

import subprocess
import sys
import time
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


def _recv_q() -> str | None:
    try:
        r = subprocess.run(
            ["ss", "-ltn", "sport", "=", ":9443"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in (r.stdout or "").splitlines():
            if ":9443" in line:
                return " ".join(line.split())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None


def _force_free_port() -> None:
    """Kill listeners on 9443 if unit restart left a wedged python."""
    try:
        subprocess.run(
            ["bash", "-c", "fuser -k 9443/tcp 2>/dev/null || true"],
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def main() -> int:
    if https_ok():
        return 0

    ss_line = _recv_q()
    emit(
        KIND_STEAL_WATCH_RESTART,
        reason="https_9443_no_response",
        action=f"systemctl_restart_{UNIT}",
        listen=LISTEN,
        ss=ss_line,
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

    time.sleep(2)
    if https_ok():
        return 0

    emit(
        KIND_STEAL_WATCH_RESTART,
        reason="https_still_down_after_restart",
        action="fuser_kill_9443_then_start",
        listen=LISTEN,
        ss=_recv_q(),
    )
    _force_free_port()
    time.sleep(1)
    subprocess.run(["systemctl", "start", UNIT], check=False, timeout=60)
    time.sleep(2)
    ok = https_ok()
    if not ok:
        emit(
            KIND_STEAL_WATCH_RESTART,
            reason="https_still_down_after_force",
            action="give_up",
            listen=LISTEN,
            ss=_recv_q(),
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
