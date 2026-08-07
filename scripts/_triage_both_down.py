#!/usr/bin/env python3
"""Critical triage: both RU + Direct reported dead for xenoworth."""
from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
UUID = "bb5ad439-a9bc-4d29-b4ec-e6aff4286e61"
TG = "7880252399"
SUB_URL = "https://nl.xenoworth.ru:2080/sub/af6b86cac3244c55830b9f63d38b8c16a64493f2/"


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
    c.connect(
        host,
        username="root",
        password=password,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    return out + (("\nSTDERR:\n" + err) if err.strip() else "")


def decode_sub(raw: str) -> list[dict]:
    text = raw.strip()
    try:
        pad = "=" * (-len(text) % 4)
        text = base64.b64decode(text + pad).decode("utf-8", "replace")
    except Exception:
        pass
    profiles = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("vless://"):
            continue
        u = urllib.parse.urlparse(line)
        q = dict(urllib.parse.parse_qsl(u.query))
        profiles.append(
            {
                "name": urllib.parse.unquote(u.fragment or ""),
                "uuid": u.username,
                "host": u.hostname,
                "port": u.port,
                "security": q.get("security"),
                "type": q.get("type"),
                "sni": q.get("sni"),
                "pbk": q.get("pbk"),
                "sid": q.get("sid"),
                "fp": q.get("fp"),
                "path": urllib.parse.unquote(q.get("path") or ""),
                "mode": q.get("mode"),
                "flow": q.get("flow"),
                "raw_q": q,
            }
        )
    return profiles


def main() -> int:
    inv = load(ROOT / "inventory" / "hosts.local.env")
    nl = load(ROOT / "secrets" / "nl-access.env")
    ru = load(ROOT / "secrets" / "ru-access.env")
    reality = load(ROOT / "secrets" / "reality.env")
    nl_ip = inv["NL_EXIT_IP"]
    ru_ip = inv["RU_BRIDGE_IP"]

    print("=" * 60)
    print("1) FETCH LIVE SUB")
    print("=" * 60)
    req = urllib.request.Request(SUB_URL, headers={"User-Agent": "Happ/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")
        print("HTTP", resp.status, "len", len(raw), "ct", resp.headers.get("content-type"))
    profiles = decode_sub(raw)
    print("profiles", len(profiles))
    for p in profiles:
        print(
            json.dumps(
                {
                    "name": p["name"],
                    "uuid": p["uuid"],
                    "host": p["host"],
                    "port": p["port"],
                    "type": p["type"],
                    "security": p["security"],
                    "sni": p["sni"],
                    "pbk": p["pbk"],
                    "sid": p["sid"],
                    "fp": p["fp"],
                    "path": p["path"],
                    "mode": p["mode"],
                },
                ensure_ascii=False,
            )
        )

    print("\n" + "=" * 60)
    print("2) NL TRIAGE")
    print("=" * 60)
    c_nl = conn(nl_ip, nl["NL_SSH_PASS"])
    print(
        run(
            c_nl,
            r"""
set +e
echo now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo '--- uptime/load/mem/disk ---'
uptime
free -h | head -3
df -h / /usr/local /var 2>/dev/null | head -10
echo '--- xray/nginx/ufw ---'
systemctl is-active xray 2>/dev/null; systemctl is-active xray@* 2>/dev/null
ps aux | grep -E '[x]ray|[n]ginx|[s]ub_server' | head -20
ss -lntp | grep -E ':(9443|8443|2053|2080|443|80)\s' || true
echo '--- Recv-Q SelfSteal/hop/direct ---'
ss -ltn | grep -E ':(9443|8443|2053|2080)\s' || true
echo '--- ufw ---'
ufw status numbered 2>/dev/null | head -60 || iptables -L INPUT -n | head -40
echo '--- dmesg OOM recent ---'
dmesg -T 2>/dev/null | grep -iE 'oom|killed process|out of memory' | tail -10 || true
echo '--- journal xray 1h ---'
journalctl -u xray --since '1 hour ago' --no-pager 2>/dev/null | tail -40 || true
""",
        )
    )

    print("=== NL Reality / UUID on Direct ===")
    print(
        run(
            c_nl,
            rf"""
python3 - <<'P'
import json, os, glob
from pathlib import Path

def dump_ib(path):
  print('CFG', path)
  cfg=json.loads(Path(path).read_text())
  for ib in cfg.get('inbounds',[]):
    tag=ib.get('tag'); port=ib.get('port')
    if tag in ('xeno-direct-in','xeno-relay-in') or port in (2053,8443):
      ss=ib.get('streamSettings') or {{}}
      rs=ss.get('realitySettings') or {{}}
      xs=ss.get('xhttpSettings') or {{}}
      clients=(ib.get('settings') or {{}}).get('clients') or []
      ids=[c.get('id') for c in clients]
      hit='{UUID}' in ids
      print(json.dumps({{
        'tag': tag, 'port': port, 'listen': ib.get('listen'),
        'network': ss.get('network'), 'security': ss.get('security'),
        'dest': rs.get('dest'), 'serverNames': rs.get('serverNames'),
        'shortIds': rs.get('shortIds'),
        'privateKey_prefix': (rs.get('privateKey') or '')[:10],
        'publicKey': rs.get('publicKey'),
        'xhttp': xs,
        'client_count': len(clients),
        'uuid_present': hit,
      }}, ensure_ascii=False))
  for path2 in glob.glob('/usr/local/x-ui/bin/config.json') + glob.glob('/etc/x-ui/*.json'):
    pass

for p in ['/usr/local/etc/xray/xeno-relay.json','/usr/local/etc/xray/config.json','/usr/local/x-ui/bin/config.json']:
  if os.path.isfile(p):
    try: dump_ib(p)
    except Exception as e: print('ERR', p, e)

# x-ui db clients for direct
import sqlite3
for db in ['/etc/x-ui/x-ui.db','/usr/local/x-ui/x-ui.db']:
  if not os.path.isfile(db): continue
  print('XUIDB', db)
  con=sqlite3.connect(db); con.row_factory=sqlite3.Row
  try:
    for row in con.execute('SELECT id,remark,port,protocol,settings,stream_settings,tag FROM inbounds'):
      s=row['settings'] or ''
      if '{UUID}' in s or row['port'] in (2053,8443) or 'direct' in (row['remark'] or '').lower() or 'direct' in (row['tag'] or '').lower():
        print('inbound', dict(id=row['id'], remark=row['remark'], port=row['port'], protocol=row['protocol'], tag=row['tag']))
        try:
          st=json.loads(s)
          ids=[c.get('id') for c in st.get('clients') or []]
          print('  clients', len(ids), 'uuid_hit', '{UUID}' in ids)
        except Exception as e:
          print('  settings_parse_err', e)
        try:
          ss=json.loads(row['stream_settings'] or '{{}}')
          rs=ss.get('realitySettings') or {{}}
          print('  reality', {{k: rs.get(k) for k in ('dest','serverNames','shortIds','publicKey')}})
          print('  xhttp', ss.get('xhttpSettings') or ss.get('network'))
        except Exception as e:
          print('  stream_err', e)
  except Exception as e:
    print('db_err', e)
P
""",
        )
    )

    print(
        run(
            c_nl,
            r"""
echo '--- bot.db xenoworth ---'
/etc/runaway/xeno.net/.venv/bin/python - <<'P'
import sqlite3
con=sqlite3.connect('/etc/runaway/xeno.net/data/bot.db'); con.row_factory=sqlite3.Row
for row in con.execute("SELECT * FROM users WHERE tg_id=7880252399 OR client_uuid LIKE 'bb5ad439%'"):
  print(dict(row))
print('banned', [dict(r) for r in con.execute('SELECT * FROM banned_users WHERE tg_id=7880252399')])
P
echo '--- local probe 2053/8443/9443/2080 ---'
for p in 2053 8443 9443 2080; do
  timeout 3 bash -c "echo >/dev/tcp/127.0.0.1/$p" && echo OK_local_$p || echo FAIL_local_$p
done
echo '--- nginx 9443 ---'
nginx -t 2>&1 | tail -5
curl -sk --max-time 5 -o /dev/null -w 'selfsteal_https=%{http_code}\n' https://127.0.0.1:9443/ || true
""",
        )
    )

    print("\n" + "=" * 60)
    print("3) RU TRIAGE")
    print("=" * 60)
    c_ru = conn(ru_ip, ru["RU_SSH_PASS"])
    print(
        run(
            c_ru,
            r"""
set +e
echo now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
uptime
free -h | head -3
df -h / | head -3
echo '--- xray ---'
systemctl is-active xray; ps aux | grep '[x]ray' | head -10
ss -lntp | grep -E ':(443|8443)\s' || true
echo '--- ufw ---'
ufw status numbered 2>/dev/null | head -40 || true
echo '--- tcp to NL / donors ---'
for spec in '37.220.85.76:8443' '37.220.85.76:2053' '37.220.85.76:9443' 'timeweb.cloud:443' 'dl.google.com:443' 'nl.xenoworth.ru:2053' 'ru.xenoworth.ru:443'; do
  host=${spec%:*}; port=${spec#*:}
  timeout 4 bash -c "echo >/dev/tcp/$host/$port" 2>/dev/null && echo OK_$spec || echo FAIL_$spec
done
echo '--- dmesg OOM ---'
dmesg -T 2>/dev/null | grep -iE 'oom|killed process' | tail -5 || true
echo '--- journal xray 1h ---'
journalctl -u xray --since '1 hour ago' --no-pager 2>/dev/null | tail -40 || true
""",
        )
    )

    print("=== RU Reality + UUID on client-in ===")
    print(
        run(
            c_ru,
            rf"""
python3 - <<'P'
import json, os
from pathlib import Path
cfg=json.loads(Path('/usr/local/etc/xray/config.json').read_text())
for ib in cfg.get('inbounds',[]):
  tag=ib.get('tag'); port=ib.get('port')
  if tag=='client-in' or port==443:
    ss=ib.get('streamSettings') or {{}}
    rs=ss.get('realitySettings') or {{}}
    xs=ss.get('xhttpSettings') or {{}}
    clients=(ib.get('settings') or {{}}).get('clients') or []
    ids=[c.get('id') for c in clients]
    print(json.dumps({{
      'tag': tag, 'port': port,
      'network': ss.get('network'), 'security': ss.get('security'),
      'dest': rs.get('dest'), 'serverNames': rs.get('serverNames'),
      'shortIds': rs.get('shortIds'),
      'privateKey_prefix': (rs.get('privateKey') or '')[:10],
      'publicKey': rs.get('publicKey'),
      'xhttp': xs,
      'client_count': len(clients),
      'uuid_present': '{UUID}' in ids,
      'email_hit': any('{TG}' in str(c) for c in clients),
    }}, ensure_ascii=False))
    # show matching client emails
    for c in clients:
      if c.get('id')=='{UUID}' or '{TG}' in str(c.get('email') or ''):
        print('client', {{k:c.get(k) for k in ('id','email','flow')}})
for ob in cfg.get('outbounds',[]):
  if ob.get('tag') in ('nl-exit','direct','hop'):
    ss=ob.get('streamSettings') or {{}}
    rs=ss.get('realitySettings') or {{}}
    xs=ss.get('xhttpSettings') or {{}}
    vlist=((ob.get('settings') or {{}}).get('vnext') or [None])
    vnext=vlist[0] or {{}}
    users=(vnext.get('users') or [None])
    u0=users[0] or {{}}
    print(json.dumps({{
      'outbound': ob.get('tag'),
      'addr': vnext.get('address'), 'port': vnext.get('port'),
      'uid': u0.get('id'),
      'network': ss.get('network'), 'security': ss.get('security'),
      'xhttp': xs,
      'sni': rs.get('serverName'), 'sid': rs.get('shortId'),
      'pbk': rs.get('publicKey'),
    }}, ensure_ascii=False))
P
""",
        )
    )

    # Compare sub vs secrets vs live
    print("\n" + "=" * 60)
    print("4) SUB vs SECRETS vs EXPECTED")
    print("=" * 60)
    direct_prof = next((p for p in profiles if p.get("port") == 2053), None)
    ru_prof = next((p for p in profiles if p.get("port") in (443, None) and "ru" in (p.get("host") or "").lower()), None)
    if not ru_prof:
        ru_prof = next((p for p in profiles if p.get("port") == 443), None)
    for label, p, pbk_key, sid_key, sni_key in [
        ("DIRECT", direct_prof, "DIRECT_REALITY_PUBLIC_KEY", "DIRECT_REALITY_SHORT_ID", "DIRECT_REALITY_SNI"),
        ("RU", ru_prof, "BRIDGE_REALITY_PUBLIC_KEY", "BRIDGE_REALITY_SHORT_ID", "BRIDGE_REALITY_SNI"),
    ]:
        if not p:
            print(label, "MISSING_FROM_SUB")
            continue
        print(
            label,
            {
                "uuid_ok": p["uuid"] == UUID,
                "pbk_vs_secrets": p["pbk"] == reality.get(pbk_key),
                "sid_vs_secrets": p["sid"] == reality.get(sid_key),
                "sni_vs_secrets": p["sni"] == reality.get(sni_key),
                "sub_pbk": (p["pbk"] or "")[:20],
                "sec_pbk": (reality.get(pbk_key) or "")[:20],
                "sub_sid": p["sid"],
                "sec_sid": reality.get(sid_key),
                "sub_sni": p["sni"],
                "mode": p["mode"],
                "path": p["path"],
                "fp": p["fp"],
            },
        )

    # Build E2E configs FROM SUB params (not secrets) — mirrors client
    print("\n" + "=" * 60)
    print("5) E2E FROM SUB PARAMS (client-mirror)")
    print("=" * 60)

    def socks_cfg(listen_port: int, host: str, port: int, uuid: str, p: dict) -> dict:
        return {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "listen": "127.0.0.1",
                    "port": listen_port,
                    "protocol": "socks",
                    "settings": {"udp": True},
                }
            ],
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": host,
                                "port": int(port),
                                "users": [{"id": uuid, "encryption": "none"}],
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": p.get("type") or "xhttp",
                        "security": "reality",
                        "xhttpSettings": {
                            "path": p.get("path") or "/",
                            "mode": p.get("mode") or "auto",
                        },
                        "realitySettings": {
                            "serverName": p.get("sni"),
                            "fingerprint": p.get("fp") or "chrome",
                            "publicKey": p.get("pbk"),
                            "shortId": p.get("sid"),
                        },
                    },
                }
            ],
        }

    sftp = c_ru.open_sftp()
    tests = []
    if direct_prof:
        # resolve DNS host to IP if needed — use NL IP for reliability
        dhost = nl_ip
        tests.append(("direct_from_ru", 18101, socks_cfg(18101, dhost, 2053, UUID, direct_prof)))
    if ru_prof:
        tests.append(("ru_loopback", 18102, socks_cfg(18102, "127.0.0.1", 443, UUID, ru_prof)))

    for name, port, cfg in tests:
        with sftp.file(f"/tmp/xeno-triage-{name}.json", "w") as f:
            f.write(json.dumps(cfg))

    # Also local Direct probe on NL
    if direct_prof:
        nl_cfg = socks_cfg(18103, "127.0.0.1", 2053, UUID, direct_prof)
        sftp_nl = c_nl.open_sftp()
        with sftp_nl.file("/tmp/xeno-triage-direct-local.json", "w") as f:
            f.write(json.dumps(nl_cfg))
        sftp_nl.close()

    sh = """#!/bin/bash
set +e
MARKER=bb5ad439-a9bc-4d29-b4ec-e6aff4286e61
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo MARKER_TS=$TS
pkill -f '/tmp/xeno-triage-direct_from_ru.json' 2>/dev/null
pkill -f '/tmp/xeno-triage-ru_loopback.json' 2>/dev/null
sleep 1

run_one() {
  name=$1; port=$2; cfg=$3
  echo "=== $name ==="
  /usr/local/bin/xray run -c "$cfg" >/tmp/xeno-triage-$name.log 2>&1 &
  XPID=$!
  sleep 3
  if ! kill -0 $XPID 2>/dev/null; then
    echo "xray_died_$name"
    cat /tmp/xeno-triage-$name.log
    return 1
  fi
  IP=$(curl -4 -sS --max-time 25 -x socks5h://127.0.0.1:$port https://api.ipify.org)
  RC=$?
  echo RESULT_$name IP=$IP RC=$RC
  tail -n 25 /tmp/xeno-triage-$name.log || true
  kill $XPID 2>/dev/null
  wait $XPID 2>/dev/null
  sleep 1
}

"""
    if direct_prof:
        sh += 'run_one direct_from_ru 18101 /tmp/xeno-triage-direct_from_ru.json\n'
    if ru_prof:
        sh += 'run_one ru_loopback 18102 /tmp/xeno-triage-ru_loopback.json\n'
    sh += """
echo '--- access log hits around test ---'
for f in /var/log/xray/access.log /usr/local/etc/xray/access.log /var/log/xeno/ru-access.log; do
  [ -f "$f" ] || continue
  echo FILE=$f
  grep -a 'bb5ad439' "$f" | tail -n 15 || true
done
echo DONE_E2E
"""
    with sftp.file("/tmp/xeno-triage-e2e.sh", "w") as f:
        f.write(sh)
    sftp.close()

    print(run(c_ru, "chmod +x /tmp/xeno-triage-e2e.sh && bash /tmp/xeno-triage-e2e.sh", timeout=150))

    # NL local direct + access logs
    print("=== NL local Direct E2E + access ===")
    print(
        run(
            c_nl,
            r"""
set +e
pkill -f '/tmp/xeno-triage-direct-local.json' 2>/dev/null
/usr/local/bin/xray run -c /tmp/xeno-triage-direct-local.json >/tmp/xeno-triage-direct-local.log 2>&1 &
XPID=$!
sleep 3
if ! kill -0 $XPID 2>/dev/null; then
  echo xray_died_nl_local
  cat /tmp/xeno-triage-direct-local.log
else
  IP=$(curl -4 -sS --max-time 25 -x socks5h://127.0.0.1:18103 https://api.ipify.org)
  RC=$?
  echo NL_LOCAL_DIRECT IP=$IP RC=$RC
  tail -n 25 /tmp/xeno-triage-direct-local.log
  kill $XPID 2>/dev/null
  wait $XPID 2>/dev/null
fi
echo '--- NL access log ---'
for f in /var/log/xray/access.log /usr/local/etc/xray/access.log /var/log/xeno/*access*; do
  [ -f "$f" ] || continue
  echo FILE=$f
  grep -a 'bb5ad439' "$f" | tail -n 20 || true
done
echo '--- recent access tail ---'
for f in /var/log/xray/access.log /usr/local/etc/xray/access.log; do
  [ -f "$f" ] && echo FILE=$f && tail -n 40 "$f"
done
echo DONE_NL_LOCAL
""",
            timeout=90,
        )
    )

    # Snapshot live shortIds/publicKey again for explicit compare print
    print("\n" + "=" * 60)
    print("6) FINAL KEY COMPARE")
    print("=" * 60)
    live_nl = run(
        c_nl,
        r"""
python3 - <<'P'
import json
from pathlib import Path
for path in ['/usr/local/etc/xray/xeno-relay.json','/usr/local/etc/xray/config.json']:
  p=Path(path)
  if not p.is_file(): continue
  cfg=json.loads(p.read_text())
  for ib in cfg.get('inbounds',[]):
    if ib.get('tag')=='xeno-direct-in' or ib.get('port')==2053:
      rs=ib['streamSettings']['realitySettings']
      xs=ib['streamSettings'].get('xhttpSettings') or {}
      print('NL_DIRECT', path, 'shortIds', rs.get('shortIds'), 'dest', rs.get('dest'), 'sni', rs.get('serverNames'), 'path', xs.get('path'), 'mode', xs.get('mode'), 'priv', (rs.get('privateKey') or '')[:12])
P
""",
    )
    print(live_nl)
    live_ru = run(
        c_ru,
        r"""
python3 - <<'P'
import json
from pathlib import Path
cfg=json.loads(Path('/usr/local/etc/xray/config.json').read_text())
for ib in cfg.get('inbounds',[]):
  if ib.get('tag')=='client-in':
    rs=ib['streamSettings']['realitySettings']
    xs=ib['streamSettings'].get('xhttpSettings') or {}
    print('RU_CLIENT', 'shortIds', rs.get('shortIds'), 'dest', rs.get('dest'), 'sni', rs.get('serverNames'), 'path', xs.get('path'), 'mode', xs.get('mode'), 'priv', (rs.get('privateKey') or '')[:12])
P
""",
    )
    print(live_ru)
    if direct_prof:
        print("SUB_DIRECT", direct_prof["pbk"], direct_prof["sid"], direct_prof["sni"], direct_prof["path"], direct_prof["mode"])
        print("SEC_DIRECT", reality["DIRECT_REALITY_PUBLIC_KEY"], reality["DIRECT_REALITY_SHORT_ID"], reality["DIRECT_REALITY_SNI"])
    if ru_prof:
        print("SUB_RU", ru_prof["pbk"], ru_prof["sid"], ru_prof["sni"], ru_prof["path"], ru_prof["mode"])
        print("SEC_RU", reality["BRIDGE_REALITY_PUBLIC_KEY"], reality["BRIDGE_REALITY_SHORT_ID"], reality["BRIDGE_REALITY_SNI"])

    c_nl.close()
    c_ru.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
