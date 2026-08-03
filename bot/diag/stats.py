"""Scrape Xray StatsService counters (uplink/downlink per user email)."""
from __future__ import annotations

import json
import re
import subprocess
from typing import Mapping

from xray_sync import XRAY_API_SERVER, XRAY_BIN


def _parse_stats_output(text: str) -> dict[str, dict[str, int]]:
    """Return email -> {up, down} from statsquery / stats list output."""
    out: dict[str, dict[str, int]] = {}
    # user>>>tg-123>>>traffic>>>uplink
    pat = re.compile(
        r"user>>>(?P<email>[^>]+)>>>traffic>>>(?P<dir>uplink|downlink)(?:>>>)?\s*(?P<val>\d+)?",
        re.I,
    )
    # Also JSON-ish: "name": "user>>>…", "value": N
    for m in re.finditer(
        r'"name"\s*:\s*"user>>>(?P<email>[^>]+)>>>traffic>>>(?P<dir>uplink|downlink)"\s*,\s*"value"\s*:\s*(?P<val>\d+)',
        text,
        re.I,
    ):
        email = m.group("email").lower()
        bucket = out.setdefault(email, {"up": 0, "down": 0})
        if m.group("dir").lower() == "uplink":
            bucket["up"] = int(m.group("val"))
        else:
            bucket["down"] = int(m.group("val"))
    for line in text.splitlines():
        m = pat.search(line)
        if not m:
            continue
        email = m.group("email").lower()
        bucket = out.setdefault(email, {"up": 0, "down": 0})
        val = int(m.group("val") or 0)
        # Sometimes value is on next token
        if not m.group("val"):
            nums = re.findall(r"\b(\d+)\b", line)
            val = int(nums[-1]) if nums else 0
        if m.group("dir").lower() == "uplink":
            bucket["up"] = val
        else:
            bucket["down"] = val
    return out


def query_user_traffic_local(*, server: str = XRAY_API_SERVER) -> dict[str, dict[str, int]]:
    try:
        r = subprocess.run(
            [XRAY_BIN, "api", "statsquery", f"--server={server}", "-pattern", "user>>>"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        text = (r.stdout or "") + "\n" + (r.stderr or "")
        if r.returncode != 0 and "stat" not in text.lower():
            # fallback older CLI
            r2 = subprocess.run(
                [XRAY_BIN, "api", "stats", f"--server={server}", "-name", ""],
                capture_output=True,
                text=True,
                timeout=30,
            )
            text = (r2.stdout or "") + "\n" + (r2.stderr or "")
        return _parse_stats_output(text)
    except (OSError, subprocess.TimeoutExpired):
        return {}


def query_user_traffic_remote(ssh, *, server: str = XRAY_API_SERVER) -> dict[str, dict[str, int]]:
    cmd = (
        f"{XRAY_BIN} api statsquery --server={server} -pattern 'user>>>' 2>/dev/null || "
        f"{XRAY_BIN} api stats --server={server} 2>/dev/null | head -c 200000"
    )
    try:
        _i, o, e = ssh.exec_command(cmd, timeout=45)
        text = o.read().decode("utf-8", "replace") + e.read().decode("utf-8", "replace")
        return _parse_stats_output(text)
    except Exception:
        return {}


def merge_traffic(
    base: Mapping[str, Mapping[str, int]],
    extra: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {k: {"up": v.get("up", 0), "down": v.get("down", 0)} for k, v in base.items()}
    for email, vals in extra.items():
        bucket = out.setdefault(email, {"up": 0, "down": 0})
        bucket["up"] = max(bucket["up"], int(vals.get("up", 0)))
        bucket["down"] = max(bucket["down"], int(vals.get("down", 0)))
    return out
