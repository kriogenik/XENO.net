#!/usr/bin/env python3
"""Offline validation: render Xray templates and build Happ subscription."""
from __future__ import annotations

import base64
import json
import re
import secrets
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "secrets" / "dry-run"


def b64url(n: int = 32) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(n)).decode().rstrip("=")


def render(src: Path, dst: Path, env: dict[str, str]) -> None:
    text = src.read_text(encoding="utf-8")
    missing = [k for k in re.findall(r"\{\{(\w+)\}\}", text) if not env.get(k)]
    if missing:
        raise SystemExit(f"missing template vars: {', '.join(sorted(set(missing)))}")
    for k, v in env.items():
        text = text.replace("{{" + k + "}}", v)
    dst.write_text(text, encoding="utf-8")


def main() -> None:
    print("==> Dry-run (no SSH)")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    env = {
        "NL_EXIT_IP": "203.0.113.10",
        "RU_BRIDGE_IP": "203.0.113.20",
        "YC_WHITELIST_IP": "203.0.113.30",
        "CLIENT_PORT": "443",
        "RELAY_PORT": "8443",
        "REALITY_SNI": "www.cloudflare.com",
        "REALITY_DEST": "www.cloudflare.com:443",
        "REALITY_PRIVATE_KEY": b64url(),
        "REALITY_PUBLIC_KEY": b64url(),
        "REALITY_SHORT_ID": secrets.token_hex(4),
        "CLIENT_UUID": str(uuid.uuid4()),
        "RELAY_UUID": str(uuid.uuid4()),
        "DOMAIN": "example.com",
    }

    (OUT / "uuids.env").write_text(
        f"CLIENT_UUID={env['CLIENT_UUID']}\nRELAY_UUID={env['RELAY_UUID']}\n",
        encoding="utf-8",
    )
    (OUT / "reality.env").write_text(
        "\n".join(
            [
                f"REALITY_PRIVATE_KEY={env['REALITY_PRIVATE_KEY']}",
                f"REALITY_PUBLIC_KEY={env['REALITY_PUBLIC_KEY']}",
                f"REALITY_SHORT_ID={env['REALITY_SHORT_ID']}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    render(
        ROOT / "configs" / "xray" / "nl-exit-standalone.json.template",
        OUT / "nl-exit.json",
        env,
    )
    render(
        ROOT / "configs" / "xray" / "relay.json.template",
        OUT / "relay.json",
        env,
    )
    for name in ("nl-exit.json", "relay.json"):
        json.loads((OUT / name).read_text(encoding="utf-8"))
        print(f"  ok json: {name}")

    mini = OUT / "mini-root"
    (mini / "inventory").mkdir(parents=True)
    (mini / "secrets").mkdir(parents=True)
    (mini / "inventory" / "hosts.env").write_text(
        "\n".join(
            [
                "SSH_USER=root",
                f"NL_EXIT_IP={env['NL_EXIT_IP']}",
                f"RU_BRIDGE_IP={env['RU_BRIDGE_IP']}",
                f"YC_WHITELIST_IP={env['YC_WHITELIST_IP']}",
                f"DOMAIN={env['DOMAIN']}",
                f"REALITY_SNI={env['REALITY_SNI']}",
                f"CLIENT_PORT={env['CLIENT_PORT']}",
                f"RELAY_PORT={env['RELAY_PORT']}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    shutil.copy(OUT / "uuids.env", mini / "secrets" / "uuids.env")
    shutil.copy(OUT / "reality.env", mini / "secrets" / "reality.env")
    (mini / "secrets" / "panel.env").write_text(
        "PANEL_PORT=24567\nSUB_PORT=2096\n", encoding="utf-8"
    )

    sys.path.insert(0, str(ROOT / "scripts" / "lib"))
    from build_subscription import main as build_main

    sys.argv = ["build_subscription.py", "--root", str(mini)]
    build_main()

    for name in ("subscription.txt", "subscription.base64", "subscription.url"):
        shutil.copy(mini / "secrets" / name, OUT / name)

    text = (OUT / "subscription.txt").read_text(encoding="utf-8").strip().splitlines()
    assert len(text) == 4, text
    raw = base64.b64decode((OUT / "subscription.base64").read_text(encoding="utf-8"))
    assert raw.decode().strip().splitlines() == text
    url = (OUT / "subscription.url").read_text(encoding="utf-8").strip()
    assert url.startswith("http://203.0.113.10:2096/")
    print(f"  ok profiles ({len(text)}):")
    for line in text:
        print("   ", line.rsplit("#", 1)[-1])
    print("  ok url:", url)
    print("==> Dry-run passed - artifacts in secrets/dry-run/")
    print()
    print("Order servers: docs/ops/order-checklist.md")
    print("Fill inventory/hosts.env then run ./scripts/deploy-all.sh")


if __name__ == "__main__":
    main()
