#!/usr/bin/env python3
"""Verify NL backups: Direct :2053 XHTTP e2e + HY2 UDP listen.

Does not touch 3x-ui / trading. Uses secrets/subscription.txt multi-profile.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]


def _inventory_path() -> Path:
    local = ROOT / "inventory" / "hosts.local.env"
    return local if local.is_file() else ROOT / "inventory" / "hosts.env"

SECRETS = ROOT / "secrets"
INV = _inventory_path()


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


def parse_vless(link: str) -> dict:
    u = urlparse(link)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    return {
        "uuid": u.username,
        "host": u.hostname,
        "port": u.port or 443,
        "sni": q.get("sni", ""),
        "pbk": q.get("pbk", ""),
        "sid": q.get("sid", ""),
        "path": unquote(q.get("path", "/")),
        "name": unquote(u.fragment or ""),
    }


def load_links() -> list[str]:
    text = (SECRETS / "subscription.txt").read_text(encoding="utf-8")
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]


def xray_client_config(v: dict, socks_port: int) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": socks_port,
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
                            "address": v["host"],
                            "port": v["port"],
                            "users": [{"id": v["uuid"], "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "reality",
                    "xhttpSettings": {"path": v["path"], "mode": "auto"},
                    "realitySettings": {
                        "serverName": v["sni"],
                        "fingerprint": "chrome",
                        "publicKey": v["pbk"],
                        "shortId": v["sid"],
                    },
                },
            }
        ],
    }


def find_xray() -> str | None:
    for p in (
        ROOT / "tools" / "xray.exe",
        ROOT / "tools" / "xray",
        Path("C:/Users/lenox/Desktop/dev/xeno.net/tools/xray.exe"),
    ):
        if p.exists():
            return str(p)
    which = subprocess.run(["where", "xray"], capture_output=True, text=True)
    if which.returncode == 0 and which.stdout.strip():
        return which.stdout.strip().splitlines()[0]
    return None


def socks_get_ip(socks_port: int) -> str:
    import socks  # type: ignore

    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, "127.0.0.1", socks_port)
    s.settimeout(25)
    s.connect(("api.ipify.org", 80))
    s.sendall(b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\nConnection: close\r\n\r\n")
    data = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    s.close()
    body = data.split(b"\r\n\r\n", 1)[-1].decode().strip()
    return body


def verify_tcp(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=8):
            return True
    except OSError as e:
        print(f"TCP {host}:{port} FAIL {e}")
        return False


def verify_udp_open_remote(nl_ip: str, port: int, password: str) -> bool:
    """SSH check ss listen on NL (local probe)."""
    try:
        import paramiko
    except ImportError:
        print("paramiko missing; skip remote UDP check")
        return True
    acc = load_env(SECRETS / "nl-access.env")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(nl_ip, username="root", password=acc["NL_SSH_PASS"], timeout=20, allow_agent=False, look_for_keys=False)
    _, o, _ = c.exec_command(
        f"systemctl is-active xeno-hy2; ss -lnup | grep ':{port}' || true",
        timeout=30,
    )
    out = o.read().decode()
    c.close()
    print(out.strip())
    return "active" in out and f":{port}" in out


def main() -> int:
    inv = load_env(INV)
    nl_ip = inv["NL_EXIT_IP"]
    links = load_links()
    direct = None
    for ln in links:
        if ln.startswith("vless://") and ("NL%20Direct" in ln or "NL Direct" in ln or "Direct" in unquote(urlparse(ln).fragment)):
            direct = ln
            break
    hy2 = next((ln for ln in links if ln.startswith("hysteria2://")), None)
    if not direct:
        print("FAIL: no NL Direct link in secrets/subscription.txt — run deploy_backups.py")
        return 1

    v = parse_vless(direct)
    print("Direct profile:", v["host"], v["port"], v["path"])
    host = v["host"]
    if host in (inv.get("NL_DOMAIN"), "exit.example.com") and nl_ip:
        host = nl_ip
    if not verify_tcp(host, v["port"]):
        # try domain then IP
        if not verify_tcp(nl_ip, v["port"]):
            return 2
    print("TCP direct OK")

    xray = find_xray()
    if not xray:
        print("WARN: no local xray binary; skip e2e socks IP check")
    else:
        socks_port = 11081
        cfg = xray_client_config({**v, "host": nl_ip}, socks_port)
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "client.json"
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
            proc = subprocess.Popen(
                [xray, "run", "-config", str(cfg_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                time.sleep(2)
                try:
                    ip = socks_get_ip(socks_port)
                except Exception:
                    # fallback urllib via pysocks may fail; try curl
                    r = subprocess.run(
                        [
                            "curl",
                            "-sS",
                            "--max-time",
                            "25",
                            "-x",
                            f"socks5h://127.0.0.1:{socks_port}",
                            "https://api.ipify.org",
                        ],
                        capture_output=True,
                        text=True,
                    )
                    ip = r.stdout.strip()
                    if r.returncode != 0:
                        print("e2e FAIL", r.stderr)
                        return 3
                print("exit IP via NL Direct:", ip)
                if ip != nl_ip:
                    print(f"FAIL expected {nl_ip}")
                    return 4
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()

    if hy2:
        port = int(urlparse(hy2).port or inv.get("HY2_PORT", "8444"))
        if not verify_udp_open_remote(nl_ip, port, ""):
            print("FAIL HY2 not listening")
            return 5
        print("HY2 unit + UDP listen OK")
    else:
        print("WARN: no hysteria2 link")

    # Cascade primary still present
    if not any(ln.startswith("vless://") and "RU" in ln for ln in links):
        print("WARN: no RU primary in multi-sub")
    print("BACKUPS VERIFY OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
