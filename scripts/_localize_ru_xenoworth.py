#!/usr/bin/env python3
"""Localize xenoworth RU failure (A–F) + Direct slowness notes.

SSH: try RU direct; on banner failure jump via NL direct-tcpip.
Does not rotate UUID / retarget Reality.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import paramiko

ROOT = Path(__file__).resolve().parents[1]
TG = "7880252399"
TOKEN = "af6b86cac3244c55830b9f63d38b8c16a64493f2"
UUID = "bb5ad439-a9bc-4d29-b4ec-e6aff4286e61"
MSK = timezone(timedelta(hours=3))
UTC = timezone.utc


def load(p: Path) -> dict[str, str]:
    d: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip().strip("'").strip('"')
    return d


def ssh_direct(host: str, password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        host,
        username="root",
        password=password,
        timeout=30,
        banner_timeout=60,
        auth_timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    return c


def ssh_via(nl: paramiko.SSHClient, host: str, password: str) -> paramiko.SSHClient:
    transport = nl.get_transport()
    assert transport is not None
    ch = transport.open_channel("direct-tcpip", (host, 22), ("127.0.0.1", 0), timeout=30)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        host,
        username="root",
        password=password,
        sock=ch,
        timeout=30,
        banner_timeout=60,
        allow_agent=False,
        look_for_keys=False,
    )
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 180) -> tuple[int, str, str]:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    return o.channel.recv_exit_status(), out, err


def parse_vless(line: str) -> dict:
    u = urlparse(line.strip())
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    return {
        "uuid": u.username,
        "host": u.hostname,
        "port": u.port,
        "fragment": unquote(u.fragment or ""),
        **q,
    }


def parse_ts(line: str) -> datetime | None:
    m = re.search(r"(\d{4}[/-]\d{2}[/-]\d{2}[ T]\d{2}:\d{2}:\d{2})", line)
    if not m:
        return None
    s = m.group(1).replace("T", " ").replace("/", "-")
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except Exception:
        return None


def msk_s(ts: datetime | None) -> str:
    if not ts:
        return "?"
    return ts.astimezone(MSK).strftime("%Y-%m-%d %H:%M:%S MSK")


REMOTE_RU = r'''
python3 - <<'PY'
import json, os, re, socket, time, ssl
from datetime import datetime, timedelta, timezone
from collections import Counter

UTC = timezone.utc
MSK = timezone(timedelta(hours=3))
now = datetime.now(UTC)
cut2 = now - timedelta(hours=2)
cut24 = now - timedelta(hours=24)
UUID = "bb5ad439-a9bc-4d29-b4ec-e6aff4286e61"
TG = "7880252399"
needles = (UUID, "tg-"+TG, TG, UUID[:8])

print("HOST", socket.gethostname(), "UTC", now.isoformat(), "MSK", now.astimezone(MSK).isoformat())

# --- config ---
c = json.load(open("/usr/local/etc/xray/config.json"))
ci = [i for i in c["inbounds"] if i.get("tag") == "client-in"][0]
rs = ci["streamSettings"]["realitySettings"]
xh = ci["streamSettings"]["xhttpSettings"]
clients = ci["settings"].get("clients") or []
hit = [x for x in clients if x.get("id") == UUID or TG in str(x.get("email") or "")]
print("CLIENT_IN", json.dumps({
  "n_clients": len(clients),
  "user_hit": [(h.get("email"), h.get("id")) for h in hit],
  "dest": rs.get("dest"),
  "serverNames": rs.get("serverNames"),
  "shortIds": rs.get("shortIds"),
  "publicKey": rs.get("publicKey") or "",
  "path": xh.get("path"),
  "mode": xh.get("mode"),
}, ensure_ascii=False))
for o in c["outbounds"]:
  if o.get("tag") == "nl-exit":
    v = o["settings"]["vnext"][0]
    ox = o["streamSettings"]["xhttpSettings"]
    print("NL_EXIT", v["address"], v["port"], "mode", ox.get("mode"), "path", ox.get("path"))

# --- donor / hop TCP from RU (server-side) ---
def tcp(host, port, t=5):
  t0 = time.time()
  try:
    s = socket.create_connection((host, port), timeout=t); s.close()
    return True, round((time.time()-t0)*1000, 1)
  except Exception as e:
    return False, str(e)

for h, p in [("timeweb.cloud", 443), ("dl.google.com", 443), ("37.220.85.76", 8443), ("37.220.85.76", 2053)]:
  ok, ms = tcp(h, p)
  print(f"TCP {h}:{p} -> {ok} {ms}")
try:
  ctx = ssl.create_default_context()
  with socket.create_connection(("timeweb.cloud", 443), timeout=5) as sock:
    with ctx.wrap_socket(sock, server_hostname="timeweb.cloud") as ss:
      cert = ss.getpeercert() or {}
      print("TLS timeweb.cloud", ss.version(), "ok")
except Exception as e:
  print("TLS timeweb.cloud FAIL", e)

# --- find logs ---
paths = []
for p in [
  "/var/log/xeno/ru-access.log",
  "/var/log/xeno/access.log",
  "/var/log/xray/access.log",
  "/usr/local/etc/xray/access.log",
]:
  if os.path.isfile(p):
    paths.append(p)
if os.path.isdir("/var/log/xeno"):
  for f in sorted(os.listdir("/var/log/xeno")):
    p = os.path.join("/var/log/xeno", f)
    if os.path.isfile(p) and p not in paths:
      paths.append(p)
print("LOGS", [(p, os.path.getsize(p)) for p in paths[:20]])

def read_tail(p, n=10_000_000):
  with open(p, "rb") as fh:
    fh.seek(0, 2)
    size = fh.tell()
    fh.seek(max(0, size - n))
    return fh.read().decode("utf-8", "replace")

def pts(line):
  m = re.search(r"(\d{4}[/-]\d{2}[/-]\d{2}[ T]\d{2}:\d{2}:\d{2})", line)
  if not m: return None
  s = m.group(1).replace("T", " ").replace("/", "-")
  try: return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
  except: return None

user_ci_2h = []
user_ci_24h = []
pop_ci_2h = 0
pop_emails_2h = Counter()
user_any = []
err_2h = []

for p in paths:
  try:
    data = read_tail(p, 12_000_000)
  except Exception:
    continue
  base = os.path.basename(p)
  for line in data.splitlines():
    ts = pts(line)
    is_user = any(n in line for n in needles)
    if is_user:
      user_any.append((ts, base, line[:320]))
    if ts and ts >= cut2:
      if "accepted" in line and "client-in" in line:
        pop_ci_2h += 1
        m = re.search(r"email:\s*(\S+)", line)
        if m: pop_emails_2h[m.group(1)] += 1
        if is_user:
          user_ci_2h.append((ts, base, line[:320]))
      low = line.lower()
      if any(k in low for k in ("rejected", "failed", "eof", "unexpected", "reality", "invalid")):
        if "client-in" in line or "reality" in low or "xhttp" in low:
          err_2h.append((ts, base, line[:280]))
    if ts and ts >= cut24 and is_user and "accepted" in line and "client-in" in line:
      user_ci_24h.append((ts, base, line[:320]))

print("POP_client_in_accepted_2h", pop_ci_2h, "unique_emails", len(pop_emails_2h))
print("POP_top_emails", pop_emails_2h.most_common(12))
print("USER_client_in_2h", len(user_ci_2h))
for ts, base, line in user_ci_2h[-40:]:
  print(f"UCI2 {ts.astimezone(MSK).strftime('%H:%M:%S')} MSK | {base} | {line}")
print("USER_client_in_24h", len(user_ci_24h))
for ts, base, line in user_ci_24h[-25:]:
  print(f"UCI24 {ts.astimezone(MSK).strftime('%Y-%m-%d %H:%M:%S')} MSK | {base} | {line}")
print("USER_ANY_lines_tail", len(user_any))
for ts, base, line in user_any[-30:]:
  tss = ts.astimezone(MSK).strftime('%Y-%m-%d %H:%M:%S') if ts else "?"
  print(f"UANY {tss} MSK | {base} | {line}")
print("ERR_2h", len(err_2h))
for ts, base, line in err_2h[-30:]:
  print(f"ERR {ts.astimezone(MSK).strftime('%H:%M:%S')} MSK | {base} | {line}")

# journal
import subprocess
jc = subprocess.run(
  ["journalctl", "-u", "xray", "--since", "2 hours ago", "--no-pager"],
  capture_output=True, text=True, errors="replace"
)
jlines = [ln for ln in (jc.stdout or "").splitlines() if re.search(r"reality|reject|eof|unexpected|xhttp|fail|error", ln, re.I)]
print("JOURNAL_xray_match_2h", len(jlines))
for ln in jlines[-25:]:
  print("J", ln[:280])

# routing split quick
rules = c.get("routing", {}).get("rules") or []
print("ROUTING_rules", len(rules))
for r in rules[:12]:
  print(" R", {k: r.get(k) for k in ("type","inboundTag","outboundTag","domain","ip","protocol","port") if k in r or r.get(k)})
PY
'''

REMOTE_NL = r'''
python3 - <<'PY'
import json, os, re, sqlite3, subprocess
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict

UTC = timezone.utc
MSK = timezone(timedelta(hours=3))
now = datetime.now(UTC)
cut2 = now - timedelta(hours=2)
cut24 = now - timedelta(hours=24)
UUID = "bb5ad439-a9bc-4d29-b4ec-e6aff4286e61"
TG = "7880252399"
needles = (UUID, "tg-"+TG, TG, UUID[:8])
RU_IP = "201.34.131.141"

print("HOST", subprocess.check_output(["hostname"], text=True).strip(), "UTC", now.isoformat())
print("UPTIME", subprocess.check_output(["uptime"], text=True).strip())
# load
try:
  la = open("/proc/loadavg").read().strip()
  print("LOADAVG", la)
  mem = open("/proc/meminfo").read().splitlines()[:5]
  print("MEM", " | ".join(mem))
except Exception as e:
  print("load_err", e)

# network rough
try:
  out = subprocess.check_output(["ss", "-s"], text=True, errors="replace")
  print("SS_S", out.strip().replace("\n", " | "))
except Exception:
  pass

# CPU / bandwidth sample
try:
  # one-shot iface bytes
  def rx_tx():
    d = {}
    for line in open("/proc/net/dev"):
      if ":" not in line: continue
      name, rest = line.split(":", 1)
      name = name.strip()
      parts = rest.split()
      d[name] = (int(parts[0]), int(parts[8]))
    return d
  a = rx_tx(); import time; time.sleep(1.0); b = rx_tx()
  for iface in ("eth0", "ens3", "enp1s0", "venet0"):
    if iface in a and iface in b:
      print(f"BW1s {iface} rx_Mbit={(b[iface][0]-a[iface][0])*8/1e6:.2f} tx_Mbit={(b[iface][1]-a[iface][1])*8/1e6:.2f}")
except Exception as e:
  print("bw_err", e)

# direct inbound settings from xray or x-ui
for path in ("/usr/local/etc/xray/config.json", "/etc/xray/config.json"):
  if not os.path.isfile(path):
    continue
  c = json.load(open(path))
  for i in c.get("inbounds", []):
    tag = i.get("tag")
    if tag in ("xeno-direct-in", "xeno-relay-in") or i.get("port") in (2053, 8443):
      ss = i.get("streamSettings") or {}
      xh = ss.get("xhttpSettings") or {}
      rs = ss.get("realitySettings") or {}
      clients = (i.get("settings") or {}).get("clients") or []
      hit = [x for x in clients if x.get("id")==UUID or TG in str(x.get("email") or "")]
      print("INBOUND", path, tag, "port", i.get("port"), "n", len(clients),
            "mode", xh.get("mode"), "path", xh.get("path"),
            "dest", rs.get("dest"), "sni", rs.get("serverNames"),
            "user_hit", [(h.get("email"), (h.get("id") or "")[:8]) for h in hit])

# x-ui
db = "/etc/x-ui/x-ui.db"
if os.path.isfile(db):
  con = sqlite3.connect(db)
  for row in con.execute("select id,port,protocol,remark,settings from inbounds"):
    s = row[4] or ""
    if UUID in s or TG in s or "2053" == str(row[1]) or "direct" in str(row[3] or "").lower():
      print("XUI", "id", row[0], "port", row[1], "proto", row[2], "remark", row[3], "has_uuid", UUID in s)
  con.close()

# bot user
for dbp in ("/etc/runaway/xeno.net/data/bot.db", "/var/lib/xenonet/bot.db", "/opt/xenonet/bot.db"):
  if os.path.isfile(dbp):
    con = sqlite3.connect(dbp); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("select tg_id,username,active,expires_at,client_uuid,sub_token from users where tg_id=? or username like ?", (int(TG), "%xenoworth%"))]
    bans = [dict(r) for r in con.execute("select * from banned_users where tg_id=?", (int(TG),))]
    print("BOTDB", dbp, rows, "bans", bans)
    con.close()

def pts(line):
  m = re.search(r"(\d{4}[/-]\d{2}[/-]\d{2}[ T]\d{2}:\d{2}:\d{2})", line)
  if not m: return None
  s = m.group(1).replace("T"," ").replace("/","-")
  try: return datetime.strptime(s,"%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
  except: return None

def read_tail(p, n=12_000_000):
  with open(p,"rb") as fh:
    fh.seek(0,2); fh.seek(max(0, fh.tell()-n)); return fh.read().decode("utf-8","replace")

paths = []
if os.path.isdir("/var/log/xeno"):
  for f in sorted(os.listdir("/var/log/xeno")):
    p = os.path.join("/var/log/xeno", f)
    if os.path.isfile(p):
      paths.append(p)
print("NL_LOGS", [(os.path.basename(p), os.path.getsize(p)) for p in paths])

user_direct_2h = []
user_direct_24h = []
user_hop_2h = []
pop_direct_2h = 0
pop_hop_2h = 0
hop_from_ru_2h = 0
hop_canary_2h = 0
direct_emails = Counter()
# session length heuristic: group by email+from IP bursts
direct_user_lines = []

for p in paths:
  base = os.path.basename(p)
  try:
    data = read_tail(p)
  except Exception:
    continue
  for line in data.splitlines():
    ts = pts(line)
    is_user = any(n in line for n in needles)
    if not ts:
      continue
    if ts >= cut2 and "accepted" in line:
      if "xeno-direct" in line or "direct" in base:
        pop_direct_2h += 1
        m = re.search(r"email:\s*(\S+)", line)
        if m: direct_emails[m.group(1)] += 1
        if is_user:
          user_direct_2h.append((ts, base, line[:320]))
          direct_user_lines.append(line)
      if "xeno-relay" in line or "relay" in base or "hop" in line:
        pop_hop_2h += 1
        if RU_IP in line:
          hop_from_ru_2h += 1
        if "canary" in line.lower() or "hop_canary" in line:
          hop_canary_2h += 1
        if is_user:
          user_hop_2h.append((ts, base, line[:320]))
    if ts >= cut24 and is_user:
      if "xeno-direct" in line or "direct" in base:
        if "accepted" in line:
          user_direct_24h.append((ts, base, line[:320]))

print("POP_direct_accepted_2h", pop_direct_2h, "unique_emails", len(direct_emails), "top", direct_emails.most_common(8))
print("POP_hop_accepted_2h", pop_hop_2h, "from_RU_IP", hop_from_ru_2h, "canaryish", hop_canary_2h)
print("USER_direct_2h", len(user_direct_2h))
for ts, base, line in user_direct_2h[-40:]:
  print(f"udir2 {ts.astimezone(MSK).strftime('%H:%M:%S')} MSK | {base} | {line}")
print("USER_direct_24h", len(user_direct_24h))
for ts, base, line in user_direct_24h[-25:]:
  print(f"udir24 {ts.astimezone(MSK).strftime('%Y-%m-%d %H:%M:%S')} MSK | {base} | {line}")
print("USER_hop_2h", len(user_hop_2h))
for ts, base, line in user_hop_2h[-20:]:
  print(f"uhop2 {ts.astimezone(MSK).strftime('%H:%M:%S')} MSK | {base} | {line}")

# short-flow heuristic: count accepts per minute for user on direct last 2h
buckets = Counter()
for ts, base, line in user_direct_2h:
  buckets[ts.astimezone(MSK).strftime("%H:%M")] += 1
print("USER_direct_per_minute_2h", dict(sorted(buckets.items())[-30:]))

# path stats / canaries
for f in ("path_stats.json", "hop_canary.json", "ru_hop_canary.json"):
  p = f"/var/log/xeno/{f}"
  if os.path.isfile(p):
    raw = open(p, encoding="utf-8", errors="replace").read()
    print(f"FILE {f}", raw[:2500])
PY
'''


def main() -> int:
    inv = load(ROOT / "inventory" / "hosts.local.env")
    nl_pw = load(ROOT / "secrets" / "nl-access.env")
    ru_pw = load(ROOT / "secrets" / "ru-access.env")
    reality = load(ROOT / "secrets" / "reality.env")
    nl_ip = inv["NL_EXIT_IP"]
    ru_ip = inv["RU_BRIDGE_IP"]

    now = datetime.now(UTC)
    print("=== LOCALIZE xenoworth ===")
    print("utc", now.isoformat(), "msk", now.astimezone(MSK).strftime("%Y-%m-%d %H:%M:%S MSK"))
    print("uuid", UUID, "tg", TG, "ru", ru_ip, "nl", nl_ip)

    print("\n=== SSH ===")
    nl = ssh_direct(nl_ip, nl_pw["NL_SSH_PASS"])
    print("NL connected")
    ru = None
    how = "direct"
    try:
        ru = ssh_direct(ru_ip, ru_pw["RU_SSH_PASS"])
        print("RU connected direct")
    except Exception as e:
        print("RU direct fail:", type(e).__name__, e)
        how = "via_nl"
        ru = ssh_via(nl, ru_ip, ru_pw["RU_SSH_PASS"])
        print("RU connected via NL jump")

    print("\n=== LIVE SUB (via NL localhost, workstation may be filtered) ===")
    code, sub_out, sub_err = run(
        nl,
        f"curl -fsS --max-time 20 'https://127.0.0.1:2080/sub/{TOKEN}/' -k || "
        f"curl -fsS --max-time 20 'https://nl.xenoworth.ru:2080/sub/{TOKEN}/'",
        timeout=40,
    )
    body = sub_out
    if code != 0 or not body.strip():
        print("sub_fetch_fail", code, sub_err[:300], body[:200])
        # fallback: on-disk sub file
        code2, body2, _ = run(
            nl,
            f"python3 - <<'PY'\n"
            f"import os\n"
            f"tok={TOKEN!r}\n"
            f"for root in ('/etc/runaway/xeno.net/data/subs','/var/lib/xenonet/subs','/opt/xenonet/subs'):\n"
            f"  p=os.path.join(root, tok)\n"
            f"  if os.path.isfile(p):\n"
            f"    print(open(p,encoding='utf-8',errors='replace').read()); break\n"
            f"  p2=os.path.join(root, tok+'.txt')\n"
            f"  if os.path.isfile(p2):\n"
            f"    print(open(p2,encoding='utf-8',errors='replace').read()); break\n"
            f"PY",
            timeout=30,
        )
        body = body2
    profiles = []
    for ln in body.splitlines():
        if not ln.strip() or not ln.strip().startswith("vless://"):
            continue
        p = parse_vless(ln)
        profiles.append(p)
        print({k: p.get(k) for k in ("fragment", "host", "port", "sni", "pbk", "sid", "path", "mode", "fp", "type")})
        print("  uuid_ok", p.get("uuid") == UUID)
    if not profiles:
        print("WARNING: no profiles parsed; body_head", repr(body[:200]))
    ru_prof = next((p for p in profiles if p.get("port") == 443), None)
    dir_prof = next((p for p in profiles if p.get("port") == 2053), None)

    print("\n=== RU REMOTE ===")
    code, out, err = run(ru, REMOTE_RU, timeout=200)
    print(out)
    if err.strip():
        print("[ru stderr]", err[:800])
    print("ru_exit", code)

    # align
    if ru_prof:
        m = re.search(r"CLIENT_IN (\{.*\})", out)
        if m:
            live = json.loads(m.group(1))
            print("\n=== ALIGN sub vs live RU ===")
            print("sni", ru_prof.get("sni"), "in", live.get("serverNames"), "dest", live.get("dest"))
            print("sid_ok", ru_prof.get("sid") in (live.get("shortIds") or []), ru_prof.get("sid"), live.get("shortIds"))
            print("pbk_ok", ru_prof.get("pbk") == live.get("publicKey"))
            print("path_ok", ru_prof.get("path") == live.get("path"), ru_prof.get("path"), live.get("path"))
            print("mode_ok", ru_prof.get("mode") == live.get("mode"), ru_prof.get("mode"), live.get("mode"))
            print("env_pbk", (reality.get("REALITY_PUBLIC_KEY") or reality.get("PUBLIC_KEY") or "")[:24])

    print("\n=== NL REMOTE (accepts + Direct perf) ===")
    code, out2, err2 = run(nl, REMOTE_NL, timeout=200)
    print(out2)
    if err2.strip():
        print("[nl stderr]", err2[:800])
    print("nl_exit", code)

    # Optional: quick e2e from RU with xenoworth UUID (server-side label)
    print("\n=== SERVER-SIDE RU loopback e2e (NOT RF proof) ===")
    e2e = f"""
python3 - <<'PY'
import json, subprocess, tempfile, os, time, urllib.request
UUID={UUID!r}
cfg=json.load(open('/usr/local/etc/xray/config.json'))
ci=[i for i in cfg['inbounds'] if i.get('tag')=='client-in'][0]
rs=ci['streamSettings']['realitySettings']
xh=ci['streamSettings']['xhttpSettings']
pbk=rs.get('publicKey') or ''
sid=(rs.get('shortIds') or [''])[0]
sni=(rs.get('serverNames') or ['timeweb.cloud'])[0]
path=xh.get('path') or '/'
mode=xh.get('mode') or 'stream-one'
# find xray binary
xray='xray'
for p in ('/usr/local/bin/xray','/usr/bin/xray'):
  if os.path.isfile(p): xray=p; break
client={{
  "log": {{"loglevel":"warning"}},
  "outbounds": [{{
    "protocol":"vless",
    "settings":{{"vnext":[{{"address":"127.0.0.1","port":443,"users":[{{"id":UUID,"encryption":"none","flow":""}}]}}]}},
    "streamSettings":{{
      "network":"xhttp",
      "security":"reality",
      "realitySettings":{{"serverName":sni,"fingerprint":"chrome","publicKey":pbk,"shortId":sid,"spiderX":""}},
      "xhttpSettings":{{"path":path,"mode":mode}}
    }},
    "tag":"proxy"
  }}]
}}
td=tempfile.mkdtemp()
open(f'{{td}}/c.json','w').write(json.dumps(client))
# use curl through local socks? simpler: xray with freedom via proxy dokodemo - skip if heavy
# Instead TCP accept check already done; try curl to hop via existing ru_hop if script exists
print('loopback_params', sni, sid[:8], path, mode, pbk[:12])
# donor already checked
print('note', 'full e2e skipped if no local xray client helper')
PY
"""
    code, out3, _ = run(ru, e2e, timeout=60)
    print(out3)

    # Try official ru_hop_probe if present on NL bot
    print("\n=== ru_hop_probe if available (server-side) ===")
    code, out4, _ = run(
        nl,
        "python3 -c \"import sys; sys.path.insert(0,'/etc/runaway/xeno.net'); "
        "from bot.diag.ru_hop_probe import main\" 2>&1 | tail -n 40; "
        "ls /var/log/xeno/ru_hop_canary.json 2>/dev/null; "
        "python3 /etc/runaway/xeno.net/bot/diag/ru_hop_probe.py 2>&1 | tail -n 50 || "
        "python3 -c 'print(\"no probe module\")'",
        timeout=90,
    )
    print(out4[-3000:])

    print("\n=== SSH_PATH", how, "===")
    print("DONE")
    ru.close()
    nl.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
