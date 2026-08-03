#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'").strip('"')
    return env


def main() -> int:
    nl = load_env(SECRETS / "nl-access.env")
    script = (ROOT / "scripts" / "backfill_sync.py").read_text(encoding="utf-8")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        nl["NL_EXIT_IP"],
        username="root",
        password=nl["NL_SSH_PASS"],
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    sftp = c.open_sftp()
    with sftp.file("/tmp/backfill_sync.py", "w") as f:
        f.write(script)
    sftp.close()
    _, o, e = c.exec_command("/etc/runaway/xeno.net/.venv/bin/python /tmp/backfill_sync.py", timeout=180)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print(err.rstrip(), file=sys.stderr)
    c.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
