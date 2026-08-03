#!/usr/bin/env python3
"""Print latest XENO connection digests from NL (/var/log/xeno/digests).

Usage:
  python scripts/show_digest.py              # latest daily (via SSH to NL)
  python scripts/show_digest.py --kind week
  python scripts/show_digest.py --local      # read local path (on NL)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
DIGEST_ROOT = "/var/log/xeno/digests"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v.strip().strip("'").strip('"')
    return env


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--kind", choices=["day", "week", "month"], default="day")
    p.add_argument("--local", action="store_true")
    args = p.parse_args()
    name = {"day": "latest-daily.md", "week": "latest-weekly.md", "month": "latest-monthly.md"}[args.kind]
    path = f"{DIGEST_ROOT}/{name}"

    if args.local:
        text = Path(path).read_text(encoding="utf-8")
        print(text)
        return 0

    nl = load_env(SECRETS / "nl-access.env")
    host = nl.get("NL_EXIT_IP") or nl.get("NL_SSH_HOST")
    if not host:
        print("NL_EXIT_IP missing in secrets/nl-access.env", file=sys.stderr)
        return 1
    password = nl.get("NL_SSH_PASS", "")
    if not password:
        print("NL_SSH_PASS missing in secrets/nl-access.env", file=sys.stderr)
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="root", password=password, timeout=30, allow_agent=False, look_for_keys=False)
    try:
        _, o, e = c.exec_command(f"test -f {path} && cat {path} || echo 'MISSING {path}'", timeout=30)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        print(out)
        if err.strip():
            print(err, file=sys.stderr)
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
