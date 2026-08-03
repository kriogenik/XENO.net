#!/usr/bin/env python3
"""Stop Hysteria2 + close UFW UDP; keep binary/config/unit on disk."""
from __future__ import annotations

import sys
from pathlib import Path

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
    inv = load(_inventory_path())
    nl = load(SECRETS / "nl-access.env")
    br = load(SECRETS / "bridge.env")
    port = br.get("HY2_PORT") or inv.get("HY2_PORT") or "8444"

    save_env(SECRETS / "bridge.env", {"HY2_ENABLED": "0"})
    save_env(SECRETS / "reality.env", {"HY2_ENABLED": "0"})

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        inv["NL_EXIT_IP"],
        username="root",
        password=nl["NL_SSH_PASS"],
        timeout=25,
        allow_agent=False,
        look_for_keys=False,
    )

    # push flag into live bridge.env without corrupting newlines
    bridge_body = (SECRETS / "bridge.env").read_text(encoding="utf-8")
    if "\\n" in bridge_body and bridge_body.count("\n") < 5:
        raise RuntimeError("bridge.env looks corrupted")
    sftp = c.open_sftp()
    with sftp.file("/etc/runaway/xeno.net/config/bridge.env", "w") as f:
        f.write(bridge_body if bridge_body.endswith("\n") else bridge_body + "\n")
    sftp.close()

    remote = f"""
set -euo pipefail
PORT='{port}'
systemctl stop xeno-hy2 || true
systemctl disable xeno-hy2 || true
python3 - <<PY
import re, subprocess
port = "{port}"
out = subprocess.check_output(["ufw", "status", "numbered"], text=True, errors="replace")
nums = []
for line in out.splitlines():
    m = re.match(r"\\[\\s*(\\d+)\\]\\s+(.+)", line)
    if not m:
        continue
    n, rest = int(m.group(1)), m.group(2)
    low = rest.lower()
    # only HY2 / explicit 8444/udp comments
    if f"{{port}}/udp" in rest.replace(" ", "") or f"{{port}}/udp" in rest:
        nums.append(n)
    elif port in rest and "udp" in low and ("hy2" in low or "xeno-hy2" in low):
        nums.append(n)
print("ufw candidates", nums)
for n in sorted(set(nums), reverse=True):
    p = subprocess.run(["ufw", "--force", "delete", str(n)], capture_output=True, text=True)
    print("deleted", n, (p.stdout or p.stderr).strip())
PY
ufw reload || true
echo "--- status ---"
systemctl is-active xeno-hy2 || true
systemctl is-enabled xeno-hy2 || true
ss -lnup | grep ":$PORT" || echo "UDP $PORT not listening"
ufw status numbered | grep -E "$PORT|hy2" || echo "no ufw $PORT/hy2 rules"
test -x /usr/local/bin/hysteria && echo binary_kept
test -f /etc/hysteria/config.yaml && echo config_kept
test -f /etc/systemd/system/xeno-hy2.service && echo unit_kept
grep -E '^HY2_ENABLED=' /etc/runaway/xeno.net/config/bridge.env || true
"""
    # fix f-string double braces in remote python - use percent formatting for port only
    remote = """
set -euo pipefail
PORT='%(port)s'
systemctl stop xeno-hy2 || true
systemctl disable xeno-hy2 || true
python3 - <<'PY'
import re, subprocess
port = "%(port)s"
out = subprocess.check_output(["ufw", "status", "numbered"], text=True, errors="replace")
nums = []
for line in out.splitlines():
    m = re.match(r"\\[\\s*(\\d+)\\]\\s+(.+)", line)
    if not m:
        continue
    n, rest = int(m.group(1)), m.group(2)
    low = rest.lower()
    if f"{port}/udp" in rest.replace(" ", "") or f"{port}/udp" in rest:
        nums.append(n)
    elif port in rest and "udp" in low and ("hy2" in low or "xeno-hy2" in low):
        nums.append(n)
print("ufw candidates", nums)
for n in sorted(set(nums), reverse=True):
    p = subprocess.run(["ufw", "--force", "delete", str(n)], capture_output=True, text=True)
    print("deleted", n, (p.stdout or p.stderr).strip())
PY
ufw reload || true
echo "--- status ---"
systemctl is-active xeno-hy2 || true
systemctl is-enabled xeno-hy2 || true
ss -lnup | grep ":$PORT" || echo "UDP $PORT not listening"
ufw status numbered | grep -E "$PORT|hy2" || echo "no ufw $PORT/hy2 rules"
test -x /usr/local/bin/hysteria && echo binary_kept
test -f /etc/hysteria/config.yaml && echo config_kept
test -f /etc/systemd/system/xeno-hy2.service && echo unit_kept
grep -E '^HY2_ENABLED=' /etc/runaway/xeno.net/config/bridge.env || true
""" % {"port": port}

    print(" $ park hy2 port", port)
    _, o, e = c.exec_command(remote, timeout=120)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    sys.stdout.buffer.write(out.encode("utf-8", "replace"))
    if err.strip():
        sys.stdout.buffer.write(("stderr: " + err[:800] + "\n").encode())
    c.close()
    if code != 0:
        raise SystemExit(code)
    print("DONE: hy2 parked (stopped+disabled, ufw closed, files kept, HY2_ENABLED=0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
