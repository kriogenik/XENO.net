#!/usr/bin/env python3
"""Deploy two-node cascade: NL coexist relay (:8443) + RU bridge. Does not touch 3x-ui."""
from __future__ import annotations

import json
import os
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
CONFIGS = ROOT / "configs" / "xray"
INV = _inventory_path()


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v.strip().strip("'").strip('"')
    return env


def ssh_connect(host: str, password: str, user: str = "root") -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        host,
        username=user,
        password=password,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    return c


def _safe_print(text: str, *, file=sys.stdout) -> None:
    enc = getattr(file, "encoding", None) or "utf-8"
    file.buffer.write((text + "\n").encode(enc, errors="replace"))
    file.flush()


def run(c: paramiko.SSHClient, cmd: str, check: bool = True) -> str:
    _safe_print(f"  $ {cmd[:120]}{'…' if len(cmd) > 120 else ''}")
    _i, o, e = c.exec_command(cmd, timeout=600)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        # apt/systemd often emit unicode arrows — don't crash Windows console
        _safe_print(out.rstrip()[:4000])
    if err.strip():
        _safe_print(err.rstrip()[:2000], file=sys.stderr)
    if check and code != 0:
        raise RuntimeError(f"cmd failed ({code}): {cmd}\n{err[:2000]}")
    return out

def sftp_write(c: paramiko.SSHClient, remote: str, data: str | bytes) -> None:
    sftp = c.open_sftp()
    with sftp.file(remote, "w") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            f.write(data)
    sftp.close()


def render(template: str, mapping: dict[str, str]) -> str:
    text = template
    for k, v in mapping.items():
        text = text.replace("{{" + k + "}}", str(v))
    if "{{" in text:
        raise RuntimeError(f"unrendered placeholders left in template: {text[text.find('{{'):text.find('{{')+40]}")
    return text


def _parse_x25519(out: str) -> tuple[str, str]:
    priv = pub = None
    for line in out.splitlines():
        low = line.lower()
        if "private" in low and ":" in line:
            priv = line.split(":", 1)[1].strip()
        if "public" in low and ":" in line:
            pub = line.split(":", 1)[1].strip()
    if not priv or not pub:
        raise RuntimeError(f"failed to parse x25519 output:\n{out}")
    return priv, pub


def _xray_bin_cmd(extra: str) -> str:
    return (
        "XBIN=$(command -v xray 2>/dev/null || true); "
        "if [ -z \"$XBIN\" ]; then XBIN=$(ls /usr/local/x-ui/bin/xray* 2>/dev/null | head -n1); fi; "
        "if [ -z \"$XBIN\" ]; then "
        "  ver=$(curl -fsSL https://api.github.com/repos/XTLS/Xray-core/releases/latest | grep -oP '\"tag_name\": \"\\K[^\"]+'); "
        "  tmp=$(mktemp -d); cd $tmp; "
        "  curl -fsSL -o x.zip https://github.com/XTLS/Xray-core/releases/download/${ver}/Xray-linux-64.zip; "
        "  unzip -q x.zip xray; XBIN=$tmp/xray; "
        "fi; "
        f"$XBIN {extra}"
    )


def ensure_local_secrets(inv: dict[str, str]) -> dict[str, str]:
    """Fresh dual-hop secrets (bridge vs relay). Legacy single REALITY_* ignored."""
    SECRETS.mkdir(exist_ok=True)
    reality_path = SECRETS / "reality.env"
    uuids_path = SECRETS / "uuids.env"
    data: dict[str, str] = {}
    if reality_path.exists():
        data.update(load_env(reality_path))
    if uuids_path.exists():
        data.update(load_env(uuids_path))

    if "CLIENT_UUID" not in data:
        data["CLIENT_UUID"] = str(uuid.uuid4())
    if "RELAY_UUID" not in data:
        data["RELAY_UUID"] = str(uuid.uuid4())
    if "BRIDGE_REALITY_SHORT_ID" not in data:
        data["BRIDGE_REALITY_SHORT_ID"] = secrets.token_hex(8)
    if "RELAY_REALITY_SHORT_ID" not in data:
        data["RELAY_REALITY_SHORT_ID"] = secrets.token_hex(8)
    if "BRIDGE_PATH" not in data:
        data["BRIDGE_PATH"] = "/" + secrets.token_hex(8)
    if "RELAY_PATH" not in data:
        data["RELAY_PATH"] = "/" + secrets.token_hex(8)

    # Defaults: short-cert dest until SelfSteal succeeds in deploy_*
    fallback_sni = inv.get("REALITY_SNI", "www.cloudflare.com")
    fallback_dest = inv.get("REALITY_DEST", f"{fallback_sni}:443")
    nl_dom = inv.get("NL_DOMAIN", "").strip()
    # Bridge stays on short-cert until RU LE SelfSteal is ready (certbot often blocked)
    data.setdefault("BRIDGE_REALITY_SNI", fallback_sni)
    data.setdefault("BRIDGE_REALITY_DEST", fallback_dest)
    data.setdefault("RELAY_REALITY_SNI", nl_dom or fallback_sni)
    data.setdefault("RELAY_REALITY_DEST", fallback_dest)

    uuids_path.write_text(
        f"CLIENT_UUID={data['CLIENT_UUID']}\nRELAY_UUID={data['RELAY_UUID']}\n",
        encoding="utf-8",
    )
    return data


def persist_reality(data: dict[str, str]) -> None:
    keys = [
        "BRIDGE_REALITY_PRIVATE_KEY",
        "BRIDGE_REALITY_PUBLIC_KEY",
        "BRIDGE_REALITY_SHORT_ID",
        "BRIDGE_REALITY_SNI",
        "BRIDGE_REALITY_DEST",
        "BRIDGE_PATH",
        "RELAY_REALITY_PRIVATE_KEY",
        "RELAY_REALITY_PUBLIC_KEY",
        "RELAY_REALITY_SHORT_ID",
        "RELAY_REALITY_SNI",
        "RELAY_REALITY_DEST",
        "RELAY_PATH",
    ]
    lines = [f"{k}={data[k]}" for k in keys if k in data]
    (SECRETS / "reality.env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Wrote secrets/reality.env")


def gen_reality_keys_on_nl(c: paramiko.SSHClient, data: dict[str, str]) -> dict[str, str]:
    need_bridge = not (data.get("BRIDGE_REALITY_PRIVATE_KEY") and data.get("BRIDGE_REALITY_PUBLIC_KEY"))
    need_relay = not (data.get("RELAY_REALITY_PRIVATE_KEY") and data.get("RELAY_REALITY_PUBLIC_KEY"))
    if not need_bridge and not need_relay:
        return data
    if need_bridge:
        out = run(c, _xray_bin_cmd("x25519"))
        priv, pub = _parse_x25519(out)
        data["BRIDGE_REALITY_PRIVATE_KEY"] = priv
        data["BRIDGE_REALITY_PUBLIC_KEY"] = pub
    if need_relay:
        out = run(c, _xray_bin_cmd("x25519"))
        priv, pub = _parse_x25519(out)
        data["RELAY_REALITY_PRIVATE_KEY"] = priv
        data["RELAY_REALITY_PUBLIC_KEY"] = pub
    persist_reality(data)
    return data


def install_xray_binary(c: paramiko.SSHClient) -> None:
    run(
        c,
        r"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y curl unzip jq ca-certificates ufw
arch=$(uname -m)
case "$arch" in x86_64) a=64;; aarch64|arm64) a=arm64-v8a;; *) echo bad arch; exit 1;; esac
ver=$(curl -fsSL https://api.github.com/repos/XTLS/Xray-core/releases/latest | jq -r .tag_name)
tmp=$(mktemp -d)
cd "$tmp"
curl -fsSL -o xray.zip "https://github.com/XTLS/Xray-core/releases/download/${ver}/Xray-linux-${a}.zip"
unzip -qo xray.zip
install -m 755 xray /usr/local/bin/xray
mkdir -p /usr/local/etc/xray /usr/local/share/xray /etc/xeno
curl -fsSL -o /usr/local/share/xray/geoip.dat https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat
curl -fsSL -o /usr/local/share/xray/geosite.dat https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat
rm -rf "$tmp"
/usr/local/bin/xray version | head -n 1
""",
    )


def setup_selfsteal(
    c: paramiko.SSHClient,
    *,
    domain: str,
    cert_dir: str,
    unit_name: str = "xeno-steal",
    listen: str = "127.0.0.1:9443",
) -> bool:
    """Serve Reality dest via dedicated nginx (not Python ThreadingTCPServer).

    Python SelfSteal wedges under probe load (active + Recv-Q, TLS timeout).
    Isolated ``nginx -c /etc/xeno/steal-nginx.conf`` does not touch sibling sites.
    """
    if not domain or not cert_dir:
        return False
    fullchain = f"{cert_dir}/fullchain.pem"
    privkey = f"{cert_dir}/privkey.pem"
    check = run(c, f"test -f {fullchain} && test -f {privkey} && echo OK || echo NO", check=False)
    if "OK" not in check:
        print(f"  selfsteal skip: no certs in {cert_dir}")
        return False
    run(
        c,
        "export DEBIAN_FRONTEND=noninteractive; "
        "command -v nginx >/dev/null || apt-get install -y nginx",
        check=False,
    )
    run(c, "mkdir -p /var/www/xeno-steal /etc/xeno /var/log/xeno")
    run(
        c,
        "test -f /var/www/xeno-steal/index.html || "
        "echo '<!doctype html><title>ok</title>ok' > /var/www/xeno-steal/index.html",
        check=False,
    )
    host, port = listen.split(":")
    nginx_conf = f"""# XENO Reality SelfSteal — dedicated nginx
worker_processes 1;
error_log /var/log/xeno/steal-nginx-error.log warn;
pid /run/xeno-steal-nginx.pid;
events {{
    worker_connections 256;
}}
http {{
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log off;
    sendfile on;
    keepalive_timeout 15;
    server {{
        listen {host}:{port} ssl;
        server_name {domain} _;
        ssl_certificate {fullchain};
        ssl_certificate_key {privkey};
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_cache shared:xeno_steal:10m;
        root /var/www/xeno-steal;
        location / {{
            try_files $uri $uri/ /index.html;
        }}
    }}
}}
"""
    sftp_write(c, "/etc/xeno/steal-nginx.conf", nginx_conf)
    unit = f"""[Unit]
Description=XENO Reality SelfSteal HTTPS (nginx {listen})
After=network.target
StartLimitIntervalSec=0
[Service]
Type=forking
PIDFile=/run/xeno-steal-nginx.pid
ExecStartPre=/usr/sbin/nginx -t -c /etc/xeno/steal-nginx.conf
ExecStart=/usr/sbin/nginx -c /etc/xeno/steal-nginx.conf
ExecReload=/usr/sbin/nginx -s reload
ExecStop=/bin/kill -s QUIT $MAINPID
Restart=always
RestartSec=2
KillMode=mixed
TimeoutStopSec=10
[Install]
WantedBy=multi-user.target
"""
    sftp_write(c, f"/etc/systemd/system/{unit_name}.service", unit)
    run(c, f"systemctl stop {unit_name} 2>/dev/null || true", check=False)
    run(c, f"fuser -k {port}/tcp 2>/dev/null || true", check=False)
    time.sleep(1)
    run(c, f"systemctl daemon-reload && systemctl enable {unit_name} && systemctl restart {unit_name}")
    time.sleep(1)
    active = run(c, f"systemctl is-active {unit_name}", check=False).strip()
    print(f"  selfsteal {unit_name}: {active}")
    return active == "active"


def ensure_ru_cert(ru: paramiko.SSHClient, domain: str) -> str | None:
    """Issue/reuse LE cert for RU domain. Returns cert dir or None."""
    if not domain:
        return None
    cert_dir = f"/etc/letsencrypt/live/{domain}"
    exists = run(ru, f"test -f {cert_dir}/fullchain.pem && echo OK || echo NO", check=False)
    if "OK" in exists:
        return cert_dir
    print(f"==> RU: certbot for {domain}")
    run(
        ru,
        r"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y certbot
""",
        check=False,
    )
    # free :80 if something holds it
    run(ru, "systemctl stop nginx apache2 caddy 2>/dev/null || true", check=False)
    run(ru, "ufw allow 80/tcp comment 'http-01' || true", check=False)
    out = run(
        ru,
        f"certbot certonly --standalone -d {domain} --non-interactive --agree-tos "
        f"--register-unsafely-without-email --preferred-challenges http || true",
        check=False,
    )
    exists = run(ru, f"test -f {cert_dir}/fullchain.pem && echo OK || echo NO", check=False)
    if "OK" in exists:
        return cert_dir
    print("  certbot failed; will use cloudflare Reality dest fallback")
    print(out[:500])
    return None


def deploy_nl_relay(nl: paramiko.SSHClient, inv: dict[str, str], sec: dict[str, str], ru_ip: str) -> None:
    print("==> NL: backup x-ui db (read-only safety)")
    run(
        nl,
        "mkdir -p /root/xeno-backups && "
        "cp -a /etc/x-ui/x-ui.db /root/xeno-backups/x-ui.db.$(date +%F-%H%M%S) 2>/dev/null || true && "
        "ls -la /root/xeno-backups | tail -n 5",
        check=False,
    )

    print("==> NL: install dedicated xeno relay xray (does not replace 3x-ui)")
    install_xray_binary(nl)
    run(nl, "mkdir -p /var/log/xeno && touch /var/log/xeno/.keep", check=False)

    nl_dom = inv.get("NL_DOMAIN", "").strip()
    if nl_dom and setup_selfsteal(
        nl,
        domain=nl_dom,
        cert_dir=f"/root/cert/{nl_dom}",
        unit_name="xeno-steal-nl",
    ):
        sec["RELAY_REALITY_SNI"] = nl_dom
        sec["RELAY_REALITY_DEST"] = "127.0.0.1:9443"
        persist_reality(sec)
        print(f"  NL Reality SelfSteal -> {nl_dom} via 127.0.0.1:9443")

    mapping = {
        **inv,
        **sec,
        "RELAY_PORT": inv.get("RELAY_PORT", "8443"),
    }
    tpl = (CONFIGS / "nl-relay-only.json.template").read_text(encoding="utf-8")
    cfg = render(tpl, mapping)
    # validate json
    json.loads(cfg)
    sftp_write(nl, "/usr/local/etc/xray/xeno-relay.json", cfg)

    unit = """[Unit]
Description=xeno.net NL relay (coexist alongside 3x-ui)
After=network.target nss-lookup.target

[Service]
Type=simple
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/xeno-relay.json
Restart=on-failure
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
"""
    sftp_write(nl, "/etc/systemd/system/xeno-relay.service", unit)
    run(nl, "systemctl daemon-reload && systemctl enable xeno-relay && systemctl restart xeno-relay")
    time.sleep(1)
    run(nl, "systemctl is-active xeno-relay")
    run(nl, f"ss -lntp | grep ':{mapping['RELAY_PORT']} ' || ss -lntp | grep {mapping['RELAY_PORT']}")

    # UFW: allow relay only from RU (additive; never ufw reset on NL)
    run(nl, "ufw status | head -n 5", check=False)
    # Drop any world-open 8443 if previously added (best-effort by comment/from)
    run(
        nl,
        "ufw status numbered | grep -E '8443' || true",
        check=False,
    )
    run(
        nl,
        f"ufw allow from {ru_ip} to any port {mapping['RELAY_PORT']} proto tcp comment 'xeno-relay-ru' || true",
        check=False,
    )
    run(nl, "ufw reload || true", check=False)
    # Ensure sacred units still up — never restart/stop them here
    run(nl, "systemctl is-active x-ui xeno-bot")


def deploy_ru(ru: paramiko.SSHClient, inv: dict[str, str], sec: dict[str, str]) -> None:
    print("==> RU: harden basics + xray relay")
    run(
        ru,
        r"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
timedatectl set-timezone UTC || true
apt-get update -y
apt-get install -y curl unzip jq ca-certificates ufw fail2ban
""",
    )
    install_xray_binary(ru)
    run(ru, "mkdir -p /var/log/xeno && touch /var/log/xeno/.keep", check=False)

    ru_dom = inv.get("RU_DOMAIN", "").strip()
    cert_dir = ensure_ru_cert(ru, ru_dom) if ru_dom else None
    if cert_dir and setup_selfsteal(
        ru,
        domain=ru_dom,
        cert_dir=cert_dir,
        unit_name="xeno-steal-ru",
    ):
        sec["BRIDGE_REALITY_SNI"] = ru_dom
        sec["BRIDGE_REALITY_DEST"] = "127.0.0.1:9443"
        persist_reality(sec)
        print(f"  RU Reality SelfSteal -> {ru_dom} via 127.0.0.1:9443")

    mapping = {
        **inv,
        **sec,
        "NL_EXIT_IP": inv["NL_EXIT_IP"],
        "RELAY_PORT": inv.get("RELAY_PORT", "8443"),
        "CLIENT_PORT": inv.get("CLIENT_PORT", "443"),
    }
    tpl = (CONFIGS / "relay.json.template").read_text(encoding="utf-8")
    cfg = render(tpl, mapping)
    json.loads(cfg)
    sftp_write(ru, "/usr/local/etc/xray/config.json", cfg)
    unit = """[Unit]
Description=xeno.net RU bridge Xray
After=network.target nss-lookup.target

[Service]
Type=simple
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json
Restart=on-failure
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
"""
    sftp_write(ru, "/etc/systemd/system/xray.service", unit)
    run(ru, "systemctl daemon-reload && systemctl enable xray && systemctl restart xray")
    time.sleep(1)
    run(ru, "systemctl is-active xray")
    run(ru, "ss -lntp | grep ':443 ' || true")
    run(
        ru,
        r"""
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 443/tcp comment 'xeno-client'
ufw --force enable
""",
    )
    # connectivity to NL relay
    run(
        ru,
        f"timeout 5 bash -c 'cat < /dev/null > /dev/tcp/{inv['NL_EXIT_IP']}/{inv.get('RELAY_PORT','8443')}' && echo NL_RELAY_TCP_OK",
        check=False,
    )


def build_sub(inv: dict[str, str], sec: dict[str, str]) -> str:
    from urllib.parse import quote, urlencode

    client = sec["CLIENT_UUID"]
    pbk = sec["BRIDGE_REALITY_PUBLIC_KEY"]
    sid = sec["BRIDGE_REALITY_SHORT_ID"]
    sni = sec.get("BRIDGE_REALITY_SNI", inv.get("REALITY_SNI", "www.cloudflare.com"))
    path = sec["BRIDGE_PATH"]
    # Prefer domain in client link; IP remains valid fallback
    ru = inv.get("RU_DOMAIN") or inv["RU_BRIDGE_IP"]
    port = inv.get("CLIENT_PORT", "443")
    q = urlencode(
        {
            "encryption": "none",
            "security": "reality",
            "sni": sni,
            "fp": "chrome",
            "pbk": pbk,
            "sid": sid,
            "type": "xhttp",
            "path": path,
            "mode": "stream-one",
        }
    )
    link = f"vless://{client}@{ru}:{port}?{q}#{quote('🇷🇺XENO RU')}"
    text = link + "\n"
    import base64

    b64 = base64.b64encode(text.encode()).decode()
    (SECRETS / "subscription.txt").write_text(text, encoding="utf-8")
    (SECRETS / "subscription.base64").write_text(b64, encoding="utf-8")
    return link


def publish_sub_on_ru(ru: paramiko.SSHClient, ru_ip: str, token: str | None = None) -> str:
    """Serve base64 body as text/plain (not HTML). Happ is picky about Content-Type."""
    import secrets as pysecrets

    token = token or pysecrets.token_urlsafe(18)
    b64 = (SECRETS / "subscription.base64").read_text(encoding="utf-8").strip() + "\n"
    run(ru, f"mkdir -p /var/www/xeno-sub/{token}")
    for name in ("sub.txt", "index.txt", "index.html"):
        sftp_write(ru, f"/var/www/xeno-sub/{token}/{name}", b64)

    server = f'''#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path("/var/www/xeno-sub")
TOKEN = "{token}"
BODY = (ROOT / TOKEN / "sub.txt").read_bytes()

class H(BaseHTTPRequestHandler):
    def _ok(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(BODY)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Profile-Update-Interval", "1")
        self.end_headers()
    def do_HEAD(self):
        p = self.path.split("?", 1)[0]
        if p.rstrip("/") in ("/" + TOKEN, "/sub/" + TOKEN) or p.startswith("/" + TOKEN + "/") or p.startswith("/sub/" + TOKEN + "/"):
            self._ok()
            return
        self.send_error(404)
    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p.rstrip("/") in ("/" + TOKEN, "/sub/" + TOKEN) or p.startswith("/" + TOKEN + "/") or p.startswith("/sub/" + TOKEN + "/"):
            self._ok()
            self.wfile.write(BODY)
            return
        self.send_error(404)
    def log_message(self, *a):
        return

ThreadingHTTPServer(("0.0.0.0", 2096), H).serve_forever()
'''
    sftp_write(ru, "/usr/local/bin/xeno-sub-server.py", server)
    unit = """[Unit]
Description=xeno subscription (text/plain)
After=network.target
[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/xeno-sub-server.py
Restart=on-failure
[Install]
WantedBy=multi-user.target
"""
    sftp_write(ru, "/etc/systemd/system/xeno-sub.service", unit)
    run(ru, "ufw allow 2096/tcp comment 'xeno-sub' || true; ufw reload || true", check=False)
    run(ru, "systemctl daemon-reload && systemctl enable xeno-sub && systemctl restart xeno-sub")
    url = f"http://{ru_ip}:2096/{token}/"
    (SECRETS / "subscription.url").write_text(url + "\n", encoding="utf-8")
    return url


def main() -> int:
    inv = load_env(INV)
    nl_acc = load_env(SECRETS / "nl-access.env")
    ru_acc = load_env(SECRETS / "ru-access.env")
    nl_ip = inv["NL_EXIT_IP"]
    ru_ip = inv["RU_BRIDGE_IP"]
    nl_pass = nl_acc["NL_SSH_PASS"]
    ru_pass = ru_acc["RU_SSH_PASS"]

    # Fresh dual-hop secrets for clean rebuild (drop legacy single-keypair files)
    for stale in (
        "reality.env",
        "uuids.env",
        "subscription.txt",
        "subscription.base64",
        "subscription.url",
    ):
        p = SECRETS / stale
        if p.exists():
            p.unlink()
            print(f"removed stale secrets/{stale}")
    sec = ensure_local_secrets(inv)
    print("Connecting NL…")
    nl = ssh_connect(nl_ip, nl_pass)
    sec = gen_reality_keys_on_nl(nl, sec)
    persist_reality(sec)
    deploy_nl_relay(nl, inv, sec, ru_ip)
    run(nl, "systemctl is-active x-ui xeno-bot")
    nl.close()

    print("Connecting RU…")
    ru = ssh_connect(ru_ip, ru_pass)
    deploy_ru(ru, inv, sec)
    link = build_sub(inv, sec)
    # Keep existing token if URL already published (avoid breaking Happ bookmarks)
    token = None
    if (SECRETS / "subscription.url").exists():
        existing = (SECRETS / "subscription.url").read_text(encoding="utf-8").strip()
        if existing.startswith("http://") or existing.startswith("https://"):
            token = existing.rstrip("/").split("/")[-1] or None
    url = publish_sub_on_ru(ru, ru_ip, token=token)
    ru.close()

    print("\n=== DONE ===")
    print("Happ subscription URL:", url)
    print("VLESS (RU Bridge XHTTP+Reality):", link)
    print("Legacy 3x-ui / trading xeno-bot on NL were not modified.")
    print("See docs/ops/rebuild-plan.md")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print("FATAL:", e, file=sys.stderr)
        raise
