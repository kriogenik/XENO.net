#!/usr/bin/env python3
"""Migrate Reality SelfSteal from Python ThreadingTCPServer → dedicated nginx.

Python SelfSteal wedges under Reality probe load (LISTEN + Recv-Q, TLS timeout)
while systemd still shows active. nginx is the stable product choice for :9443.

Idempotent. Safe on NL coexist (isolated nginx -c, does not touch sibling sites).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v.strip().strip("'\"")
    return env


def run(c: paramiko.SSHClient, cmd: str, check: bool = True) -> str:
    print(" $", cmd[:200].replace("\n", " "))
    _, o, e = c.exec_command(cmd, timeout=300)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        try:
            print(out.rstrip()[:3000])
        except UnicodeEncodeError:
            sys.stdout.buffer.write((out.rstrip()[:3000] + "\n").encode("utf-8", "replace"))
    if check and code != 0:
        raise RuntimeError(f"failed ({code}): {cmd}\n{err[:1500]}")
    return out


def sftp_write(c: paramiko.SSHClient, remote: str, data: str) -> None:
    sftp = c.open_sftp()
    with sftp.file(remote, "w") as f:
        f.write(data if data.endswith("\n") else data + "\n")
    sftp.close()


def resolve_cert_dir(c: paramiko.SSHClient, domain: str) -> str | None:
    candidates = [
        f"/etc/letsencrypt/live/{domain}",
        "/etc/letsencrypt/live/nl.xenoworth.ru",
    ]
    # Also try whatever steal python was using from unit if present
    for d in candidates:
        chk = run(c, f"test -f {d}/fullchain.pem && test -f {d}/privkey.pem && echo OK || echo NO", check=False)
        if "OK" in chk:
            return d
    # Discover first LE live dir with both files
    out = run(
        c,
        "ls -d /etc/letsencrypt/live/*/ 2>/dev/null | head -n 20 || true",
        check=False,
    )
    for line in out.splitlines():
        d = line.strip().rstrip("/")
        if not d:
            continue
        chk = run(c, f"test -f {d}/fullchain.pem && test -f {d}/privkey.pem && echo OK || echo NO", check=False)
        if "OK" in chk:
            return d
    return None


def main() -> int:
    nl = load_env(ROOT / "secrets" / "nl-access.env")
    hosts = load_env(ROOT / "inventory" / "hosts.local.env")
    bridge = load_env(ROOT / "secrets" / "bridge.env")
    host = nl.get("NL_EXIT_IP") or nl.get("NL_SSH_HOST")
    user = nl.get("NL_SSH_USER") or "root"
    password = nl.get("NL_SSH_PASS") or nl.get("NL_SSH_PASSWORD")
    if not host or not password:
        print("ERROR: need NL_EXIT_IP/NL_SSH_HOST and NL_SSH_PASS in secrets/nl-access.env")
        return 1
    domain = (
        bridge.get("NL_DOMAIN")
        or bridge.get("RELAY_SNI")
        or hosts.get("NL_DOMAIN")
        or "nl.xenoworth.ru"
    )

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=password, timeout=30, allow_agent=False, look_for_keys=False)

    print("==> ensure nginx package")
    run(
        c,
        "export DEBIAN_FRONTEND=noninteractive; "
        "command -v nginx >/dev/null || apt-get install -y nginx",
        check=False,
    )

    cert_dir = resolve_cert_dir(c, domain)
    if not cert_dir:
        print("ERROR: no LE cert found for SelfSteal")
        return 1
    print("cert_dir", cert_dir)

    run(c, "mkdir -p /var/www/xeno-steal /etc/xeno /var/log/xeno /run")
    run(
        c,
        "test -f /var/www/xeno-steal/index.html || "
        "echo '<!doctype html><title>ok</title>ok' > /var/www/xeno-steal/index.html",
        check=False,
    )

    nginx_conf = f"""# XENO Reality SelfSteal — dedicated nginx (do not merge into sibling site configs)
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
        listen 127.0.0.1:9443 ssl;
        server_name {domain} _;
        ssl_certificate {cert_dir}/fullchain.pem;
        ssl_certificate_key {cert_dir}/privkey.pem;
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

    unit = """[Unit]
Description=XENO Reality SelfSteal HTTPS (nginx :9443)
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
    sftp_write(c, "/etc/systemd/system/xeno-steal-nl.service", unit)

    # Free port from old python steal
    run(c, "systemctl stop xeno-steal-nl 2>/dev/null || true", check=False)
    run(c, "fuser -k 9443/tcp 2>/dev/null || true", check=False)
    time.sleep(1)
    run(c, "systemctl daemon-reload && systemctl enable xeno-steal-nl && systemctl restart xeno-steal-nl")
    time.sleep(2)
    run(c, "systemctl is-active xeno-steal-nl")
    run(c, "curl -sk --max-time 5 -o /dev/null -w '%{http_code}\\n' https://127.0.0.1:9443/")
    run(c, "ss -lntp | grep 9443 || true")
    # Keep python script around but unused (rollback reference)
    run(c, "test -f /usr/local/bin/xeno-steal-nl.py && echo python_script_kept || true", check=False)
    c.close()
    print("SelfSteal nginx migration OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
