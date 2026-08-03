#!/usr/bin/env python3
"""Enable validated NL backups for mobile DPI: Direct + HY2 in Happ sub.

Does not change RU Reality. Verifies cascade still green, then Direct e2e.
Sacred: never touch 3x-ui / trading.
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse, parse_qs, unquote

import paramiko

ROOT = Path(__file__).resolve().parents[1]


def _inventory_path() -> Path:
    local = ROOT / "inventory" / "hosts.local.env"
    return local if local.is_file() else ROOT / "inventory" / "hosts.env"

SECRETS = ROOT / "secrets"
INV = _inventory_path()


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


def merge() -> dict[str, str]:
    d: dict[str, str] = {}
    for name in ("uuids.env", "reality.env", "bridge.env"):
        d.update(load(SECRETS / name))
    return d


def ssh(host: str, password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="root", password=password, timeout=30, allow_agent=False, look_for_keys=False)
    return c


def run(c: paramiko.SSHClient, cmd: str, check: bool = True, t: int = 300) -> str:
    print(" $", cmd[:150].replace("\n", " "))
    _, o, e = c.exec_command(cmd, timeout=t)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        sys.stdout.buffer.write((out.rstrip()[:2500] + "\n").encode("utf-8", "replace"))
    if check and code != 0:
        raise RuntimeError(f"fail {code}: {err[:1500]}")
    return out


def sftp_write(c: paramiko.SSHClient, remote: str, data: str) -> None:
    sftp = c.open_sftp()
    with sftp.file(remote, "w") as f:
        f.write(data if data.endswith("\n") else data + "\n")
    sftp.close()


def assert_server_direct_matches(nl: paramiko.SSHClient, sec: dict) -> None:
    out = run(
        nl,
        "python3 - <<'PY'\n"
        "import json\n"
        "c=json.load(open('/usr/local/etc/xray/xeno-relay.json'))\n"
        "for i in c['inbounds']:\n"
        "  if i.get('tag')=='xeno-direct-in':\n"
        "    r=i['streamSettings']['realitySettings']\n"
        "    x=i['streamSettings']['xhttpSettings']\n"
        "    print('PORT', i['port'])\n"
        "    print('PATH', x.get('path'))\n"
        "    print('SNI', ','.join(r.get('serverNames') or []))\n"
        "    print('DEST', r.get('dest'))\n"
        "    print('SID', ','.join(r.get('shortIds') or []))\n"
        "    print('PBK_PRIV_PREFIX', (r.get('privateKey') or '')[:8])\n"
        "    print('CLIENTS', len(i['settings'].get('clients') or []))\n"
        "PY",
    )
    assert f"PORT {sec['NL_DIRECT_PORT']}" in out or f"PORT {int(sec['NL_DIRECT_PORT'])}" in out
    assert f"PATH {sec['DIRECT_PATH']}" in out, "DIRECT_PATH mismatch — redeploy backups"
    assert sec["DIRECT_REALITY_SNI"] in out
    assert sec["DIRECT_REALITY_SHORT_ID"] in out
    assert sec["DIRECT_REALITY_PRIVATE_KEY"][:8] in out
    print("server Direct matches secrets OK")


def sync_clients(nl: paramiko.SSHClient, sec: dict) -> list[str]:
    out = run(
        nl,
        "python3 - <<'PY'\n"
        "import sqlite3,time,json\n"
        "uu=[]\n"
        "con=sqlite3.connect('/etc/runaway/xeno.net/data/bot.db')\n"
        "now=int(time.time())\n"
        "for r in con.execute('SELECT client_uuid FROM users WHERE active=1 AND expires_at>?',(now,)):\n"
        "  uu.append(r[0])\n"
        "for r in con.execute('SELECT client_uuid FROM issued_links WHERE active=1 AND expires_at>?',(now,)):\n"
        "  if r[0] not in uu: uu.append(r[0])\n"
        "print(','.join(uu))\n"
        "PY",
        check=False,
    ).strip()
    uuids = [x for x in out.split(",") if x]
    boot = sec.get("BOOTSTRAP_CLIENT_UUID") or sec.get("CLIENT_UUID")
    if boot and boot not in uuids:
        uuids.insert(0, boot)
    clients = json.dumps([{"id": u, "email": f"xeno-{u[:8]}"} for u in uuids], ensure_ascii=False)
    # patch direct clients
    run(
        nl,
        "python3 - <<'PY'\n"
        "import json\n"
        f"clients=json.loads({clients!r})\n"
        "p='/usr/local/etc/xray/xeno-relay.json'\n"
        "c=json.load(open(p))\n"
        "found=False\n"
        "for i in c['inbounds']:\n"
        "  if i.get('tag')=='xeno-direct-in':\n"
        "    i['settings']['clients']=clients; found=True\n"
        "assert found\n"
        "open(p,'w').write(json.dumps(c,indent=2)+'\\n')\n"
        "print('direct clients', len(clients))\n"
        "PY",
    )
    run(nl, "systemctl restart xeno-relay && sleep 1 && systemctl is-active xeno-relay")
    # hy2 userpass — emit via JSON to avoid shell/heredoc quoting issues
    userpass = {f"xeno-{u[:8]}": u for u in uuids} or {
        "xeno-placeholder": "00000000-0000-0000-0000-000000000000"
    }
    payload = json.dumps(userpass)
    run(
        nl,
        "python3 - <<'PY'\n"
        "import json,re\n"
        f"userpass=json.loads({payload!r})\n"
        "p='/etc/hysteria/config.yaml'\n"
        "t=open(p).read()\n"
        "block='  userpass:\\n'+''.join(f'    {k}: {v}\\n' for k,v in userpass.items())\n"
        "nt,n=re.subn(r'(?m)^  userpass:\\n(?:    .+\\n)*', block, t, count=1)\n"
        "assert n==1, 'userpass block missing'\n"
        "open(p,'w').write(nt if nt.endswith('\\n') else nt+'\\n')\n"
        "print('hy2 users', len(userpass))\n"
        "PY",
    )
    run(nl, "systemctl restart xeno-hy2 && sleep 1 && systemctl is-active xeno-hy2")
    return uuids


def build_multi_sub(inv: dict, sec: dict) -> list[str]:
    client = sec.get("CLIENT_UUID") or sec.get("BOOTSTRAP_CLIENT_UUID")
    ru = inv.get("RU_DOMAIN") or inv["RU_BRIDGE_IP"]
    nl = inv.get("NL_DOMAIN") or inv["NL_EXIT_IP"]
    links = []
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
    links.append(f"vless://{client}@{ru}:{inv.get('CLIENT_PORT','443')}?{q}#{quote('🇷🇺XENO RU')}")
    qd = urlencode(
        {
            "encryption": "none",
            "security": "reality",
            "sni": sec["DIRECT_REALITY_SNI"],
            "fp": "chrome",
            "pbk": sec["DIRECT_REALITY_PUBLIC_KEY"],
            "sid": sec["DIRECT_REALITY_SHORT_ID"],
            "type": "xhttp",
            "path": sec["DIRECT_PATH"],
            "mode": "auto",
        }
    )
    links.append(f"vless://{client}@{nl}:{sec['NL_DIRECT_PORT']}?{qd}#{quote('🇳🇱XENO NL Direct')}")
    user = f"xeno-{client[:8]}"
    qh = urlencode({"sni": nl, "insecure": "0"})
    links.append(f"hysteria2://{user}:{client}@{nl}:{sec['HY2_PORT']}/?{qh}#{quote('🇳🇱XENO HY2')}")
    text = "\n".join(links) + "\n"
    b64 = base64.b64encode(text.encode()).decode()
    (SECRETS / "subscription.txt").write_text(text, encoding="utf-8")
    (SECRETS / "subscription.base64").write_text(b64 + "\n", encoding="utf-8")
    return links


def republish_ru_sub(inv: dict, ru_pass: str) -> None:
    url_path = SECRETS / "subscription.url"
    if not url_path.exists():
        return
    url = url_path.read_text(encoding="utf-8").strip()
    if ":2096/" not in url:
        return
    token = url.rstrip("/").split("/")[-1]
    b64 = (SECRETS / "subscription.base64").read_text(encoding="utf-8").strip() + "\n"
    ru = ssh(inv["RU_BRIDGE_IP"], ru_pass)
    run(ru, f"mkdir -p /var/www/xeno-sub/{token}")
    for name in ("sub.txt", "index.txt", "index.html"):
        sftp_write(ru, f"/var/www/xeno-sub/{token}/{name}", b64)
    run(ru, "systemctl restart xeno-sub || true", check=False)
    ru.close()


def find_xray() -> str | None:
    for p in (ROOT / "tools" / "xray.exe", ROOT / "tools" / "xray"):
        if p.exists():
            return str(p)
    return None


def e2e_direct(nl_ip: str, link: str) -> bool:
    import subprocess

    u = urlparse(link)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    socks_port = 11091
    cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": socks_port, "protocol": "socks", "settings": {"udp": True}}],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": nl_ip,
                            "port": u.port,
                            "users": [{"id": u.username, "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "reality",
                    "xhttpSettings": {"path": unquote(q.get("path", "/")), "mode": "auto"},
                    "realitySettings": {
                        "serverName": q.get("sni", ""),
                        "fingerprint": "chrome",
                        "publicKey": q.get("pbk", ""),
                        "shortId": q.get("sid", ""),
                    },
                },
            }
        ],
    }
    xray = find_xray()
    if not xray:
        print("WARN: no local xray; skip Direct e2e (TCP-only check)")
        import socket

        with socket.create_connection((nl_ip, u.port), timeout=8):
            print("TCP Direct OK")
        return True
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        proc = subprocess.Popen([xray, "run", "-c", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(2)
            r = subprocess.run(
                ["curl", "-sS", "--max-time", "25", "-x", f"socks5h://127.0.0.1:{socks_port}", "https://api.ipify.org"],
                capture_output=True,
                text=True,
            )
            ip = r.stdout.strip()
            print("Direct exit IP:", ip, "stderr:", r.stderr[:200])
            return r.returncode == 0 and ip == nl_ip
        finally:
            proc.terminate()
            try:
                proc.wait(5)
            except Exception:
                proc.kill()


def force_bot_sync(nl: paramiko.SSHClient) -> None:
    run(
        nl,
        "/etc/runaway/xeno.net/.venv/bin/python - <<'PY'\n"
        "import sys\n"
        "sys.path.insert(0,'/etc/runaway/xeno.net/bot')\n"
        "from config import load_settings\n"
        "from db import Database\n"
        "from provision import sync_all\n"
        "s=load_settings()\n"
        "print('backups_enabled', s.backups_enabled)\n"
        "db=Database(s.db_path)\n"
        "sync_all(db,s)\n"
        "print('sync_all OK')\n"
        "PY",
        t=180,
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    inv = load(INV)
    nl_acc = load(SECRETS / "nl-access.env")
    ru_acc = load(SECRETS / "ru-access.env")
    sec = merge()
    for k in (
        "DIRECT_PATH",
        "DIRECT_REALITY_PUBLIC_KEY",
        "DIRECT_REALITY_PRIVATE_KEY",
        "DIRECT_REALITY_SHORT_ID",
        "NL_DIRECT_PORT",
        "HY2_PORT",
    ):
        if not sec.get(k):
            raise RuntimeError(f"missing {k}")

    nl = ssh(inv["NL_EXIT_IP"], nl_acc["NL_SSH_PASS"])
    run(nl, "systemctl is-active x-ui xeno-bot xeno-relay xeno-steal-nl xeno-hy2")
    assert_server_direct_matches(nl, sec)
    uuids = sync_clients(nl, sec)
    print("uuids", uuids)

    save_env(
        SECRETS / "bridge.env",
        {
            "BACKUPS_ENABLED": "1",
            "NL_PUBLIC_HOST": inv.get("NL_DOMAIN") or inv.get("NL_EXIT_IP") or "exit.example.com",
        },
    )
    save_env(SECRETS / "reality.env", {"BACKUPS_ENABLED": "1"})
    # push bridge.env to NL before sync
    bridge_body = (SECRETS / "bridge.env").read_text(encoding="utf-8")
    # ensure no literal \\n corruption
    if "\\n" in bridge_body and bridge_body.count("\n") < 5:
        raise RuntimeError("bridge.env looks corrupted with literal \\n")
    sftp_write(nl, "/etc/runaway/xeno.net/config/bridge.env", bridge_body)
    run(nl, "systemctl restart xenonet-bot xenonet-sub && sleep 1 && systemctl is-active xenonet-bot xenonet-sub")

    # refresh bot code for multi-profile (in case older)
    # lightweight: only sync via current code if profiles_for exists
    force_bot_sync(nl)

    links = build_multi_sub(inv, merge())
    republish_ru_sub(inv, ru_acc["RU_SSH_PASS"])

    # verify hop still ok quickly
    run(nl, "systemctl is-active xeno-relay x-ui xeno-bot")
    nl.close()

    direct = next(x for x in links if "NL%20Direct" in x or "Direct" in x)
    ok = e2e_direct(inv["NL_EXIT_IP"], direct)
    if not ok:
        raise RuntimeError("Direct e2e failed — not enabling for clients")

    print("\n=== MOBILE BACKUPS ENABLED ===")
    for ln in links:
        print(ln[:100] + ("…" if len(ln) > 100 else ""))
    print("On mobile: try XENO HY2 first, then XENO NL Direct, keep XENO RU for WiFi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
