#!/usr/bin/env python3
"""Stabilize cascade for clients: RU-only sub, force sync_all, keep hop healthy.

Does not tear down Direct/HY2 services (harmless to hop) but removes them from
published client subscriptions until backups are validated separately.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

import paramiko

ROOT = Path(__file__).resolve().parents[1]


def _inventory_path() -> Path:
    local = ROOT / "inventory" / "hosts.local.env"
    return local if local.is_file() else ROOT / "inventory" / "hosts.env"

SECRETS = ROOT / "secrets"


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


def run(c: paramiko.SSHClient, cmd: str, check: bool = True, t: int = 180) -> str:
    print(" $", cmd[:140].replace("\n", " "))
    _, o, e = c.exec_command(cmd, timeout=t)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        sys.stdout.buffer.write((out.rstrip()[:2500] + "\n").encode("utf-8", "replace"))
    if check and code != 0:
        raise RuntimeError(f"fail {code}: {err[:1200]}")
    return out


def sftp_write(c: paramiko.SSHClient, remote: str, data: str) -> None:
    sftp = c.open_sftp()
    with sftp.file(remote, "w") as f:
        f.write(data if data.endswith("\n") else data + "\n")
    sftp.close()


def build_ru_only_sub(inv: dict, sec: dict) -> str:
    client = sec.get("CLIENT_UUID") or sec.get("BOOTSTRAP_CLIENT_UUID")
    ru = inv.get("RU_DOMAIN") or inv["RU_BRIDGE_IP"]
    port = inv.get("CLIENT_PORT", "443")
    q = urlencode(
        {
            "encryption": "none",
            "security": "reality",
            "sni": sec["BRIDGE_REALITY_SNI"],
            "fp": "chrome",
            "pbk": sec["BRIDGE_REALITY_PUBLIC_KEY"],
            "sid": sec["BRIDGE_REALITY_SHORT_ID"],
            "type": "xhttp",
            "path": sec["BRIDGE_PATH"],
            "mode": "auto",
        }
    )
    link = f"vless://{client}@{ru}:{port}?{q}#{quote('🇷🇺XENO RU')}"
    text = link + "\n"
    b64 = base64.b64encode(text.encode()).decode()
    (SECRETS / "subscription.txt").write_text(text, encoding="utf-8")
    (SECRETS / "subscription.base64").write_text(b64 + "\n", encoding="utf-8")
    return link


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    inv = load(_inventory_path())
    nl_acc = load(SECRETS / "nl-access.env")
    ru_acc = load(SECRETS / "ru-access.env")
    sec = {}
    for name in ("uuids.env", "reality.env", "bridge.env"):
        sec.update(load(SECRETS / name))

    # Do not advertise backups in client subs until cascade UX is solid
    save_env(SECRETS / "bridge.env", {"BACKUPS_ENABLED": "0"})
    sec["BACKUPS_ENABLED"] = "0"

    link = build_ru_only_sub(inv, sec)
    print("RU-only bootstrap:", link[:100], "...")

    nl = ssh(inv["NL_EXIT_IP"], nl_acc["NL_SSH_PASS"])
    ru = ssh(inv["RU_BRIDGE_IP"], ru_acc["RU_SSH_PASS"])

    # Health: hop units
    run(nl, "systemctl is-active x-ui xeno-bot xeno-relay xeno-steal-nl")
    run(ru, "systemctl is-active xray")

    # Ensure bridge.env on NL disables backups for bot
    bridge_local = (SECRETS / "bridge.env").read_text(encoding="utf-8")
    sftp_write(nl, "/etc/runaway/xeno.net/config/bridge.env", bridge_local)
    run(nl, "chmod 600 /etc/runaway/xeno.net/config/bridge.env")

    # Republish RU bootstrap sub if URL exists
    url_path = SECRETS / "subscription.url"
    if url_path.exists():
        url = url_path.read_text(encoding="utf-8").strip()
        if ":2096/" in url:
            token = url.rstrip("/").split("/")[-1]
            b64 = (SECRETS / "subscription.base64").read_text(encoding="utf-8").strip() + "\n"
            run(ru, f"mkdir -p /var/www/xeno-sub/{token}")
            for name in ("sub.txt", "index.txt", "index.html"):
                sftp_write(ru, f"/var/www/xeno-sub/{token}/{name}", b64)
            run(ru, "systemctl restart xeno-sub || true", check=False)
            print("republished", url)

    # Soft restart hop path (not sacred units)
    run(nl, "systemctl restart xeno-steal-nl xeno-relay && sleep 1 && systemctl is-active xeno-steal-nl xeno-relay")
    run(ru, "systemctl restart xray && sleep 1 && systemctl is-active xray")

    # Force bot sync_all + rewrite user subs (uses code already on server)
    run(
        nl,
        "/etc/runaway/xeno.net/.venv/bin/python - <<'PY'\n"
        "import sys\n"
        "sys.path.insert(0, '/etc/runaway/xeno.net/bot')\n"
        "from config import load_settings\n"
        "from db import Database\n"
        "from provision import sync_all, profiles_for, vless_for\n"
        "from xray_sync import write_user_sub_file\n"
        "s = load_settings(require_token=True)\n"
        "print('backups_enabled', s.backups_enabled)\n"
        "print('entry', s.ru_public_host, s.bridge_path, s.reality_sni)\n"
        "db = Database(s.db_path)\n"
        "sync_all(db, s)\n"
        "for u in db.list_active_users():\n"
        "  links = profiles_for(s, u) if hasattr(__import__('provision'), 'profiles_for') else [vless_for(s, u)]\n"
        "  write_user_sub_file(s.sub_root, u.sub_token, links)\n"
        "  print('user', u.tg_id, 'profiles', len(links) if isinstance(links, list) else 1)\n"
        "  print((links[0] if isinstance(links, list) else links)[:120])\n"
        "print('SYNC_OK')\n"
        "PY",
    )

    run(nl, "systemctl restart xenonet-bot xenonet-sub && sleep 1 && systemctl is-active xenonet-bot xenonet-sub")
    # sacred still up
    run(nl, "systemctl is-active x-ui xeno-bot")

    nl.close()
    ru.close()
    print("STABILIZE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
