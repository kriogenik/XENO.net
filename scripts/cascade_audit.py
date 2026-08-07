#!/usr/bin/env python3
"""Cascade audit phases 0–5: snapshot, population, RU/NL/link/client probes.

Privacy: no destinations in output. Does not restart units (phase 0 freeze).
Writes report under /tmp or --out.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paramiko

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))


def load(p: Path) -> dict[str, str]:
    d: dict[str, str] = {}
    if not p.is_file():
        return d
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip().strip("'").strip('"')
    return d


def tcp_ok(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ssh_connect(host: str, password: str, user: str = "root") -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=password, timeout=30, allow_agent=False, look_for_keys=False)
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    return code, out, err


def phase0(nl: paramiko.SSHClient, ru: paramiko.SSHClient, env: dict[str, str]) -> dict[str, Any]:
    snap: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat()}
    code, out, _ = run(
        nl,
        "systemctl is-active xeno-relay xeno-steal-nl xenonet-bot xenonet-sub x-ui 2>/dev/null; "
        "ss -lntp | grep -E ':8443|:2053|:9443|:2080' || true; "
        "cat /var/log/xeno/hop_canary.json 2>/dev/null || echo '{}'; "
        "echo '---SMOKE---'; "
        "head -n 40 /var/log/xeno/digests/smoke/latest.md 2>/dev/null || true",
    )
    snap["nl_raw"] = out[-6000:]
    code, out, _ = run(
        ru,
        "systemctl is-active xray; "
        "ss -lntp | grep ':443' || true; "
        "python3 -c \"import json;c=json.load(open('/usr/local/etc/xray/config.json'));"
        "ci=[i for i in c['inbounds'] if i.get('tag')=='client-in'][0];"
        "xo=[o for o in c['outbounds'] if o.get('tag')=='nl-exit'][0];"
        "xh=ci['streamSettings']['xhttpSettings'];"
        "print('client_mode',xh.get('mode'));"
        "print('clients',len(ci['settings'].get('clients') or []));"
        "print('nl_exit_mode',xo['streamSettings']['xhttpSettings'].get('mode'));"
        "print('nl_exit_addr',xo['settings']['vnext'][0]['address'],xo['settings']['vnext'][0]['port'])\"",
    )
    snap["ru_raw"] = out[-4000:]
    snap["tcp"] = {
        "ru_443": tcp_ok(env.get("RU_PUBLIC_IP") or env.get("RU_BRIDGE_IP") or "", 443),
        "nl_8443": tcp_ok(env.get("NL_EXIT_IP") or "", 8443),
        "nl_2053": tcp_ok(env.get("NL_EXIT_IP") or "", 2053),
    }
    snap["env_fp"] = env.get("REALITY_CLIENT_FP")
    snap["env_xhttp_mode"] = env.get("XHTTP_MODE")
    return snap


def phase1(nl: paramiko.SSHClient, ru: paramiko.SSHClient, env: dict[str, str]) -> dict[str, Any]:
    from diag.path_stats import compute_path_stats

    def remote_tail(c: paramiko.SSHClient, path: str) -> list[str]:
        code, out, _ = run(
            c,
            f"python3 - <<'PY'\n"
            f"import os\n"
            f"p={path!r}\n"
            f"maxb=8000000\n"
            f"if not os.path.exists(p): raise SystemExit\n"
            f"sz=os.path.getsize(p)\n"
            f"f=open(p,'rb')\n"
            f"\nif sz>maxb:\n"
            f"  f.seek(sz-maxb); f.readline()\n"
            f"import sys; sys.stdout.buffer.write(f.read())\n"
            f"PY",
            timeout=180,
        )
        return out.splitlines() if code == 0 else []

    ru_lines = remote_tail(ru, "/var/log/xeno/ru-access.log")
    ru_err = remote_tail(ru, "/var/log/xeno/ru-error.log")
    nl_lines = remote_tail(nl, "/var/log/xeno/nl-relay-access.log")
    nl_err = remote_tail(nl, "/var/log/xeno/nl-relay-error.log")
    stats = compute_path_stats(
        ru_lines=ru_lines,
        nl_lines=nl_lines,
        ru_err=ru_err,
        nl_err=nl_err,
        ru_ip=env.get("RU_PUBLIC_IP") or env.get("RU_BRIDGE_IP"),
    )
    # Fail classification
    w = stats["windows"]["15m"]
    fails = []
    if stats["signals"].get("cascade_ratio_break") or stats["signals"].get("canary_mask_risk"):
        fails.append("cascade_ratio_or_canary_mask")
    if stats["signals"].get("direct_migration_hint"):
        fails.append("direct_migration_hint")
    if stats["signals"].get("short_session_spike"):
        fails.append("short_session_spike")
    stats["phase1_fail"] = fails
    stats["phase1_pass"] = not fails
    return stats


def phase2_3_4(nl: paramiko.SSHClient, ru_ssh: paramiko.SSHClient, env: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # Key matrix from live configs
    _, ru_cfg, _ = run(
        ru_ssh,
        "python3 - <<'PY'\n"
        "import json\n"
        "c=json.load(open('/usr/local/etc/xray/config.json'))\n"
        "ci=[i for i in c['inbounds'] if i.get('tag')=='client-in'][0]\n"
        "xo=[o for o in c['outbounds'] if o.get('tag')=='nl-exit'][0]\n"
        "rs=ci['streamSettings']['realitySettings']\n"
        "xh=ci['streamSettings']['xhttpSettings']\n"
        "rr=xo['streamSettings']['realitySettings']\n"
        "xx=xo['streamSettings']['xhttpSettings']\n"
        "print(json.dumps({\n"
        " 'client_mode':xh.get('mode'),'client_path':xh.get('path'),\n"
        " 'client_sid':(rs.get('shortIds') or [None])[0],'client_sni':(rs.get('serverNames') or [None])[0],\n"
        " 'nl_exit_mode':xx.get('mode'),'nl_exit_path':xx.get('path'),\n"
        " 'nl_exit_sid':rr.get('shortId'),'nl_exit_pbk':rr.get('publicKey'),\n"
        " 'nl_exit_addr':xo['settings']['vnext'][0]['address'],\n"
        " 'nl_exit_port':xo['settings']['vnext'][0]['port'],\n"
        " 'nl_exit_uuid':xo['settings']['vnext'][0]['users'][0]['id'],\n"
        "}))\n"
        "PY",
    )
    _, nl_cfg, _ = run(
        nl,
        "python3 - <<'PY'\n"
        "import json\n"
        "c=json.load(open('/usr/local/etc/xray/xeno-relay.json'))\n"
        "hop=[i for i in c['inbounds'] if i.get('tag')=='xeno-relay-in'][0]\n"
        "di=[i for i in c['inbounds'] if i.get('tag')=='xeno-direct-in'][0]\n"
        "hs=hop['streamSettings']; ds=di['streamSettings']\n"
        "print(json.dumps({\n"
        " 'hop_mode':hs['xhttpSettings'].get('mode'),'hop_path':hs['xhttpSettings'].get('path'),\n"
        " 'hop_sid':(hs['realitySettings'].get('shortIds') or [None])[0],\n"
        " 'hop_dest':hs['realitySettings'].get('dest'),\n"
        " 'direct_mode':ds['xhttpSettings'].get('mode'),'direct_path':ds['xhttpSettings'].get('path'),\n"
        " 'direct_sid':(ds['realitySettings'].get('shortIds') or [None])[0],\n"
        "}))\n"
        "PY",
    )
    try:
        out["ru"] = json.loads(ru_cfg.strip().splitlines()[-1])
    except Exception:
        out["ru_raw"] = ru_cfg[-2000:]
    try:
        out["nl"] = json.loads(nl_cfg.strip().splitlines()[-1])
    except Exception:
        out["nl_raw"] = nl_cfg[-2000:]

    ru_info = out.get("ru") or {}
    nl_info = out.get("nl") or {}
    pair_ok = (
        ru_info.get("nl_exit_mode") == "stream-one"
        and nl_info.get("hop_mode") == "stream-one"
        and ru_info.get("nl_exit_path") == nl_info.get("hop_path")
        and ru_info.get("nl_exit_sid") == nl_info.get("hop_sid")
        and ru_info.get("nl_exit_uuid") == env.get("RELAY_UUID")
        and ru_info.get("nl_exit_pbk") == env.get("RELAY_REALITY_PUBLIC_KEY")
    )
    out["pair_ok"] = pair_ok
    out["steal"] = {}
    code, steal_out, _ = run(
        nl, "curl -sk --max-time 5 https://127.0.0.1:9443/ | head -c 80; echo; ss -lntp | grep 9443 || true"
    )
    out["steal"]["https_ok"] = code == 0 and bool(steal_out.strip())
    out["steal"]["raw"] = steal_out[:500]

    code, sub_grep, _ = run(
        nl,
        "grep -R 'mode=auto' /etc/runaway/xeno.net/www/sub 2>/dev/null | wc -l; "
        "grep -R 'mode=stream-one' /etc/runaway/xeno.net/www/sub 2>/dev/null | wc -l",
    )
    out["subs_mode_auto_files"] = sub_grep.strip()
    return out


def phase5_client_probe(env: dict[str, str]) -> dict[str, Any]:
    """Probe entry from this machine (often NL bias) — labeled as such."""
    from diag.ru_hop_probe import probe_ru_hop

    hop = probe_ru_hop()
    result = {
        "ru_hop_from_entry": {"ok": hop.ok, "detail": hop.detail, "exit_ip": hop.exit_ip, "ms": hop.elapsed_ms},
        "note": "ru_hop runs on entry; client Reality from Happ ISP not simulated here",
    }
    # TCP + DNS only for public entry (no fake green e2e claim)
    host = env.get("RU_PUBLIC_HOST") or env.get("RU_DOMAIN") or "ru.xenoworth.ru"
    result["dns_tcp"] = {"host": host, "tcp_443": tcp_ok(host, 443)}
    return result


def main() -> int:
    inv = load(ROOT / "inventory" / "hosts.local.env") or load(ROOT / "inventory" / "hosts.env")
    nl_acc = load(ROOT / "secrets" / "nl-access.env")
    ru_acc = load(ROOT / "secrets" / "ru-access.env")
    br = {**load(ROOT / "secrets" / "bridge.env"), **inv, **nl_acc, **ru_acc}
    for k, v in br.items():
        if k not in ("RU_SSH_PASS", "NL_SSH_PASS") and v:
            import os

            os.environ.setdefault(k, v)
    # Ensure probe env
    import os

    for k in (
        "RELAY_UUID",
        "RELAY_REALITY_PUBLIC_KEY",
        "RELAY_REALITY_SHORT_ID",
        "RELAY_REALITY_SNI",
        "RELAY_PATH",
        "RELAY_PORT",
        "NL_EXIT_IP",
        "RU_SSH_HOST",
        "RU_SSH_PASS",
        "RU_PUBLIC_IP",
        "RU_BRIDGE_IP",
        "REALITY_CLIENT_FP",
    ):
        if br.get(k):
            os.environ[k] = br[k]
    if not os.environ.get("RU_SSH_HOST"):
        os.environ["RU_SSH_HOST"] = br.get("RU_BRIDGE_IP") or br.get("RU_PUBLIC_IP") or ""

    nl_host = br.get("NL_EXIT_IP") or br.get("NL_SSH_HOST") or ""
    ru_host = br.get("RU_SSH_HOST") or br.get("RU_BRIDGE_IP") or br.get("RU_PUBLIC_IP") or ""
    nl = ssh_connect(nl_host, br["NL_SSH_PASS"])
    ru = ssh_connect(ru_host, br["RU_SSH_PASS"])
    report: dict[str, Any] = {"started": datetime.now(timezone.utc).isoformat()}
    try:
        print("=== phase0 snapshot ===", flush=True)
        report["phase0"] = phase0(nl, ru, br)
        print("=== phase1 population ===", flush=True)
        report["phase1"] = phase1(nl, ru, br)
        print(json.dumps(report["phase1"]["windows"], ensure_ascii=False, indent=2))
        print("signals", report["phase1"]["signals"], "fail", report["phase1"]["phase1_fail"])
        print("=== phase2-4 link ===", flush=True)
        report["phase2_4"] = phase2_3_4(nl, ru, br)
        print("pair_ok", report["phase2_4"].get("pair_ok"))
        print("=== phase5 probes ===", flush=True)
        report["phase5"] = phase5_client_probe(br)
        print(report["phase5"])
    finally:
        nl.close()
        ru.close()
    report["finished"] = datetime.now(timezone.utc).isoformat()
    out_path = ROOT / "scripts" / "_cascade_audit_report.json"
    # Strip nothing secret-heavy: pbk already in report — write locally gitignored pattern _*
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", out_path)
    # Exit non-zero if clear cascade fail
    fail = bool(report.get("phase1", {}).get("phase1_fail")) or not report.get("phase2_4", {}).get("pair_ok", True)
    if report.get("phase5", {}).get("ru_hop_from_entry", {}).get("ok") is False:
        fail = True
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
