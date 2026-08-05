"""Structured ops events — JSON lines at /var/log/xeno/events.jsonl.

Stability and security signals for digests/alerts. Never store full sub tokens,
destinations, or plaintext passwords. IP samples are truncated/hashed.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

EVENTS_LOG = Path("/var/log/xeno/events.jsonl")

# Known kinds (documentation / digest filters). Unknown kinds still accepted.
KIND_SMOKE_RESULT = "smoke_result"
KIND_STEAL_WATCH_RESTART = "steal_watch_restart"
KIND_SYNC_ALL_START = "sync_all_start"
KIND_SYNC_ALL_END = "sync_all_end"
KIND_SYNC_ALL_ERROR = "sync_all_error"
KIND_ALERT_OPEN = "alert_open"
KIND_ALERT_REMIND = "alert_remind"
KIND_ALERT_RECOVER = "alert_recover"
KIND_SUB_404 = "sub_404"
KIND_BOT_UNHANDLED = "bot_unhandled_error"
KIND_SUPPORT_FLOOD = "support_flood"
KIND_GODMODE = "godmode_action"
KIND_SACRED_DENIED = "sacred_denied"
KIND_DEPLOY = "deploy"


def _utc_iso(ts: float | None = None) -> str:
    t = ts if ts is not None else time.time()
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def truncate_ip(ip: str | None) -> str | None:
    """Privacy-safe IP sample: IPv4 → a.b.c.x, IPv6 → first 3 hextets."""
    if not ip:
        return None
    ip = ip.strip().split("%", 1)[0]
    if ":" in ip:
        parts = ip.split(":")
        return ":".join(parts[:3] + ["*"])
    octets = ip.split(".")
    if len(octets) == 4:
        return f"{octets[0]}.{octets[1]}.{octets[2]}.x"
    return "*"


def hash_ip(ip: str | None, *, salt: str = "xeno-ops") -> str | None:
    """Short stable hash of IP (not reversible without salt+ip)."""
    if not ip:
        return None
    dig = hashlib.sha256(f"{salt}|{ip.strip()}".encode("utf-8")).hexdigest()
    return dig[:12]


def truncate_token(token: str | None, *, keep: int = 4) -> str | None:
    """Show only a short prefix of a sub path token (never full secret)."""
    if not token:
        return None
    t = token.strip()
    if len(t) <= keep:
        return t[0] + "…" if len(t) > 1 else "*"
    return t[:keep] + "…"


def emit(kind: str, *, log_path: Path | None = None, **fields: Any) -> None:
    """Append one JSON object. Never raises (ops must not break product paths)."""
    dest = log_path or EVENTS_LOG
    try:
        payload: dict[str, Any] = {"ts": _utc_iso(), "kind": kind}
        for k, v in fields.items():
            if v is None:
                continue
            payload[k] = v
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# In-process rate limit for noisy security events (scanners). Process-local only.
_rate_buckets: dict[str, float] = {}


def emit_rate_limited(
    kind: str,
    *,
    key: str,
    min_interval_sec: float = 60.0,
    log_path: Path | None = None,
    **fields: Any,
) -> bool:
    """Emit at most once per ``key`` every ``min_interval_sec``. Returns True if written."""
    now = time.time()
    bucket = f"{kind}|{key}"
    last = _rate_buckets.get(bucket, 0.0)
    if now - last < min_interval_sec:
        return False
    _rate_buckets[bucket] = now
    # Bound memory if under scan
    if len(_rate_buckets) > 4096:
        cutoff = now - max(min_interval_sec, 60.0) * 2
        for k, ts in list(_rate_buckets.items()):
            if ts < cutoff:
                _rate_buckets.pop(k, None)
    emit(kind, log_path=log_path, **fields)
    return True


def parse_ts(iso_or_unix: Any) -> int | None:
    if iso_or_unix is None:
        return None
    if isinstance(iso_or_unix, (int, float)):
        return int(iso_or_unix)
    s = str(iso_or_unix).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return int(datetime.fromisoformat(s).timestamp())
    except ValueError:
        return None


def iter_events(
    *,
    path: Path | None = None,
    since_ts: int | None = None,
    kinds: Iterable[str] | None = None,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield parsed event dicts (newest-last file order). Skips bad lines."""
    log_path = path or EVENTS_LOG
    kind_set = set(kinds) if kinds is not None else None
    n = 0
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if kind_set is not None and obj.get("kind") not in kind_set:
                    continue
                ts = parse_ts(obj.get("ts"))
                if since_ts is not None and (ts is None or ts < since_ts):
                    continue
                yield obj
                n += 1
                if limit is not None and n >= limit:
                    return
    except OSError:
        return


def summarize_last_hours(
    *,
    hours: float = 24.0,
    path: Path | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Aggregate stability/security signals for digest section."""
    now = now if now is not None else int(time.time())
    since = now - int(hours * 3600)
    counts: Counter[str] = Counter()
    smoke_ok = 0
    smoke_fail = 0
    sync_errors: list[str] = []
    steal_reasons: Counter[str] = Counter()
    sub_404_ips: Counter[str] = Counter()
    god_actions: Counter[str] = Counter()
    alert_actions: Counter[str] = Counter()
    bot_errors: Counter[str] = Counter()
    sacred: list[str] = []
    support_flood_users = 0

    for ev in iter_events(path=path, since_ts=since):
        kind = str(ev.get("kind") or "")
        counts[kind] += 1
        if kind == KIND_SMOKE_RESULT:
            if ev.get("ok"):
                smoke_ok += 1
            else:
                smoke_fail += 1
        elif kind == KIND_STEAL_WATCH_RESTART:
            steal_reasons[str(ev.get("reason") or "unknown")] += 1
        elif kind == KIND_SYNC_ALL_ERROR:
            err = str(ev.get("error") or ev.get("detail") or "?")[:120]
            sync_errors.append(err)
        elif kind == KIND_SUB_404:
            sample = ev.get("ip_trunc") or ev.get("ip_hash") or "?"
            sub_404_ips[str(sample)] += 1
        elif kind == KIND_GODMODE:
            god_actions[str(ev.get("action") or "unknown")] += 1
        elif kind in (KIND_ALERT_OPEN, KIND_ALERT_REMIND, KIND_ALERT_RECOVER):
            alert_actions[f"{kind}:{ev.get('key') or '?'}"] += 1
        elif kind == KIND_BOT_UNHANDLED:
            bot_errors[str(ev.get("where") or "unknown")] += 1
        elif kind == KIND_SACRED_DENIED:
            sacred.append(str(ev.get("detail") or "refused")[:80])
        elif kind == KIND_SUPPORT_FLOOD:
            support_flood_users += 1

    top_404 = sub_404_ips.most_common(5)
    return {
        "hours": hours,
        "since_ts": since,
        "counts": dict(counts),
        "smoke_ok": smoke_ok,
        "smoke_fail": smoke_fail,
        "steal_restarts": int(counts.get(KIND_STEAL_WATCH_RESTART, 0)),
        "steal_reasons": dict(steal_reasons),
        "sync_errors": sync_errors[-5:],
        "sync_error_n": len(sync_errors),
        "sub_404": int(counts.get(KIND_SUB_404, 0)),
        "sub_404_top_ips": [{"ip": ip, "n": n} for ip, n in top_404],
        "bot_unhandled": int(counts.get(KIND_BOT_UNHANDLED, 0)),
        "bot_errors_by_where": dict(bot_errors),
        "support_flood": support_flood_users,
        "godmode": dict(god_actions),
        "alerts": dict(alert_actions),
        "sacred_denied": sacred[-5:],
        "sacred_n": len(sacred),
        "deploy_n": int(counts.get(KIND_DEPLOY, 0)),
    }


def render_stability_section(summary: dict[str, Any] | None = None, *, hours: float = 24.0) -> list[str]:
    """Markdown lines for digest «stability/security signals»."""
    s = summary if summary is not None else summarize_last_hours(hours=hours)
    h = s.get("hours", hours)
    lines = [
        f"## Stability / security signals (last {h:g}h)",
        "",
    ]
    counts = s.get("counts") or {}
    if not counts:
        lines += ["- No ops events recorded in window (empty or missing `events.jsonl`).", ""]
        return lines

    lines.append(
        f"- Smoke · OK **{s.get('smoke_ok', 0)}** · FAIL **{s.get('smoke_fail', 0)}**"
    )
    steal_n = int(s.get("steal_restarts") or 0)
    if steal_n:
        reasons = ", ".join(f"{k}×{v}" for k, v in (s.get("steal_reasons") or {}).items()) or "—"
        lines.append(f"- Steal watch restarts · **{steal_n}** ({reasons})")
    else:
        lines.append("- Steal watch restarts · **0**")

    sync_n = int(s.get("sync_error_n") or 0)
    lines.append(f"- sync_all errors · **{sync_n}**")
    for err in s.get("sync_errors") or []:
        lines.append(f"  - `{err}`")

    sub_n = int(s.get("sub_404") or 0)
    lines.append(f"- Sub invalid token (404) · **{sub_n}**")
    for row in s.get("sub_404_top_ips") or []:
        lines.append(f"  - sample `{row.get('ip')}` · ×{row.get('n')}")

    bot_n = int(s.get("bot_unhandled") or 0)
    lines.append(f"- Bot unhandled errors · **{bot_n}**")
    for where, n in (s.get("bot_errors_by_where") or {}).items():
        lines.append(f"  - `{where}` · ×{n}")

    lines.append(f"- Support flood hits · **{s.get('support_flood', 0)}**")
    god = s.get("godmode") or {}
    if god:
        lines.append("- Godmode · " + ", ".join(f"{k}×{v}" for k, v in god.items()))
    else:
        lines.append("- Godmode · **0**")

    sacred_n = int(s.get("sacred_n") or 0)
    lines.append(f"- Panel sacred denials · **{sacred_n}**")
    for d in s.get("sacred_denied") or []:
        lines.append(f"  - `{d}`")

    alerts = s.get("alerts") or {}
    if alerts:
        lines.append("- Alert actions · " + ", ".join(f"`{k}`×{v}" for k, v in alerts.items()))
    deploy_n = int(s.get("deploy_n") or 0)
    if deploy_n:
        lines.append(f"- Deploy events · **{deploy_n}**")

    lines += [
        "",
        "- Source · `/var/log/xeno/events.jsonl`",
        "",
    ]
    return lines
