#!/usr/bin/env python3
"""Final status: RU path + whether xenoworth still connects right now."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
UUID = "bb5ad439-a9bc-4d29-b4ec-e6aff4286e61"
PBK_D = "cfBj0dgzJKfdcDvVkYLgqHDomGAtfAfVy0sOR2XzFyU"
SID_D = "4b85406b7e4f9f9d"
PBK_R = "wxqFCNpvFmJpCe7ZLl_gF4lMnw-RZS2v4qy9WL2MfBU"
SID_R = "e7dfb849e7035923"


def load(p: Path) -> dict[str, str]:
    d: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip().strip("'").strip('"')
    return d


def conn(host: str, password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="root", password=password, timeout=30, allow_agent=False, look_for_keys=False)
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 90) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    return o.read().decode("utf-8", "replace") + e.read().decode("utf-8", "replace")


def main() -> int:
    inv = load(ROOT / "inventory" / "hosts.local.env")
    nl = load(ROOT / "secrets" / "nl-access.env")
    ru = load(ROOT / "secrets" / "ru-access.env")
    c_nl = conn(inv["NL_EXIT_IP"], nl["NL_SSH_PASS"])
    c_ru = conn(inv["RU_BRIDGE_IP"], ru["RU_SSH_PASS"])

    print("=== RU access for xenoworth ===")
    print(
        run(
            c_ru,
            r"""
ls -la /var/log/xeno/ 2>/dev/null
echo '--- uuid hits ---'
grep -a 'bb5ad439\|tg-7880252399' /var/log/xeno/ru-access.log 2>/dev/null | tail -30
echo '--- tail ---'
tail -n 25 /var/log/xeno/ru-access.log 2>/dev/null
echo '--- error ---'
tail -n 25 /var/log/xeno/ru-error.log 2>/dev/null
echo '--- journal reject ---'
journalctl -u xray --since '2 hours ago' --no-pager 2>/dev/null | grep -iE 'reject|fail|bb5ad439|7880252399' | tail -20
""",
        )
    )

    print("=== xenoworth source IPs last hour on Direct ===")
    print(
        run(
            c_nl,
            r"""
python3 - <<'P'
from collections import Counter
from datetime import datetime
hits=[]
with open('/var/log/xeno/nl-relay-access.log','r',errors='replace') as f:
  for line in f:
    if 'tg-7880252399' not in line: continue
    if '2026/08/07 18:' not in line and '2026/08/07 19:' not in line: continue
    # from IP
    try:
      ip=line.split('from ')[1].split(':')[0].replace('tcp:','')
    except Exception:
      ip='?'
    hits.append((line[:19], ip, 'accepted' in line))
print('count', len(hits))
print('ips', Counter(h[1] for h in hits))
print('last10:')
for h in hits[-10]:
  print(h)
P
echo '--- ESTAB from known user IPs ---'
ss -tnp | grep ':2053' | grep -E '88.201.206.39|81.9.49.150' || echo 'no_estab_now'
echo '--- hop canary / path stats ---'
cat /var/log/xeno/ru_hop_canary.json 2>/dev/null; echo
cat /var/log/xeno/hop_canary.json 2>/dev/null; echo
python3 -c 'import json; print(json.dumps(json.load(open("/var/log/xeno/path_stats.json")), indent=2)[:2000])'
""",
        )
    )

    # Write configs via sftp, run e2e without embedding curl in powershell
    direct = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": 18201, "protocol": "socks", "settings": {"udp": True}}],
        "outbounds": [{
            "protocol": "vless",
            "settings": {"vnext": [{"address": inv["NL_EXIT_IP"], "port": 2053, "users": [{"id": UUID, "encryption": "none"}]}]},
            "streamSettings": {
                "network": "xhttp", "security": "reality",
                "xhttpSettings": {"path": "/", "mode": "stream-one"},
                "realitySettings": {"serverName": "dl.google.com", "fingerprint": "chrome", "publicKey": PBK_D, "shortId": SID_D},
            },
        }],
    }
    ru_cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": 18202, "protocol": "socks", "settings": {"udp": True}}],
        "outbounds": [{
            "protocol": "vless",
            "settings": {"vnext": [{"address": "127.0.0.1", "port": 443, "users": [{"id": UUID, "encryption": "none"}]}]},
            "streamSettings": {
                "network": "xhttp", "security": "reality",
                "xhttpSettings": {"path": "/", "mode": "stream-one"},
                "realitySettings": {"serverName": "timeweb.cloud", "fingerprint": "chrome", "publicKey": PBK_R, "shortId": SID_R},
            },
        }],
    }
    nl_local = json.loads(json.dumps(direct))
    nl_local["inbounds"][0]["port"] = 18203
    nl_local["outbounds"][0]["settings"]["vnext"][0]["address"] = "127.0.0.1"

    sftp = c_ru.open_sftp()
    with sftp.file("/tmp/xeno-final-direct.json", "w") as f:
        f.write(json.dumps(direct))
    with sftp.file("/tmp/xeno-final-ru.json", "w") as f:
        f.write(json.dumps(ru_cfg))
    with sftp.file("/tmp/xeno-final-e2e.sh", "w") as f:
        f.write(
            """#!/bin/bash
set +e
run() {
  name=$1; port=$2; cfg=$3
  pkill -f "$cfg" >/dev/null 2>&1
  /usr/local/bin/xray run -c "$cfg" >/tmp/xeno-final-$name.log 2>&1 &
  pid=$!
  sleep 3
  ip=$(curl -4 -sS --max-time 20 -x socks5h://127.0.0.1:$port https://api.ipify.org)
  rc=$?
  echo RESULT $name IP=$ip RC=$rc
  kill $pid >/dev/null 2>&1
  wait $pid >/dev/null 2>&1
}
run direct 18201 /tmp/xeno-final-direct.json
run ru 18202 /tmp/xeno-final-ru.json
echo '--- ru access after ---'
grep -a 'bb5ad439\\|tg-7880252399' /var/log/xeno/ru-access.log | tail -5
"""
        )
    sftp.close()

    sftp = c_nl.open_sftp()
    with sftp.file("/tmp/xeno-final-nllocal.json", "w") as f:
        f.write(json.dumps(nl_local))
    with sftp.file("/tmp/xeno-final-nl.sh", "w") as f:
        f.write(
            """#!/bin/bash
set +e
pkill -f /tmp/xeno-final-nllocal.json >/dev/null 2>&1
/usr/local/bin/xray run -c /tmp/xeno-final-nllocal.json >/tmp/xeno-final-nllocal.log 2>&1 &
pid=$!
sleep 3
ip=$(curl -4 -sS --max-time 20 -x socks5h://127.0.0.1:18203 https://api.ipify.org)
echo RESULT nllocal IP=$ip RC=$?
kill $pid >/dev/null 2>&1
wait $pid >/dev/null 2>&1
echo '--- access after nllocal ---'
grep -a 'tg-7880252399' /var/log/xeno/nl-relay-access.log | tail -3
"""
        )
    sftp.close()

    print("=== FINAL E2E from RU ===")
    print(run(c_ru, "bash /tmp/xeno-final-e2e.sh", timeout=90))
    print("=== FINAL E2E NL local ===")
    print(run(c_nl, "bash /tmp/xeno-final-nl.sh", timeout=60))

    print("=== access after our tests ===")
    print(
        run(
            c_nl,
            r"""
grep -a 'tg-7880252399' /var/log/xeno/nl-relay-access.log | tail -8
""",
        )
    )

    c_nl.close()
    c_ru.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
