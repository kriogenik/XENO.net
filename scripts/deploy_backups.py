#!/usr/bin/env python3
"""Deploy exit backup profiles: Direct XHTTP + optional Hysteria2.

Never touch sacred panel inbounds or sibling services on the host.
"""
from __future__ import annotations

import json
import secrets
import sys
import time
import uuid
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]


def _inventory_path() -> Path:
    local = ROOT / "inventory" / "hosts.local.env"
    return local if local.is_file() else ROOT / "inventory" / "hosts.env"

SECRETS = ROOT / "secrets"
INV = _inventory_path()
NL_TPL = ROOT / "configs" / "xray" / "nl-coexist.json.template"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'").strip('"')
    return env


def save_env(path: Path, updates: dict[str, str]) -> None:
    existing = load_env(path)
    existing.update({k: str(v) for k, v in updates.items() if v is not None})
    lines = [f"{k}={existing[k]}" for k in existing]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def merge_secrets() -> dict[str, str]:
    data: dict[str, str] = {}
    for name in ("uuids.env", "reality.env", "bridge.env"):
        data.update(load_env(SECRETS / name))
    return data


def ssh_connect(host: str, password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="root", password=password, timeout=30, allow_agent=False, look_for_keys=False)
    return c


def run(c: paramiko.SSHClient, cmd: str, check: bool = True) -> str:
    print(" $", cmd[:160].replace("\n", " "))
    _, o, e = c.exec_command(cmd, timeout=600)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        sys.stdout.buffer.write((out.rstrip()[:3000] + "\n").encode("utf-8", "replace"))
    if check and code != 0:
        raise RuntimeError(f"failed ({code}): {cmd}\n{err[:1500]}")
    return out


def sftp_write(c: paramiko.SSHClient, remote: str, data: str) -> None:
    sftp = c.open_sftp()
    with sftp.file(remote, "w") as f:
        f.write(data if data.endswith("\n") else data + "\n")
    sftp.close()


def render(template: str, mapping: dict[str, str]) -> str:
    text = template
    for k, v in mapping.items():
        text = text.replace("{{" + k + "}}", str(v))
    if "{{" in text:
        i = text.find("{{")
        raise RuntimeError(f"unrendered placeholder: {text[i:i+48]}")
    return text


def parse_x25519(out: str) -> tuple[str, str]:
    priv = pub = None
    for line in out.splitlines():
        low = line.lower()
        if "private" in low and ":" in line:
            priv = line.split(":", 1)[1].strip()
        if "public" in low and ":" in line:
            pub = line.split(":", 1)[1].strip()
    if not priv or not pub:
        raise RuntimeError(f"x25519 parse failed:\n{out}")
    return priv, pub


def ensure_direct_secrets(nl: paramiko.SSHClient, inv: dict[str, str], data: dict[str, str]) -> dict[str, str]:
    data = dict(data)
    data.setdefault("NL_DIRECT_PORT", inv.get("NL_DIRECT_PORT", "2053"))
    data.setdefault("HY2_PORT", inv.get("HY2_PORT", "8444"))
    data.setdefault("DIRECT_PATH", "/" + secrets.token_hex(8))
    data.setdefault("DIRECT_REALITY_SHORT_ID", secrets.token_hex(8))
    nl_dom = inv.get("NL_DOMAIN") or inv.get("NL_EXIT_IP") or "exit.example.com"
    data.setdefault("DIRECT_REALITY_SNI", nl_dom)
    data.setdefault("DIRECT_REALITY_DEST", "127.0.0.1:9443")
    if not data.get("DIRECT_REALITY_PRIVATE_KEY") or not data.get("DIRECT_REALITY_PUBLIC_KEY"):
        out = run(nl, "/usr/local/bin/xray x25519 || /usr/local/x-ui/bin/xray-linux-amd64 x25519")
        priv, pub = parse_x25519(out)
        data["DIRECT_REALITY_PRIVATE_KEY"] = priv
        data["DIRECT_REALITY_PUBLIC_KEY"] = pub

    required = (
        "RELAY_UUID",
        "RELAY_PATH",
        "RELAY_REALITY_PRIVATE_KEY",
        "RELAY_REALITY_SHORT_ID",
        "RELAY_REALITY_SNI",
        "RELAY_REALITY_DEST",
    )
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise RuntimeError(f"missing hop secrets for coexist deploy: {missing}")

    direct_keys = {
        "NL_DIRECT_PORT": data["NL_DIRECT_PORT"],
        "HY2_PORT": data["HY2_PORT"],
        "DIRECT_PATH": data["DIRECT_PATH"],
        "DIRECT_REALITY_PRIVATE_KEY": data["DIRECT_REALITY_PRIVATE_KEY"],
        "DIRECT_REALITY_PUBLIC_KEY": data["DIRECT_REALITY_PUBLIC_KEY"],
        "DIRECT_REALITY_SHORT_ID": data["DIRECT_REALITY_SHORT_ID"],
        "DIRECT_REALITY_SNI": data["DIRECT_REALITY_SNI"],
        "DIRECT_REALITY_DEST": data["DIRECT_REALITY_DEST"],
        "BACKUPS_ENABLED": "1",
        "NL_DOMAIN": nl_dom,
        "NL_PUBLIC_HOST": nl_dom,
    }
    save_env(SECRETS / "reality.env", direct_keys)
    save_env(SECRETS / "bridge.env", direct_keys)
    data.update(direct_keys)
    return data


def bootstrap_clients(uuids: list[str]) -> list[dict]:
    if not uuids:
        uuids = [str(uuid.uuid4())]
    return [{"id": u, "email": f"xeno-{u[:8]}"} for u in uuids]


def hy2_yaml(port: str, cert: str, key: str, userpass: dict[str, str]) -> str:
    lines = [
        f"listen: :{port}",
        "tls:",
        f"  cert: {cert}",
        f"  key: {key}",
        "auth:",
        "  type: userpass",
        "  userpass:",
    ]
    for user, password in userpass.items():
        lines.append(f"    {user}: {password}")
    lines += [
        "masquerade:",
        "  type: proxy",
        "  proxy:",
        "    url: https://www.cloudflare.com",
        "    rewriteHost: true",
        "bandwidth:",
        "  up: 1 gbps",
        "  down: 1 gbps",
        "",
    ]
    return "\n".join(lines)


def deploy_nl_coexist(nl: paramiko.SSHClient, inv: dict[str, str], sec: dict[str, str], client_uuids: list[str]) -> None:
    clients = bootstrap_clients(client_uuids)
    mapping = {
        **inv,
        **sec,
        "RELAY_PORT": inv.get("RELAY_PORT", "8443"),
        "NL_DIRECT_PORT": sec.get("NL_DIRECT_PORT", "2053"),
        "NL_DIRECT_CLIENTS_JSON": json.dumps(clients, ensure_ascii=False),
    }
    cfg = render(NL_TPL.read_text(encoding="utf-8"), mapping)
    json.loads(cfg)
    sftp_write(nl, "/usr/local/etc/xray/xeno-relay.json", cfg)
    run(nl, "systemctl restart xeno-relay && sleep 1 && systemctl is-active xeno-relay")
    run(
        nl,
        f"ss -lntp | grep -E ':{mapping['RELAY_PORT']}|:{mapping['NL_DIRECT_PORT']}' || true",
        check=False,
    )
    run(
        nl,
        f"ufw allow {mapping['NL_DIRECT_PORT']}/tcp comment 'xeno-nl-direct' || true; ufw reload || true",
        check=False,
    )


def install_hysteria2(nl: paramiko.SSHClient) -> None:
    run(
        nl,
        r"""
set -euo pipefail
if [ -x /usr/local/bin/hysteria ]; then
  /usr/local/bin/hysteria version | head -n 1 || true
  exit 0
fi
arch=$(uname -m)
case "$arch" in x86_64) a=amd64;; aarch64|arm64) a=arm64;; *) echo bad-arch; exit 1;; esac
asset=$(curl -fsSL https://api.github.com/repos/apernet/hysteria/releases/latest \
  | python3 -c "import sys,json,re; j=json.load(sys.stdin); a=sys.argv[1];
print(next(x['browser_download_url'] for x in j['assets'] if re.search(r'linux-'+a+r'$', x['name'])))" "$a")
tmp=$(mktemp -d)
curl -fsSL -o "$tmp/hysteria" "$asset"
install -m 755 "$tmp/hysteria" /usr/local/bin/hysteria
rm -rf "$tmp"
/usr/local/bin/hysteria version | head -n 1
""",
    )


def deploy_hysteria2(nl: paramiko.SSHClient, inv: dict[str, str], sec: dict[str, str], client_uuids: list[str]) -> None:
    install_hysteria2(nl)
    nl_dom = inv.get("NL_DOMAIN") or inv.get("NL_EXIT_IP") or "exit.example.com"
    cert = f"/root/cert/{nl_dom}/fullchain.pem"
    key = f"/root/cert/{nl_dom}/privkey.pem"
    ok = run(nl, f"test -f {cert} && test -f {key} && echo OK || echo NO", check=False)
    if "OK" not in ok:
        raise RuntimeError(f"missing TLS certs for Hysteria2 at {cert}")
    uuids = client_uuids or [str(uuid.uuid4())]
    userpass = {f"xeno-{u[:8]}": u for u in uuids}
    port = sec.get("HY2_PORT", "8444")
    body = hy2_yaml(port, cert, key, userpass)
    run(nl, "mkdir -p /etc/hysteria /var/log/xeno")
    sftp_write(nl, "/etc/hysteria/config.yaml", body)
    unit = """[Unit]
Description=xeno.net Hysteria2 backup
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/hysteria server -c /etc/hysteria/config.yaml
Restart=on-failure
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
"""
    sftp_write(nl, "/etc/systemd/system/xeno-hy2.service", unit)
    run(nl, "systemctl daemon-reload && systemctl enable xeno-hy2 && systemctl restart xeno-hy2")
    time.sleep(1)
    run(nl, "systemctl is-active xeno-hy2")
    run(nl, f"ufw allow {port}/udp comment 'xeno-hy2' || true; ufw reload || true", check=False)
    run(nl, f"ss -lnup | grep ':{port}' || true", check=False)


def load_bot_uuids_from_nl(nl: paramiko.SSHClient, data: dict[str, str]) -> list[str]:
    out = run(
        nl,
        "python3 - <<'PY'\n"
        "import sqlite3, time\n"
        "db='/etc/runaway/xeno.net/data/bot.db'\n"
        "uu=[]\n"
        "try:\n"
        "  con=sqlite3.connect(db)\n"
        "  now=int(time.time())\n"
        "  for r in con.execute('SELECT client_uuid FROM users WHERE active=1 AND expires_at>?', (now,)):\n"
        "    uu.append(r[0])\n"
        "  for r in con.execute('SELECT client_uuid FROM issued_links WHERE active=1 AND expires_at>?', (now,)):\n"
        "    if r[0] not in uu: uu.append(r[0])\n"
        "except Exception as e:\n"
        "  print('ERR', e, file=__import__('sys').stderr)\n"
        "print(','.join(uu))\n"
        "PY",
        check=False,
    ).strip()
    uuids = [x for x in out.split(",") if x and "ERR" not in x]
    boot = data.get("BOOTSTRAP_CLIENT_UUID") or data.get("CLIENT_UUID")
    if boot and boot not in uuids:
        uuids.insert(0, boot)
    return uuids


def build_multi_sub(inv: dict[str, str], sec: dict[str, str]) -> list[str]:
    from urllib.parse import quote, urlencode
    import base64

    client = sec.get("CLIENT_UUID") or sec.get("BOOTSTRAP_CLIENT_UUID")
    if not client:
        raise RuntimeError("CLIENT_UUID missing")

    ru = inv.get("RU_DOMAIN") or inv["RU_BRIDGE_IP"]
    nl = inv.get("NL_DOMAIN") or inv["NL_EXIT_IP"]
    links: list[str] = []

    # 1) RU cascade primary
    q = urlencode(
        {
            "encryption": "none",
            "security": "reality",
            "sni": sec.get("BRIDGE_REALITY_SNI", "www.cloudflare.com"),
            "fp": "chrome",
            "pbk": sec["BRIDGE_REALITY_PUBLIC_KEY"],
            "sid": sec["BRIDGE_REALITY_SHORT_ID"],
            "type": "xhttp",
            "path": sec["BRIDGE_PATH"],
            "mode": "auto",
        }
    )
    links.append(f"vless://{client}@{ru}:{inv.get('CLIENT_PORT', '443')}?{q}#{quote('🇷🇺XENO RU')}")

    # 2) NL Direct backup
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
    links.append(
        f"vless://{client}@{nl}:{sec['NL_DIRECT_PORT']}?{qd}#{quote('🇳🇱XENO NL Direct')}"
    )

    # 3) Hysteria2
    user = f"xeno-{client[:8]}"
    qh = urlencode({"sni": nl, "insecure": "0"})
    links.append(f"hysteria2://{user}:{client}@{nl}:{sec['HY2_PORT']}/?{qh}#{quote('🇳🇱XENO HY2')}")

    text = "\n".join(links) + "\n"
    b64 = base64.b64encode(text.encode()).decode()
    (SECRETS / "subscription.txt").write_text(text, encoding="utf-8")
    (SECRETS / "subscription.base64").write_text(b64, encoding="utf-8")
    return links


def republish_bootstrap_sub() -> None:
    """Refresh RU :2096 bootstrap body when subscription.url points there."""
    url_path = SECRETS / "subscription.url"
    if not url_path.exists():
        return
    url = url_path.read_text(encoding="utf-8").strip()
    if ":2096/" not in url:
        return
    b64 = (SECRETS / "subscription.base64").read_text(encoding="utf-8").strip() + "\n"
    token = url.rstrip("/").split("/")[-1]
    ru_acc = load_env(SECRETS / "ru-access.env")
    ru_ip = ru_acc.get("RU_BRIDGE_IP") or load_env(INV)["RU_BRIDGE_IP"]
    ru = ssh_connect(ru_ip, ru_acc["RU_SSH_PASS"])
    run(ru, f"mkdir -p /var/www/xeno-sub/{token}")
    for name in ("sub.txt", "index.txt", "index.html"):
        sftp_write(ru, f"/var/www/xeno-sub/{token}/{name}", b64)
    run(ru, "systemctl restart xeno-sub || true", check=False)
    ru.close()


def main() -> int:
    inv = load_env(INV)
    nl_acc = load_env(SECRETS / "nl-access.env")
    data = merge_secrets()
    nl_ip = inv["NL_EXIT_IP"]
    nl = ssh_connect(nl_ip, nl_acc["NL_SSH_PASS"])
    run(nl, "systemctl is-active x-ui xeno-bot xeno-relay xeno-steal-nl")
    sec = ensure_direct_secrets(nl, inv, data)
    run(nl, "systemctl is-active xeno-steal-nl || systemctl restart xeno-steal-nl", check=False)
    uuids = load_bot_uuids_from_nl(nl, sec)
    print("client uuids:", ",".join(uuids) if uuids else "(bootstrap only)")
    deploy_nl_coexist(nl, inv, sec, uuids)
    deploy_hysteria2(nl, inv, sec, uuids)
    run(nl, "systemctl is-active x-ui xeno-bot xeno-relay xeno-hy2")
    nl.close()

    links = build_multi_sub(inv, {**merge_secrets(), **sec})
    republish_bootstrap_sub()
    print("\n=== DONE backups ===")
    print("NL Direct :%s/tcp + HY2 :%s/udp" % (sec["NL_DIRECT_PORT"], sec["HY2_PORT"]))
    for link in links:
        print(link[:96] + ("…" if len(link) > 96 else ""))
    print("Updated secrets/subscription.txt (multi-profile). Redeploy bot to refresh user subs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
