#!/usr/bin/env python3
"""Перенастроить клиентский Reality на LTE-friendly TLS-донор (пример: dl.google.com).

Не трогает чужие inbound’ы панели и сторонние сервисы на машине.
Обновляет: entry inbound + direct на exit + secrets + sync бота.
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

# Пример донора (подбирайте под свою сеть)
LTE_DEST = "dl.google.com:443"
LTE_SNI = "dl.google.com"
LTE_SERVER_NAMES = [
    "dl.google.com",
    "google.com",
    "www.google.com",
    "android.com",
    "g.co",
    "goo.gl",
    "www.goo.gl",
    "youtu.be",
    "youtube.com",
    "android.clients.google.com",
]
LTE_PATH = "/"
LTE_FP = "randomized"


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
        raise RuntimeError(f"fail {code}: {err[:1500]}")
    return out


def sftp_write(c: paramiko.SSHClient, remote: str, data: str) -> None:
    sftp = c.open_sftp()
    with sftp.file(remote, "w") as f:
        f.write(data if data.endswith("\n") else data + "\n")
    sftp.close()


def patch_ru_inbound(ru: paramiko.SSHClient) -> None:
    names = json.dumps(LTE_SERVER_NAMES)
    run(
        ru,
        "python3 - <<'PY'\n"
        "import json\n"
        "p='/usr/local/etc/xray/config.json'\n"
        "c=json.load(open(p))\n"
        "names=json.loads(" + repr(names) + ")\n"
        f"dest={LTE_DEST!r}; path={LTE_PATH!r}\n"
        "for i in c.get('inbounds',[]):\n"
        "  if i.get('tag')!='client-in':\n"
        "    continue\n"
        "  ss=i.setdefault('streamSettings',{})\n"
        "  xs=ss.setdefault('xhttpSettings',{})\n"
        "  xs['path']=path\n"
        "  xs['mode']=xs.get('mode') or 'auto'\n"
        "  xs['xPaddingBytes']='100-1000'\n"
        "  rs=ss.setdefault('realitySettings',{})\n"
        "  rs['dest']=dest\n"
        "  rs['serverNames']=names\n"
        "  rs['show']=False\n"
        "  print('RU patched', 'dest', dest, 'path', path, 'sni0', names[0])\n"
        "  break\n"
        "else:\n"
        "  raise SystemExit('client-in missing')\n"
        "open(p,'w').write(json.dumps(c,indent=2,ensure_ascii=False)+'\\n')\n"
        "PY",
    )
    run(ru, "python3 -m json.tool /usr/local/etc/xray/config.json >/dev/null && systemctl restart xray && sleep 1 && systemctl is-active xray")


def patch_nl_direct(nl: paramiko.SSHClient) -> None:
    names = json.dumps(LTE_SERVER_NAMES)
    run(
        nl,
        "python3 - <<'PY'\n"
        "import json\n"
        "p='/usr/local/etc/xray/xeno-relay.json'\n"
        "c=json.load(open(p))\n"
        "names=json.loads(" + repr(names) + ")\n"
        f"dest={LTE_DEST!r}; path={LTE_PATH!r}\n"
        "found=False\n"
        "for i in c.get('inbounds',[]):\n"
        "  tag=i.get('tag')\n"
        "  if tag=='xeno-relay-in':\n"
        "    print('keep hop', i.get('port'), i['streamSettings']['realitySettings'].get('dest'))\n"
        "    continue\n"
        "  if tag!='xeno-direct-in':\n"
        "    continue\n"
        "  found=True\n"
        "  ss=i.setdefault('streamSettings',{})\n"
        "  xs=ss.setdefault('xhttpSettings',{})\n"
        "  xs['path']=path\n"
        "  xs['mode']=xs.get('mode') or 'auto'\n"
        "  xs['xPaddingBytes']='100-1000'\n"
        "  rs=ss.setdefault('realitySettings',{})\n"
        "  rs['dest']=dest\n"
        "  rs['serverNames']=names\n"
        "  # drop SelfSteal-only dest; keep existing privateKey/shortIds\n"
        "  print('Direct patched', 'port', i.get('port'), 'dest', dest)\n"
        "assert found, 'xeno-direct-in missing'\n"
        "open(p,'w').write(json.dumps(c,indent=2,ensure_ascii=False)+'\\n')\n"
        "PY",
    )
    run(nl, "python3 -m json.tool /usr/local/etc/xray/xeno-relay.json >/dev/null && systemctl restart xeno-relay && sleep 1 && systemctl is-active xeno-relay")
    run(nl, "systemctl is-active x-ui xeno-bot")


def build_sub(inv: dict, sec: dict) -> list[str]:
    client = sec.get("CLIENT_UUID") or sec.get("BOOTSTRAP_CLIENT_UUID")
    ru = inv.get("RU_DOMAIN") or inv["RU_BRIDGE_IP"]
    nl = inv.get("NL_DOMAIN") or inv["NL_EXIT_IP"]
    links = []
    q = urlencode(
        {
            "encryption": "none",
            "security": "reality",
            "sni": sec["BRIDGE_REALITY_SNI"],
            "fp": LTE_FP,
            "pbk": sec["BRIDGE_REALITY_PUBLIC_KEY"],
            "sid": sec["BRIDGE_REALITY_SHORT_ID"],
            "type": "xhttp",
            "path": sec["BRIDGE_PATH"],
            "mode": "auto",
        }
    )
    links.append(f"vless://{client}@{ru}:{inv.get('CLIENT_PORT','443')}?{q}#{quote('🇷🇺XENO RU')}")
    if sec.get("BACKUPS_ENABLED") in ("1", "true", "yes"):
        qd = urlencode(
            {
                "encryption": "none",
                "security": "reality",
                "sni": sec["DIRECT_REALITY_SNI"],
                "fp": LTE_FP,
                "pbk": sec["DIRECT_REALITY_PUBLIC_KEY"],
                "sid": sec["DIRECT_REALITY_SHORT_ID"],
                "type": "xhttp",
                "path": sec["DIRECT_PATH"],
                "mode": "auto",
            }
        )
        links.append(
            f"vless://{client}@{nl}:{sec['NL_DIRECT_PORT']}?{qd}#{quote('🇳🇱XENO NL Direct')}"
        )
        user = f"xeno-{client[:8]}"
        qh = urlencode({"sni": nl, "insecure": "0"})
        links.append(
            f"hysteria2://{user}:{client}@{nl}:{sec['HY2_PORT']}/?{qh}#{quote('🇳🇱XENO HY2')}"
        )
    text = "\n".join(links) + "\n"
    b64 = base64.b64encode(text.encode()).decode()
    (SECRETS / "subscription.txt").write_text(text, encoding="utf-8")
    (SECRETS / "subscription.base64").write_text(b64 + "\n", encoding="utf-8")
    return links


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    inv = load(_inventory_path())
    nl_acc = load(SECRETS / "nl-access.env")
    ru_acc = load(SECRETS / "ru-access.env")

    updates = {
        "BRIDGE_REALITY_SNI": LTE_SNI,
        "BRIDGE_REALITY_DEST": LTE_DEST,
        "BRIDGE_PATH": LTE_PATH,
        "DIRECT_REALITY_SNI": LTE_SNI,
        "DIRECT_REALITY_DEST": LTE_DEST,
        "DIRECT_PATH": LTE_PATH,
        "REALITY_CLIENT_FP": LTE_FP,
        "BACKUPS_ENABLED": "1",
    }
    save_env(SECRETS / "reality.env", updates)
    save_env(SECRETS / "bridge.env", updates)
    save_env(
        _inventory_path(),
        {"REALITY_SNI": LTE_SNI, "REALITY_DEST": LTE_DEST},
    )

    nl = ssh(inv["NL_EXIT_IP"], nl_acc["NL_SSH_PASS"])
    ru = ssh(inv["RU_BRIDGE_IP"], ru_acc["RU_SSH_PASS"])

    run(nl, "systemctl is-active x-ui xeno-bot xeno-relay")
    # tls ping donor from RU
    run(
        ru,
        "/usr/local/bin/xray tls ping dl.google.com 2>&1 | head -n 20 || true",
        check=False,
    )

    patch_ru_inbound(ru)
    patch_nl_direct(nl)

    # push secrets + latest bot, then sync_all
    bridge_body = (SECRETS / "bridge.env").read_text(encoding="utf-8")
    if "\\n" in bridge_body and bridge_body.count("\n") < 5:
        raise RuntimeError("bridge.env corrupted")
    sftp_write(nl, "/etc/runaway/xeno.net/config/bridge.env", bridge_body)

    # upload bot files that matter
    bot = ROOT / "bot"
    for name in ("xray_sync.py", "provision.py", "config.py", "messages.py", "main.py"):
        sftp_write(nl, f"/etc/runaway/xeno.net/bot/{name}", (bot / name).read_text(encoding="utf-8"))

    run(
        nl,
        "/etc/runaway/xeno.net/.venv/bin/python - <<'PY'\n"
        "import sys\n"
        "sys.path.insert(0,'/etc/runaway/xeno.net/bot')\n"
        "from config import load_settings\n"
        "from db import Database\n"
        "from provision import sync_all\n"
        "s=load_settings()\n"
        "print('sni', s.reality_sni, 'dest', s.reality_dest, 'path', s.bridge_path)\n"
        "print('direct', s.direct_sni, s.direct_path, 'fp_check backups', s.backups_enabled)\n"
        "db=Database(s.db_path)\n"
        "sync_all(db,s)\n"
        "print('SYNC_OK')\n"
        "PY",
        t=180,
    )
    run(nl, "systemctl restart xenonet-bot xenonet-sub && sleep 1 && systemctl is-active xenonet-bot xenonet-sub")
    run(nl, "systemctl is-active x-ui xeno-bot xeno-relay")

    sec = {}
    for name in ("uuids.env", "reality.env", "bridge.env"):
        sec.update(load(SECRETS / name))
    links = build_sub(inv, sec)

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

    nl.close()
    ru.close()

    print("\n=== RETARGET LTE ===")
    print(f"dest={LTE_DEST} sni={LTE_SNI} path={LTE_PATH} fp={LTE_FP}")
    for ln in links:
        print(ln[:110] + ("…" if len(ln) > 110 else ""))
    print("Sacred panel inbounds / sibling services untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
