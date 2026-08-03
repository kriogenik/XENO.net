#!/usr/bin/env python3
"""List 3x-ui inbounds and highlight the XENO-managed inbound for bot.env."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

from config import load_settings  # noqa: E402
from xui import SACRED_INBOUND_PORTS, XuiClient  # noqa: E402


def main() -> int:
    s = load_settings(require_token=True)
    if not s.xui_base_url or not s.xui_api_token:
        print("Set XUI_BASE_URL and XUI_API_TOKEN in secrets/bot.env")
        return 1
    api = XuiClient(s.xui_base_url, s.xui_api_token)
    rows = api.list_inbounds()
    print(f"{'id':>4}  {'port':>6}  {'proto':<8}  remark / tag")
    print("-" * 60)
    xeno_candidates: list[tuple[int, int, str]] = []
    for ib in rows:
        iid = int(ib.get("id") or 0)
        port = int(ib.get("port") or 0)
        remark = str(ib.get("remark") or ib.get("tag") or "")
        proto = str(ib.get("protocol") or "")
        sacred = " SACRED" if port in SACRED_INBOUND_PORTS else ""
        print(f"{iid:4d}  {port:6d}  {proto:<8}  {remark}{sacred}")
        if port not in SACRED_INBOUND_PORTS and "xeno" in remark.lower():
            xeno_candidates.append((iid, port, remark))
    print()
    if s.xui_inbound_id:
        print(f"Configured XUI_INBOUND_ID={s.xui_inbound_id}")
    if xeno_candidates:
        best = xeno_candidates[0]
        print(f"Suggested: XUI_INBOUND_ID={best[0]}  # {best[2]} :{best[1]}")
    elif rows:
        print("No inbound with 'xeno' in remark — pick id manually (skip sacred IDs from env).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
