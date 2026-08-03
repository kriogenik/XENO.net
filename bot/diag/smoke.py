"""Hourly E2E smoke: units, ports, hop freshness; write smoke/latest.md."""
from __future__ import annotations

import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paramiko

from config import Settings
from db import Database
from diag import DIGEST_ROOT


SMOKE_DIR = Path(DIGEST_ROOT) / "smoke"


def _unit_active(unit: str) -> bool:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r.returncode == 0 and (r.stdout or "").strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _tcp_ok(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ru_xray_active(settings: Settings) -> bool:
    host = os.environ.get("RU_SSH_HOST") or settings.ru_ssh_host or settings.ru_public_ip
    user = os.environ.get("RU_SSH_USER") or settings.ru_ssh_user or "root"
    password = os.environ.get("RU_SSH_PASS") or settings.ru_ssh_pass
    if not password:
        return False
    try:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(
            host,
            username=user,
            password=password,
            timeout=20,
            allow_agent=False,
            look_for_keys=False,
        )
        try:
            _, o, _ = c.exec_command("systemctl is-active xray", timeout=20)
            out = o.read().decode("utf-8", "replace").strip()
            return out == "active"
        finally:
            c.close()
    except Exception:
        return False


def run_smoke(db: Database, settings: Settings) -> dict[str, Any]:
    now = int(datetime.now(timezone.utc).timestamp())
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    checks: dict[str, Any] = {}

    checks["nl_xeno_relay"] = _unit_active("xeno-relay")
    checks["nl_xenonet_bot"] = _unit_active("xenonet-bot")
    checks["nl_xenonet_sub"] = _unit_active("xenonet-sub")
    checks["tcp_ru_443"] = _tcp_ok(settings.ru_public_ip or settings.ru_public_host, settings.client_port)
    checks["tcp_nl_relay"] = _tcp_ok(settings.nl_exit_ip, settings.relay_port)
    if settings.backups_enabled:
        checks["tcp_nl_direct"] = _tcp_ok(settings.nl_exit_ip, settings.nl_direct_port)
    checks["ru_xray"] = _ru_xray_active(settings)

    hops = db.diag_get_hop_days(day, day)
    hop_last = int(hops[0]["last_seen"]) if hops and hops[0].get("last_seen") else None
    hop_fresh = bool(hop_last and (now - hop_last) < 2 * 3600)
    checks["hop_fresh_2h"] = hop_fresh
    checks["hop_last_seen"] = hop_last

    canary = (getattr(settings, "canary_client_uuid", None) or settings.bootstrap_client_uuid or "").strip()
    checks["canary_uuid"] = canary[:8] + "…" if canary else ""

    critical = [
        "nl_xeno_relay",
        "ru_xray",
        "tcp_ru_443",
        "tcp_nl_relay",
    ]
    ok = all(bool(checks.get(k)) for k in critical)
    failed = [k for k in critical if not checks.get(k)]
    summary = "OK" if ok else "FAIL " + ",".join(failed)

    db.diag_smoke_record(ok=ok, summary=summary, detail=checks)
    _write_smoke_file(ok=ok, summary=summary, checks=checks, canary=canary)
    return {"ok": ok, "summary": summary, "checks": checks}


def _write_smoke_file(*, ok: bool, summary: str, checks: dict[str, Any], canary: str) -> Path:
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"# XENO smoke · {ts}",
        "",
        f"Result · **{'OK' if ok else 'FAIL'}** · `{summary}`",
        "",
        "## Checks",
        "",
    ]
    for k, v in checks.items():
        if k == "hop_last_seen" and isinstance(v, int):
            lines.append(f"- `{k}` · `{datetime.fromtimestamp(v, tz=timezone.utc).isoformat()}`")
        else:
            lines.append(f"- `{k}` · `{v}`")
    lines += [
        "",
        "## Notes",
        "",
        "- Critical: nl_xeno_relay, ru_xray, tcp_ru_443, tcp_nl_relay",
        "- Canary UUID for Reality experiments: "
        + (canary[:8] + "…" if canary else "unset (`CANARY_CLIENT_UUID` / bootstrap)"),
        "- No destination URLs collected",
        "",
    ]
    path = SMOKE_DIR / "latest.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    dated = SMOKE_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"
    dated.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return path
