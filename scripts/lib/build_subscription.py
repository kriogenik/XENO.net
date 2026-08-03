#!/usr/bin/env python3
"""Build multi-node Happ subscription from inventory + secrets."""
from __future__ import annotations

import argparse
import base64
import pathlib
import secrets
import urllib.parse


def load_env(path: pathlib.Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data


def vless(
    *,
    uuid: str,
    address: str,
    port: str,
    name: str,
    network: str,
    pbk: str,
    sid: str,
    sni: str,
    flow: str = "",
    path: str = "",
) -> str:
    q: dict[str, str] = {
        "encryption": "none",
        "security": "reality",
        "sni": sni,
        "fp": "chrome",
        "pbk": pbk,
        "sid": sid,
        "type": network,
    }
    if flow:
        q["flow"] = flow
    if network == "xhttp":
        q["path"] = path or "/xeno"
    elif path:
        q["path"] = path
    query = urllib.parse.urlencode(q)
    return f"vless://{uuid}@{address}:{port}?{query}#{urllib.parse.quote(name)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path, required=True)
    ap.add_argument("--publish-url-only", action="store_true")
    args = ap.parse_args()
    root: pathlib.Path = args.root
    secrets_dir = root / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)

    env: dict[str, str] = {}
    inv_local = root / "inventory" / "hosts.local.env"
    inv_public = root / "inventory" / "hosts.env"
    env.update(load_env(inv_local if inv_local.exists() else inv_public))
    for name in ("reality.env", "uuids.env", "panel.env"):
        env.update(load_env(secrets_dir / name))

    required = [
        "CLIENT_UUID",
        "REALITY_PUBLIC_KEY",
        "REALITY_SHORT_ID",
        "NL_EXIT_IP",
        "RU_BRIDGE_IP",
        "YC_WHITELIST_IP",
    ]
    missing = [k for k in required if not env.get(k) or env[k].startswith("CHANGE_ME_")]
    if missing:
        raise SystemExit(f"missing env: {', '.join(missing)}")

    client_uuid = env["CLIENT_UUID"]
    pbk = env["REALITY_PUBLIC_KEY"]
    sid = env["REALITY_SHORT_ID"]
    sni = env.get("REALITY_SNI", "www.cloudflare.com")
    client_port = env.get("CLIENT_PORT", "443")
    nl = env["NL_EXIT_IP"]
    ru = env["RU_BRIDGE_IP"]
    yc = env["YC_WHITELIST_IP"]
    domain = env.get("DOMAIN", "").strip()

    links = [
        vless(
            uuid=client_uuid,
            address=ru,
            port=client_port,
            name="RU Bridge",
            network="xhttp",
            pbk=pbk,
            sid=sid,
            sni=sni,
            path="/xeno",
        ),
        vless(
            uuid=client_uuid,
            address=yc,
            port=client_port,
            name="YC Whitelist",
            network="xhttp",
            pbk=pbk,
            sid=sid,
            sni=sni,
            path="/xeno",
        ),
        vless(
            uuid=client_uuid,
            address=nl,
            port=client_port,
            name="NL Direct",
            network="tcp",
            pbk=pbk,
            sid=sid,
            sni=sni,
            flow="xtls-rprx-vision",
        ),
    ]

    if domain:
        q = urllib.parse.urlencode(
            {
                "encryption": "none",
                "security": "tls",
                "sni": domain,
                "fp": "chrome",
                "type": "ws",
                "host": domain,
                "path": "/cdn-ws",
            }
        )
        links.append(
            f"vless://{client_uuid}@{domain}:443?{q}#{urllib.parse.quote('CDN Fallback')}"
        )

    text = "\n".join(links) + "\n"
    b64 = base64.b64encode(text.encode()).decode()
    (secrets_dir / "subscription.txt").write_text(text, encoding="utf-8")
    (secrets_dir / "subscription.base64").write_text(b64, encoding="utf-8")

    sub_token = env.get("SUB_TOKEN")
    if not sub_token:
        sub_token = secrets.token_urlsafe(24)
        panel_env = secrets_dir / "panel.env"
        with panel_env.open("a", encoding="utf-8") as f:
            if panel_env.exists() and panel_env.stat().st_size:
                f.write(f"SUB_TOKEN={sub_token}\n")
            else:
                f.write(f"SUB_TOKEN={sub_token}\n")

    # Dedicated subscription port (avoids clash with 3x-ui). Always https for Happ.
    sub_port = env.get("SUB_PORT", "2096")
    sub_host = env.get("SUB_PUBLIC_HOST") or env.get("NL_DOMAIN") or domain or nl
    sub_base = (env.get("SUB_PUBLIC_BASE") or "").strip().rstrip("/")
    if sub_base.startswith("http://"):
        sub_base = "https://" + sub_base[len("http://") :]
    if not sub_base:
        sub_base = f"https://{sub_host}:{sub_port}"
    elif not sub_base.startswith("https://"):
        sub_base = "https://" + sub_base.lstrip("/")
    sub_url = f"{sub_base}/{sub_token}/"
    (secrets_dir / "subscription.url").write_text(sub_url + "\n", encoding="utf-8")

    # Persist SUB_PORT / SUB_TOKEN if needed
    panel = load_env(secrets_dir / "panel.env")
    lines = []
    if (secrets_dir / "panel.env").exists():
        for line in (secrets_dir / "panel.env").read_text(encoding="utf-8").splitlines():
            if line.startswith("SUB_TOKEN=") or line.startswith("SUB_PORT="):
                continue
            lines.append(line)
    lines.append(f"SUB_TOKEN={sub_token}")
    lines.append(f"SUB_PORT={sub_port}")
    (secrets_dir / "panel.env").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(text)
    print("---")
    print(sub_url)


if __name__ == "__main__":
    main()
