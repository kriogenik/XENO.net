#!/usr/bin/env python3
"""Restore working Happ sub: URI list + autoconnect (JSON balancer was breaking clients).

Also force sync_all and quick cascade sanity.
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


def main() -> int:
    # Prefer proven URI multi-link + Happ autoconnect meta (works on mobile Happ).
    # Full JSON balancer kept in code behind SUB_FORMAT=balancer but default links.
    save_env(SECRETS / "bridge.env", {"SUB_FORMAT": "links"})
    save_env(SECRETS / "reality.env", {"SUB_FORMAT": "links"})

    inv = load(_inventory_path())
    nl_acc = load(SECRETS / "nl-access.env")
    br = {**load(SECRETS / "reality.env"), **load(SECRETS / "bridge.env"), **load(SECRETS / "uuids.env")}

    # push bridge.env
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        inv["NL_EXIT_IP"],
        username="root",
        password=nl_acc["NL_SSH_PASS"],
        timeout=25,
        allow_agent=False,
        look_for_keys=False,
    )
    body = (SECRETS / "bridge.env").read_text(encoding="utf-8")
    sftp = c.open_sftp()
    with sftp.file("/etc/runaway/xeno.net/config/bridge.env", "w") as f:
        f.write(body if body.endswith("\n") else body + "\n")
    sftp.close()

    # upload fixed bot files from local workspace (caller should have fixed them)
    # force sync via remote after deploy expectation
    cmd = """
set -e
systemctl restart xenonet-sub xenonet-bot
sleep 1
systemctl is-active xenonet-sub xenonet-bot
/etc/runaway/xeno.net/.venv/bin/python - <<'PY'
import sys
sys.path.insert(0,'/etc/runaway/xeno.net/bot')
from config import load_settings
from db import Database
from provision import sync_all
s=load_settings()
print('SUB_FORMAT', getattr(s,'sub_format', None), 'sni', s.reality_sni)
sync_all(Database(s.db_path), s)
from pathlib import Path
p=next(Path(s.sub_root).glob('*/sub.txt'))
t=p.read_text()
print('sub_head', t[:80])
print('is_json', t.lstrip()[:1] in '[{')
import base64
try:
  raw=base64.b64decode(t).decode()
  print('b64_decoded_lines', len([x for x in raw.splitlines() if x.strip()]))
  print(raw[:200])
except Exception as e:
  print('not_b64', e)
PY
"""
    _, o, e = c.exec_command(cmd, timeout=180)
    sys.stdout.buffer.write(o.read())
    err = e.read().decode()
    if err.strip():
        print("stderr", err[:1500])
    code = o.channel.recv_exit_status()
    c.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
