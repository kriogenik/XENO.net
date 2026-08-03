#!/usr/bin/env python3
"""Republish Happ sub with text/plain + stream-one; sync bridge.env to dual-hop secrets.
Does not touch NL 3x-ui / trading xeno-bot. Does not regenerate Reality keys.
"""
from __future__ import annotations

import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _inventory_path() -> Path:
    local = ROOT / "inventory" / "hosts.local.env"
    return local if local.is_file() else ROOT / "inventory" / "hosts.env"

sys.path.insert(0, str(ROOT / "scripts"))
from deploy_two_node import (  # noqa: E402
    build_sub,
    load_env,
    publish_sub_on_ru,
    ssh_connect,
)

SECRETS = ROOT / "secrets"
INV = _inventory_path()


def lan_bind() -> str | None:
    out = subprocess.check_output("ipconfig", text=True, encoding="cp866", errors="replace")
    for line in out.splitlines():
        m = re.search(r"IPv4[^:]*:\s*(192\.168\.\d+\.\d+)", line)
        if m and not m.group(1).startswith("192.168.96."):
            return m.group(1)
    return None


def ssh_lan(host: str, password: str):
    """paramiko connect bound to LAN so Happ TUN cannot blackhole admin SSH."""
    import paramiko

    bind = lan_bind()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(40)
    if bind:
        sock.bind((bind, 0))
        print(f"ssh bind {bind} -> {host}")
    sock.connect((host, 22))
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        host,
        username="root",
        password=password,
        sock=sock,
        allow_agent=False,
        look_for_keys=False,
        timeout=40,
        banner_timeout=40,
    )
    return c


def sync_bridge_env(inv: dict[str, str], reality: dict[str, str], uuids: dict[str, str], ru_acc: dict[str, str]) -> None:
    """Keep secrets/bridge.env aligned with dual-hop reality.env (legacy single-key landmine)."""
    lines = [
        "# Synced from reality.env / uuids.env — dual-hop XHTTP. Do not use Vision.",
        f"RU_BRIDGE_IP={inv['RU_BRIDGE_IP']}",
        f"RU_PUBLIC_IP={inv['RU_BRIDGE_IP']}",
        f"RU_SSH_HOST={inv['RU_BRIDGE_IP']}",
        f"RU_SSH_USER={ru_acc.get('RU_SSH_USER', 'root')}",
        f"RU_SSH_PASS={ru_acc.get('RU_SSH_PASS', '')}",
        f"NL_EXIT_IP={inv['NL_EXIT_IP']}",
        f"NL_PUBLIC_IP={inv['NL_EXIT_IP']}",
        f"SUB_PUBLIC_IP={inv['RU_BRIDGE_IP']}",
        f"RELAY_PORT={inv.get('RELAY_PORT', '8443')}",
        f"CLIENT_PORT={inv.get('CLIENT_PORT', '443')}",
        f"BRIDGE_REALITY_PRIVATE_KEY={reality['BRIDGE_REALITY_PRIVATE_KEY']}",
        f"BRIDGE_REALITY_PUBLIC_KEY={reality['BRIDGE_REALITY_PUBLIC_KEY']}",
        f"BRIDGE_REALITY_SHORT_ID={reality['BRIDGE_REALITY_SHORT_ID']}",
        f"BRIDGE_REALITY_SNI={reality['BRIDGE_REALITY_SNI']}",
        f"BRIDGE_REALITY_DEST={reality['BRIDGE_REALITY_DEST']}",
        f"BRIDGE_PATH={reality['BRIDGE_PATH']}",
        f"RELAY_REALITY_PRIVATE_KEY={reality['RELAY_REALITY_PRIVATE_KEY']}",
        f"RELAY_REALITY_PUBLIC_KEY={reality['RELAY_REALITY_PUBLIC_KEY']}",
        f"RELAY_REALITY_SHORT_ID={reality['RELAY_REALITY_SHORT_ID']}",
        f"RELAY_REALITY_SNI={reality['RELAY_REALITY_SNI']}",
        f"RELAY_REALITY_DEST={reality['RELAY_REALITY_DEST']}",
        f"RELAY_PATH={reality['RELAY_PATH']}",
        # Legacy aliases = bridge (client-facing) so old readers still get *something* correct for pbk
        f"REALITY_PRIVATE_KEY={reality['BRIDGE_REALITY_PRIVATE_KEY']}",
        f"REALITY_PUBLIC_KEY={reality['BRIDGE_REALITY_PUBLIC_KEY']}",
        f"REALITY_SHORT_ID={reality['BRIDGE_REALITY_SHORT_ID']}",
        f"REALITY_SNI={reality['BRIDGE_REALITY_SNI']}",
        f"REALITY_DEST={reality['BRIDGE_REALITY_DEST']}",
        f"RELAY_UUID={uuids['RELAY_UUID']}",
        f"CLIENT_UUID={uuids['CLIENT_UUID']}",
        f"BOOTSTRAP_CLIENT_UUID={uuids['CLIENT_UUID']}",
        "SUB_PORT=2096",
        "DEMO_DAYS=30",
        "DB_PATH=/etc/runaway/xeno.net/data/bot.db",
        "SUB_ROOT=/etc/runaway/xeno.net/www/sub",
        "RU_XRAY_CONFIG=/usr/local/etc/xray/config.json",
    ]
    (SECRETS / "bridge.env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Updated secrets/bridge.env from dual-hop reality.env")


def main() -> int:
    inv = load_env(INV)
    ru_acc = load_env(SECRETS / "ru-access.env")
    reality = load_env(SECRETS / "reality.env")
    uuids = load_env(SECRETS / "uuids.env")
    sec = {**reality, **uuids}

    sync_bridge_env(inv, reality, uuids, ru_acc)
    link = build_sub(inv, sec)
    token = None
    if (SECRETS / "subscription.url").exists():
        existing = (SECRETS / "subscription.url").read_text(encoding="utf-8").strip()
        if existing.startswith("http"):
            token = existing.rstrip("/").split("/")[-1] or None

    ru = ssh_lan(inv["RU_BRIDGE_IP"], ru_acc["RU_SSH_PASS"])
    url = publish_sub_on_ru(ru, inv["RU_BRIDGE_IP"], token=token)
    # verify content-type
    _, o, _ = ru.exec_command(
        f"curl -sSI http://127.0.0.1:2096/{token}/ | tr -d '\\r' | head -n 15; "
        f"curl -sS http://127.0.0.1:2096/{token}/ | head -c 80; echo"
    )
    print(o.read().decode("utf-8", "replace"))
    ru.close()

    print("\nSUB_URL", url)
    print("VLESS", link)
    print("DONE — in Happ: delete ALL old XENO subs, keep only this URL, reconnect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
