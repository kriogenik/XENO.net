#!/usr/bin/env python3
"""Smoke-test: add+delete client on managed inbound; assert sacred IDs unchanged."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

from config import load_settings  # noqa: E402
from xui import SACRED_INBOUND_IDS, XuiClient  # noqa: E402


def client_count(api: XuiClient, inbound_id: int) -> int:
    ib = api.get_inbound(inbound_id)
    settings = ib.get("settings")
    if isinstance(settings, str):
        settings = json.loads(settings)
    return len((settings or {}).get("clients") or [])


def main() -> int:
    s = load_settings(require_token=True)
    api = XuiClient(s.xui_base_url, s.xui_api_token)
    sacred_before = {i: client_count(api, i) for i in sorted(SACRED_INBOUND_IDS)}
    xeno_before = client_count(api, s.xui_inbound_id)
    print(f"before sacred={sacred_before} managed={xeno_before}")

    email = f"smoke-{uuid.uuid4().hex[:8]}"
    cuid = str(uuid.uuid4())
    api.add_client(
        inbound_id=s.xui_inbound_id,
        email=email,
        client_uuid=cuid,
        sub_id=uuid.uuid4().hex[:16],
        comment="smoke-test",
    )
    xeno_mid = client_count(api, s.xui_inbound_id)
    sacred_mid = {i: client_count(api, i) for i in sorted(SACRED_INBOUND_IDS)}
    print(f"after add sacred={sacred_mid} managed={xeno_mid}")
    assert sacred_mid == sacred_before, "sacred inbound client count changed!"
    assert xeno_mid == xeno_before + 1

    api.delete_client(email)
    xeno_after = client_count(api, s.xui_inbound_id)
    print(f"after del managed={xeno_after}")
    assert xeno_after == xeno_before

    st = api.server_status()
    print(f"xray={st.xray_state} cpu={st.cpu:.1f}%")
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
