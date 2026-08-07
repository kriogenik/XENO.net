"""Population / path metrics from access logs (no destinations persisted)."""
from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from diag import HOP_EMAIL, NL_ACCESS_LOG, NL_ERROR_LOG, RU_ACCESS_LOG, RU_ERROR_LOG
from diag.classify import classify_error_line
from diag.hop_src import HOP_SRC_CANARY, HOP_SRC_RU, classify_hop_src
from diag.parse import parse_access_line
from ops_events import emit as emit_ops

KIND_PATH_STATS = "path_stats"
STATE_PATH = Path("/var/log/xeno/path_stats.json")

_WINDOWS = (5 * 60, 15 * 60, 60 * 60, 24 * 60 * 60)
_TS = re.compile(r"^(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})")


@dataclass
class WindowAgg:
    unique_ru: set[str] = field(default_factory=set)
    unique_nl_exit: set[str] = field(default_factory=set)
    unique_ru_direct: set[str] = field(default_factory=set)
    unique_nl_direct: set[str] = field(default_factory=set)
    accepts_ru: int = 0
    accepts_nl_exit: int = 0
    accepts_ru_bypass: int = 0
    accepts_nl_direct: int = 0
    hop_canary: int = 0
    hop_ru_sourced: int = 0
    hop_other: int = 0
    short_gaps: int = 0  # consecutive same-email accepts <3s
    error_xhttp: int = 0
    error_handshake: int = 0
    last_by_email: dict[str, int] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        ratio = None
        if self.hop_ru_sourced > 0:
            ratio = round(self.accepts_nl_exit / self.hop_ru_sourced, 2)
        elif self.accepts_nl_exit > 0:
            ratio = float("inf")
        short_ratio = None
        if self.accepts_ru > 0:
            short_ratio = round(self.short_gaps / self.accepts_ru, 3)
        return {
            "unique_ru": len(self.unique_ru),
            "unique_nl_exit": len(self.unique_nl_exit),
            "unique_ru_bypass": len(self.unique_ru_direct),
            "unique_nl_direct": len(self.unique_nl_direct),
            "accepts_ru": self.accepts_ru,
            "accepts_nl_exit": self.accepts_nl_exit,
            "accepts_ru_bypass": self.accepts_ru_bypass,
            "accepts_nl_direct": self.accepts_nl_direct,
            "hop_canary": self.hop_canary,
            "hop_ru_sourced": self.hop_ru_sourced,
            "hop_other": self.hop_other,
            "short_session_events": self.short_gaps,
            "short_session_ratio": short_ratio,
            "nl_exit_per_hop_ru": ratio if ratio != float("inf") else "inf",
            "error_xhttp": self.error_xhttp,
            "error_handshake": self.error_handshake,
        }


def _parse_ts_line(line: str) -> int | None:
    m = _TS.match(line.strip())
    if not m:
        return None
    raw = m.group(1)
    dt = datetime.strptime(raw, "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _route_kind(route: str | None) -> str:
    r = (route or "").lower()
    if "nl-exit" in r:
        return "nl_exit"
    if "-> direct" in r or "→ direct" in r or "client-in -> direct" in r:
        return "bypass"
    if "xeno-direct" in r:
        return "nl_direct"
    return "other"


def _tail_lines(path: str | Path, *, max_bytes: int = 8_000_000) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    size = p.stat().st_size
    with p.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()
        data = f.read()
    return data.decode("utf-8", "replace").splitlines()


def _ingest_access(
    lines: Iterable[str],
    *,
    host: str,
    now: int,
    windows: dict[int, WindowAgg],
    ru_ip: str | None,
) -> None:
    for line in lines:
        ts = _parse_ts_line(line)
        if ts is None or ts > now + 60:
            continue
        ev = parse_access_line(line)
        if not ev or ev.action != "accepted":
            continue
        age = now - ev.ts
        for wsec, agg in windows.items():
            if age > wsec:
                continue
            if host == "nl" and ev.hop:
                kind = classify_hop_src(ev.src_ip, ru_ip=ru_ip)
                if kind == HOP_SRC_CANARY:
                    agg.hop_canary += 1
                elif kind == HOP_SRC_RU:
                    agg.hop_ru_sourced += 1
                else:
                    agg.hop_other += 1
                continue
            if not ev.email or ev.email == HOP_EMAIL:
                continue
            rk = _route_kind(ev.route)
            if host == "ru":
                agg.accepts_ru += 1
                agg.unique_ru.add(ev.email)
                prev = agg.last_by_email.get(ev.email)
                if prev is not None and 0 < (ev.ts - prev) < 3:
                    agg.short_gaps += 1
                agg.last_by_email[ev.email] = ev.ts
                if rk == "nl_exit":
                    agg.accepts_nl_exit += 1
                    agg.unique_nl_exit.add(ev.email)
                elif rk == "bypass":
                    agg.accepts_ru_bypass += 1
                    agg.unique_ru_direct.add(ev.email)
            elif host == "nl":
                if rk == "nl_direct" or "direct" in (ev.route or "").lower():
                    agg.accepts_nl_direct += 1
                    agg.unique_nl_direct.add(ev.email)
                elif "xeno-direct" in (ev.route or "").lower() or host == "nl":
                    # NL non-hop with user email ≈ Direct inbound
                    if not ev.hop:
                        agg.accepts_nl_direct += 1
                        agg.unique_nl_direct.add(ev.email)


def _ingest_errors(lines: Iterable[str], *, now: int, windows: dict[int, WindowAgg]) -> None:
    for line in lines:
        ts = _parse_ts_line(line)
        if ts is None:
            continue
        age = now - ts
        klass = classify_error_line(line)
        for wsec, agg in windows.items():
            if age > wsec:
                continue
            if klass in ("xhttp_eof", "xhttp_version"):
                agg.error_xhttp += 1
            if klass == "reality_handshake":
                agg.error_handshake += 1


def compute_path_stats(
    *,
    ru_lines: list[str] | None = None,
    nl_lines: list[str] | None = None,
    ru_err: list[str] | None = None,
    nl_err: list[str] | None = None,
    ru_ip: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    now = now or int(time.time())
    ru_ip = ru_ip or (os.environ.get("RU_PUBLIC_IP") or os.environ.get("RU_BRIDGE_IP") or "")
    windows = {w: WindowAgg() for w in _WINDOWS}
    _ingest_access(ru_lines or [], host="ru", now=now, windows=windows, ru_ip=ru_ip or None)
    _ingest_access(nl_lines or [], host="nl", now=now, windows=windows, ru_ip=ru_ip or None)
    _ingest_errors(ru_err or [], now=now, windows=windows)
    _ingest_errors(nl_err or [], now=now, windows=windows)

    labels = {300: "5m", 900: "15m", 3600: "60m", 86400: "24h"}
    out: dict[str, Any] = {"ts": now, "windows": {}}
    for w, agg in windows.items():
        out["windows"][labels[w]] = agg.to_public()

    w15 = windows[900].to_public()
    only_direct = max(0, int(w15["unique_nl_direct"]) - int(w15["unique_ru"]))
    out["signals"] = {
        "cascade_ratio_break": (
            int(w15["accepts_nl_exit"]) >= 10 and int(w15["hop_ru_sourced"]) == 0
        ),
        "canary_mask_risk": (
            int(w15["hop_canary"]) > 0 and int(w15["hop_ru_sourced"]) == 0 and int(w15["accepts_nl_exit"]) >= 5
        ),
        "direct_migration_hint": only_direct >= 3 and int(w15["unique_ru"]) <= 1,
        "short_session_spike": (w15.get("short_session_ratio") or 0) >= 0.4 and int(w15["accepts_ru"]) >= 20,
    }
    return out


def compute_from_files(*, ru_ip: str | None = None) -> dict[str, Any]:
    return compute_path_stats(
        ru_lines=_tail_lines(RU_ACCESS_LOG),
        nl_lines=_tail_lines(NL_ACCESS_LOG),
        ru_err=_tail_lines(RU_ERROR_LOG),
        nl_err=_tail_lines(NL_ERROR_LOG),
        ru_ip=ru_ip,
    )


def emit_path_stats(stats: dict[str, Any] | None = None) -> dict[str, Any]:
    stats = stats or compute_from_files()
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    w = (stats.get("windows") or {}).get("15m") or {}
    sig = stats.get("signals") or {}
    emit_ops(
        KIND_PATH_STATS,
        unique_ru=w.get("unique_ru"),
        unique_nl_exit=w.get("unique_nl_exit"),
        unique_nl_direct=w.get("unique_nl_direct"),
        accepts_nl_exit=w.get("accepts_nl_exit"),
        hop_canary=w.get("hop_canary"),
        hop_ru_sourced=w.get("hop_ru_sourced"),
        short_session_ratio=w.get("short_session_ratio"),
        nl_exit_per_hop_ru=w.get("nl_exit_per_hop_ru"),
        cascade_ratio_break=bool(sig.get("cascade_ratio_break")),
        canary_mask_risk=bool(sig.get("canary_mask_risk")),
        direct_migration_hint=bool(sig.get("direct_migration_hint")),
        short_session_spike=bool(sig.get("short_session_spike")),
    )
    return stats


def read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
