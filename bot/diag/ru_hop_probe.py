"""RU→NL hop path probe — runs short VLESS client on entry toward public hop.

Unlike local hop_canary (127.0.0.1 on NL), this proves the cascade contract
entry uses: nl-exit → NL:8443 Reality+XHTTP → egress.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paramiko

from ops_events import emit as emit_ops

KIND_RU_HOP_CANARY = "ru_hop_canary"
STATE_PATH = Path("/var/log/xeno/ru_hop_canary.json")
XRAY_BIN = "/usr/local/bin/xray"


@dataclass
class RuHopResult:
    ok: bool
    detail: str
    elapsed_ms: int = 0
    exit_ip: str = ""


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def build_probe_config(*, socks_port: int = 18181) -> dict[str, Any] | None:
    uuid = _env("RELAY_UUID")
    pbk = _env("RELAY_REALITY_PUBLIC_KEY")
    sid = _env("RELAY_REALITY_SHORT_ID")
    sni = _env("RELAY_REALITY_SNI") or _env("NL_DOMAIN")
    path = _env("RELAY_PATH")
    port = int(_env("RELAY_PORT") or "8443")
    addr = _env("NL_EXIT_IP") or _env("NL_SSH_HOST")
    fp = _env("REALITY_CLIENT_FP") or "chrome"
    if not all((uuid, pbk, sid, sni, path, addr)):
        return None
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": socks_port,
                "protocol": "socks",
                "settings": {"udp": False},
            }
        ],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": addr,
                            "port": port,
                            "users": [{"id": uuid, "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "reality",
                    "xhttpSettings": {"path": path, "mode": "stream-one"},
                    "realitySettings": {
                        "serverName": sni,
                        "fingerprint": fp,
                        "publicKey": pbk,
                        "shortId": sid,
                    },
                },
            }
        ],
    }


def _ssh_ru() -> paramiko.SSHClient:
    host = _env("RU_SSH_HOST") or _env("RU_PUBLIC_IP") or _env("RU_BRIDGE_IP")
    user = _env("RU_SSH_USER") or "root"
    password = _env("RU_SSH_PASS")
    if not host or not password:
        raise RuntimeError("missing_relay_env: RU_SSH_*")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=password, timeout=30, allow_agent=False, look_for_keys=False)
    return c


def probe_ru_hop(*, timeout: float = 45.0) -> RuHopResult:
    cfg = build_probe_config()
    if not cfg:
        return RuHopResult(ok=False, detail="missing_relay_env")
    expect_ip = _env("NL_EXIT_IP")
    t0 = time.time()
    sh = f"""#!/bin/bash
set -e
pkill -f /tmp/xeno-ru-hop-probe.json 2>/dev/null || true
{XRAY_BIN} run -c /tmp/xeno-ru-hop-probe.json >/tmp/xeno-ru-hop-probe.log 2>&1 &
sleep 2
IP=$(curl -4 -sS -m 20 -x socks5h://127.0.0.1:18181 https://api.ipify.org || echo FAIL)
echo EXIT_IP=$IP
pkill -f /tmp/xeno-ru-hop-probe.json 2>/dev/null || true
"""
    try:
        c = _ssh_ru()
    except Exception as exc:
        return RuHopResult(ok=False, detail=f"ssh:{type(exc).__name__}", elapsed_ms=int((time.time() - t0) * 1000))
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/xeno-ru-hop-probe.json", "w") as f:
            f.write(json.dumps(cfg))
        with sftp.file("/tmp/xeno-ru-hop-probe.sh", "w") as f:
            f.write(sh)
        sftp.close()
        _i, o, e = c.exec_command("bash /tmp/xeno-ru-hop-probe.sh", timeout=int(timeout))
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        code = o.channel.recv_exit_status()
    finally:
        c.close()
    ms = int((time.time() - t0) * 1000)
    exit_ip = ""
    for line in out.splitlines():
        if line.startswith("EXIT_IP="):
            exit_ip = line.split("=", 1)[1].strip()
    if code != 0:
        return RuHopResult(ok=False, detail=f"rc={code}", elapsed_ms=ms, exit_ip=exit_ip)
    if not exit_ip or exit_ip == "FAIL":
        return RuHopResult(ok=False, detail="curl_fail", elapsed_ms=ms, exit_ip=exit_ip)
    if expect_ip and exit_ip != expect_ip:
        return RuHopResult(ok=False, detail=f"ip_mismatch:{exit_ip}", elapsed_ms=ms, exit_ip=exit_ip)
    return RuHopResult(ok=True, detail="ru_hop_ok", elapsed_ms=ms, exit_ip=exit_ip)


def write_state(result: RuHopResult) -> dict[str, Any]:
    prev: dict[str, Any] = {}
    if STATE_PATH.exists():
        try:
            prev = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}
    now = int(time.time())
    fails = int(prev.get("consecutive_fail") or 0)
    if result.ok:
        fails = 0
        last_ok = now
    else:
        fails += 1
        last_ok = int(prev.get("last_ok_at") or 0)
    state = {
        "ok": result.ok,
        "detail": result.detail,
        "elapsed_ms": result.elapsed_ms,
        "exit_ip": result.exit_ip,
        "consecutive_fail": fails,
        "last_ok_at": last_ok,
        "checked_at": now,
    }
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    emit_ops(
        KIND_RU_HOP_CANARY,
        ok=result.ok,
        detail=result.detail,
        consecutive_fail=fails,
        elapsed_ms=result.elapsed_ms,
    )
    return state


def read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def ru_hop_alerting(state: dict[str, Any] | None = None, *, min_fails: int = 3) -> bool:
    st = state if state is not None else read_state()
    if not st:
        return False
    return (not bool(st.get("ok"))) and int(st.get("consecutive_fail") or 0) >= min_fails


def main() -> int:
    result = probe_ru_hop()
    state = write_state(result)
    print(json.dumps(state, ensure_ascii=False))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
