#!/usr/bin/env python3
"""Resume RU deploy after NL coexist relay is already up."""
from __future__ import annotations

import sys
from pathlib import Path

# reuse functions
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy_two_node import (  # noqa: E402
    INV,
    SECRETS,
    build_sub,
    deploy_ru,
    ensure_local_secrets,
    load_env,
    publish_sub_on_ru,
    ssh_connect,
)


def main() -> int:
    inv = load_env(INV)
    ru_acc = load_env(SECRETS / "ru-access.env")
    sec = ensure_local_secrets(inv)
    # reality.env must exist from NL step
    sec.update(load_env(SECRETS / "reality.env"))
    sec.update(load_env(SECRETS / "uuids.env"))
    ru = ssh_connect(inv["RU_BRIDGE_IP"], ru_acc["RU_SSH_PASS"])
    deploy_ru(ru, inv, sec)
    link = build_sub(inv, sec)
    url = publish_sub_on_ru(ru, inv["RU_BRIDGE_IP"])
    ru.close()
    print("\n=== DONE ===")
    print("Happ subscription URL:", url)
    print("VLESS (RU Bridge):", link)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
