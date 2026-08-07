#!/usr/bin/env python3
"""Retarget RU entry Reality donor to Timeweb-colocated TLS (TSPU-friendly).

Does NOT change NL Direct (Google there is fine). Leaves hop/steal/sacred untouched.
Updates secrets + RU client-in + sync_all(rewrite_subs).
"""
from __future__ import annotations

import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"

# Co-located with Timeweb Cloud entry (~0.7ms RTT from RU VPS).
DONOR_DEST = "timeweb.cloud:443"
DONOR_SNI = "timeweb.cloud"


def _inventory_path() -> Path:
    local = ROOT / "inventory" / "hosts.local.env"
    return local if local.is_file() else ROOT / "inventory" / "hosts.env"


def load(p: Path) -> dict[str, str]:
    d: dict[str, str] = {}
    if not p.exists():
        return d
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip().strip("'").strip('"')
    return d


def save_env(path: Path, updates: dict[str, str]) -> None:
    existing = load(path)
    existing.update(updates)
    path.write_text("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n", encoding="utf-8")


def ssh(host: str, password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="root", password=password, timeout=30, allow_agent=False, look_for_keys=False)
    return c


def run(c: paramiko.SSHClient, cmd: str, check: bool = True, t: int = 300) -> str:
    print(" $", cmd[:160].replace("\n", " "))
    _, o, e = c.exec_command(cmd, timeout=t)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        sys.stdout.buffer.write((out.rstrip()[:3000] + "\n").encode("utf-8", "replace"))
    if check and code != 0:
        raise RuntimeError(f"fail {code}: {err[:1500]}")
    return out


def sftp_write(c: paramiko.SSHClient, remote: str, data: str) -> None:
    sftp = c.open_sftp()
    with sftp.file(remote, "w") as f:
        f.write(data if data.endswith("\n") else data + "\n")
    sftp.close()


def main() -> int:
    inv = load(_inventory_path())
    nl_acc = load(SECRETS / "nl-access.env")
    ru_acc = load(SECRETS / "ru-access.env")

    updates = {
        "BRIDGE_REALITY_SNI": DONOR_SNI,
        "BRIDGE_REALITY_DEST": DONOR_DEST,
        # keep DIRECT on Google — NL Direct works for users
    }
    save_env(SECRETS / "reality.env", updates)
    save_env(SECRETS / "bridge.env", updates)
    save_env(_inventory_path(), {"REALITY_SNI": DONOR_SNI, "REALITY_DEST": DONOR_DEST})

    nl_host = inv.get("NL_EXIT_IP") or nl_acc.get("NL_EXIT_IP")
    ru_host = inv.get("RU_BRIDGE_IP") or ru_acc.get("RU_BRIDGE_IP")
    nl = ssh(nl_host, nl_acc["NL_SSH_PASS"])
    ru = ssh(ru_host, ru_acc["RU_SSH_PASS"])

    run(ru, f"python3 -c \"import socket,ssl;s=socket.create_connection(('timeweb.cloud',443),3);c=ssl.create_default_context();ss=c.wrap_socket(s,server_hostname='timeweb.cloud');print('donor_ok',ss.version());ss.close()\"")

    # Push secrets + xray_sync (serverNames expansion) before sync
    sftp_write(nl, "/etc/runaway/xeno.net/config/bridge.env", (SECRETS / "bridge.env").read_text(encoding="utf-8"))
    for name in ("xray_sync.py", "config.py", "provision.py"):
        sftp_write(
            nl,
            f"/etc/runaway/xeno.net/bot/{name}",
            (ROOT / "bot" / name).read_text(encoding="utf-8"),
        )

    # Force RU rebuild: sync_all structural change on dest/sni → restart
    out = run(
        nl,
        "cd /etc/runaway/xeno.net/bot && set -a && "
        ". /etc/runaway/xeno.net/config/bridge.env && "
        ". /etc/runaway/xeno.net/config/bot.env && "
        ". /etc/runaway/xeno.net/config/ru-ssh.env && set +a && "
        "/etc/runaway/xeno.net/.venv/bin/python - <<'PY'\n"
        "from config import load_settings\n"
        "from db import Database\n"
        "from provision import sync_all\n"
        "s=load_settings()\n"
        "print('BRIDGE', s.reality_sni, s.reality_dest, 'mode', s.xhttp_mode)\n"
        "print('DIRECT', s.direct_sni, s.direct_path)\n"
        "db=Database(s.db_path)\n"
        "sync_all(db, s, rewrite_subs=True)\n"
        "print('SYNC_OK')\n"
        "PY",
        t=300,
    )
    if "SYNC_OK" not in out:
        raise RuntimeError("sync_all did not report SYNC_OK")

    # Verify live RU inbound
    run(
        ru,
        "python3 - <<'PY'\n"
        "import json\n"
        "c=json.load(open('/usr/local/etc/xray/config.json'))\n"
        "ci=[i for i in c['inbounds'] if i.get('tag')=='client-in'][0]\n"
        "rs=ci['streamSettings']['realitySettings']\n"
        "xh=ci['streamSettings']['xhttpSettings']\n"
        "print('dest', rs.get('dest'))\n"
        "print('names', rs.get('serverNames'))\n"
        "print('mode', xh.get('mode'))\n"
        "assert rs.get('dest')=='timeweb.cloud:443', rs.get('dest')\n"
        "assert 'timeweb.cloud' in (rs.get('serverNames') or []), rs.get('serverNames')\n"
        "assert xh.get('mode')=='stream-one'\n"
        "print('RU_INBOUND_OK')\n"
        "PY",
    )
    run(ru, "systemctl is-active xray")
    run(nl, "systemctl restart xenonet-bot xenonet-sub && sleep 1 && systemctl is-active xenonet-bot xenonet-sub")

    # Sample sub line
    run(
        nl,
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "root=Path('/etc/runaway/xeno.net/www/sub')\n"
        "for p in sorted(root.rglob('*'))[:1]:\n"
        "  pass\n"
        "# pick any token dir with index\n"
        "found=None\n"
        "for p in root.iterdir():\n"
        "  if p.is_dir():\n"
        "    for f in p.iterdir():\n"
        "      if f.is_file():\n"
        "        t=f.read_text(encoding='utf-8',errors='replace')\n"
        "        if 'vless://' in t:\n"
        "          found=t; break\n"
        "  if found: break\n"
        "if not found:\n"
        "  print('NO_SUB'); raise SystemExit(1)\n"
        "line=found.splitlines()[0]\n"
        "assert 'sni=timeweb.cloud' in line or 'sni=timeweb' in line, line[:200]\n"
        "assert 'mode=stream-one' in line\n"
        "assert 'dl.google.com' not in line.split('#')[0]\n"
        "print('SUB_RU_OK', line[:120])\n"
        "PY",
    )

    nl.close()
    ru.close()
    print(f"\n=== RETARGET RU DONE dest={DONOR_DEST} sni={DONOR_SNI} (Direct unchanged) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
