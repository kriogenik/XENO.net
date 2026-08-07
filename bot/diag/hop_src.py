"""Classify hop access source: local canary vs RU entry vs other."""
from __future__ import annotations

import os

HOP_SRC_CANARY = "canary"
HOP_SRC_RU = "ru"
HOP_SRC_OTHER = "other"


def _looks_like_ipv4(v: str) -> bool:
    parts = v.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def ru_ip_set(extra: str | None = None) -> set[str]:
    ips: set[str] = set()
    for key in ("RU_PUBLIC_IP", "RU_BRIDGE_IP"):
        v = (os.environ.get(key) or "").strip()
        if v and _looks_like_ipv4(v):
            ips.add(v)
    if extra and _looks_like_ipv4(extra.strip()):
        ips.add(extra.strip())
    return ips


def classify_hop_src(ip: str | None, *, ru_ip: str | None = None) -> str:
    """Return canary | ru | other. Loopback = local hop canary."""
    if not ip:
        return HOP_SRC_OTHER
    raw = ip.strip().split("%", 1)[0]
    if raw in ("127.0.0.1", "::1", "0.0.0.0", "::") or raw.startswith("127."):
        return HOP_SRC_CANARY
    if raw in ru_ip_set(ru_ip):
        return HOP_SRC_RU
    return HOP_SRC_OTHER
