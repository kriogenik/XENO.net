"""Privacy-safe parsers for Xray access/error logs.

Never keep destination URLs — strip `to …` / `tcp:host:port` destination fields.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from diag.classify import classify_access_action, classify_error_line

# 2024/08/02 12:00:00.123456 from 1.2.3.4:12345 accepted tcp:example.com:443 email: tg-1 [a >> b]
_ACCESS = re.compile(
    r"^(?P<ts>\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+"
    r"from\s+(?P<ip>[0-9a-fA-F.:]+):\d+\s+"
    r"(?P<action>accepted|rejected|blocked)\s+"
    r"(?:(?P<dest>\S+)\s+)?"
    r"(?:email:\s*)?(?P<email>tg-\d+|issued-\d+-[0-9a-f]+|xeno-relay-hop|xeno-[0-9a-f]{8})?"
    r".*?(?:\[(?P<route>[^\]]+)\])?",
    re.I,
)

_EMAIL_ANY = re.compile(
    r"\b(?:email:\s*)?(?P<em>tg-\d+|issued-\d+-[0-9a-f]+|xeno-relay-hop|xeno-[0-9a-f]{8})\b",
    re.I,
)
_TS_PREFIX = re.compile(r"^(?P<ts>\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)")


@dataclass(frozen=True)
class ParsedEvent:
    ts: int  # unix seconds UTC
    day: str  # YYYY-MM-DD
    email: str | None
    action: str  # accepted | rejected | error
    error_class: str | None
    src_ip_masked: str | None
    route: str | None
    source_kind: str  # access | error
    hop: bool = False


def mask_ip(ip: str) -> str:
    if ":" in ip:
        # IPv6 → keep /48-ish first 3 hextets
        parts = ip.split(":")
        return ":".join(parts[:3] + ["*"])
    octets = ip.split(".")
    if len(octets) == 4:
        return f"{octets[0]}.{octets[1]}.{octets[2]}.0/24"
    return "*"


def _parse_ts(ts: str) -> tuple[int, str]:
    raw = ts.strip()
    if "." in raw:
        raw = raw.split(".", 1)[0]
    dt = datetime.strptime(raw, "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp()), dt.strftime("%Y-%m-%d")


def _route_host(route: str | None) -> str:
    return (route or "").lower()


def parse_access_line(line: str) -> ParsedEvent | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = _ACCESS.search(line)
    email: str | None = None
    action = "accepted"
    ip = None
    route = None
    ts_s: str | None = None
    if m:
        ts_s = m.group("ts")
        ip = m.group("ip")
        action = m.group("action").lower()
        email = (m.group("email") or "").strip() or None
        route = (m.group("route") or "").strip() or None
        # destination deliberately ignored (m.group("dest"))
    else:
        tm = _TS_PREFIX.match(line)
        if not tm:
            return None
        ts_s = tm.group("ts")
        em = _EMAIL_ANY.search(line)
        if not em:
            return None
        email = em.group("em")
        if "rejected" in line.lower() or "blocked" in line.lower():
            action = "rejected"
        ip_m = re.search(r"from\s+([0-9a-fA-F.:]+):\d+", line)
        ip = ip_m.group(1) if ip_m else None
        rm = re.search(r"\[([^\]]+)\]", line)
        route = rm.group(1) if rm else None

    assert ts_s
    ts, day = _parse_ts(ts_s)
    if not email:
        em = _EMAIL_ANY.search(line)
        email = em.group("em") if em else None
    err = classify_access_action(action)
    hop = bool(email and email.lower() == "xeno-relay-hop")
    return ParsedEvent(
        ts=ts,
        day=day,
        email=email.lower() if email else None,
        action=action,
        error_class=err,
        src_ip_masked=mask_ip(ip) if ip else None,
        route=route,
        source_kind="access",
        hop=hop,
    )


def parse_error_line(line: str) -> ParsedEvent | None:
    line = line.strip()
    if not line:
        return None
    tm = _TS_PREFIX.match(line)
    if not tm:
        return None
    ts, day = _parse_ts(tm.group("ts"))
    em = _EMAIL_ANY.search(line)
    email = em.group("em").lower() if em else None
    klass = classify_error_line(line)
    hop = bool(email and email == "xeno-relay-hop")
    return ParsedEvent(
        ts=ts,
        day=day,
        email=email,
        action="error",
        error_class=klass,
        src_ip_masked=None,
        route=None,
        source_kind="error",
        hop=hop,
    )


def is_nl_direct_route(route: str | None) -> bool:
    r = _route_host(route)
    return "xeno-direct" in r or "direct-in" in r


def is_ru_client_route(route: str | None) -> bool:
    r = _route_host(route)
    return "client-in" in r or "nl-exit" in r or not r


def parse_lines(lines: Iterable[str], *, kind: str) -> list[ParsedEvent]:
    out: list[ParsedEvent] = []
    for line in lines:
        ev = parse_access_line(line) if kind == "access" else parse_error_line(line)
        if ev:
            # Strip any accidental destination-looking leftovers from email field
            if ev.email and ("/" in ev.email or ":" in ev.email):
                continue
            out.append(ev)
    return out
