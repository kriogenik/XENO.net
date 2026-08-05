"""Render day/week/month Markdown digests (no destinations, no Telegram)."""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from db import Database
from diag import HOP_EMAIL
from ops_events import render_stability_section
from xray_sync import client_email


def _fmt_bytes(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or unit == "TB":
            return f"{int(x)} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024
    return f"{n} B"


def _ts(v: int | None) -> str:
    if not v:
        return "—"
    return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _period_bounds(kind: str, day: date) -> tuple[str, str, str]:
    if kind == "daily":
        key = day.isoformat()
        return key, key, key
    if kind == "weekly":
        start = day - timedelta(days=day.weekday())  # Monday
        end = start + timedelta(days=6)
        iso = start.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        return key, start.isoformat(), end.isoformat()
    # monthly
    start = day.replace(day=1)
    if start.month == 12:
        end = date(start.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    key = f"{start.year:04d}-{start.month:02d}"
    return key, start.isoformat(), end.isoformat()


def _nick_map(db: Database) -> dict[str, str]:
    out: dict[str, str] = {}
    for u in db.list_active_users():
        email = client_email(u.client_uuid, tg_id=u.tg_id, slot=1)
        out[email] = f"@{u.username}" if u.username else email
    for parent, ulink in db.list_active_extra_links():
        email = client_email(ulink.client_uuid, tg_id=parent.tg_id, slot=ulink.slot)
        nick = f"@{parent.username}" if parent.username else f"tg-{parent.tg_id}"
        out[email] = f"{nick} ·{ulink.slot}"
    for link in db.list_active_issued():
        email = client_email(link.client_uuid, issued_id=link.id)
        if link.assigned_username:
            out[email] = f"@{link.assigned_username}"
        else:
            out[email] = email
    return out


def _aggregate_users(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        email = r["email"]
        a = agg.setdefault(
            email,
            {
                "accepts_ru": 0,
                "accepts_nl_direct": 0,
                "rejects": 0,
                "errors": defaultdict(int),
                "last_seen_ru": None,
                "last_seen_nl_direct": None,
                "bytes_up": 0,
                "bytes_down": 0,
                "src": set(),
            },
        )
        a["accepts_ru"] += int(r.get("accepts_ru") or 0)
        a["accepts_nl_direct"] += int(r.get("accepts_nl_direct") or 0)
        a["rejects"] += int(r.get("rejects") or 0)
        for k, v in json.loads(r.get("error_classes") or "{}").items():
            a["errors"][k] += int(v)
        for ip in json.loads(r.get("src_ip_samples") or "[]"):
            a["src"].add(ip)
        for field in ("last_seen_ru", "last_seen_nl_direct"):
            cur = r.get(field)
            if cur and (a[field] is None or cur > a[field]):
                a[field] = cur
        a["bytes_up"] = max(a["bytes_up"], int(r.get("bytes_up") or 0))
        a["bytes_down"] = max(a["bytes_down"], int(r.get("bytes_down") or 0))
    return agg


def _hints(agg: dict[str, dict[str, Any]], hop_accepts: int, hop_last: int | None) -> list[str]:
    hints: list[str] = []
    sni = sum(int(a["errors"].get("sni_mismatch", 0)) for a in agg.values())
    hs = sum(int(a["errors"].get("reality_handshake", 0)) for a in agg.values())
    if sni:
        hints.append(f"sni_mismatch ×{sni} — likely stale Happ profile; re-import subscription.")
    if hs:
        hints.append(f"reality_handshake ×{hs} — check Reality SNI/dest/fp (sacred inbounds untouched).")
    direct_only = [
        e
        for e, a in agg.items()
        if a["accepts_nl_direct"] > 0 and a["accepts_ru"] == 0 and not e.startswith("xeno-")
    ]
    if direct_only:
        hints.append(
            f"{len(direct_only)} user(s) only on NL Direct — investigate RU/LTE path "
            f"({', '.join(direct_only[:5])}{'…' if len(direct_only) > 5 else ''})."
        )
    ru_ok = sum(1 for a in agg.values() if a["accepts_ru"] > 0)
    if ru_ok and hop_accepts == 0:
        hints.append("RU accepts present but hop quiet — check xeno-relay on NL :8443.")
    elif hop_last:
        age_h = (int(datetime.now(timezone.utc).timestamp()) - hop_last) / 3600
        if age_h > 6:
            hints.append(f"Hop last_seen {age_h:.0f}h ago — verify cascade health.")
    silent = [e for e, a in agg.items() if a["accepts_ru"] == 0 and a["accepts_nl_direct"] == 0]
    if silent:
        hints.append(f"{len(silent)} provisioned email(s) silent in period (no accepts).")
    if not hints:
        hints.append("No strong anomaly patterns in this period.")
    return hints


def render_digest(db: Database, *, kind: str, day: date) -> str:
    period_key, d0, d1 = _period_bounds(kind, day)
    rows = db.diag_list_user_days(d0, d1)
    hops = db.diag_get_hop_days(d0, d1)
    hop_accepts = sum(int(h.get("accepts") or 0) for h in hops)
    hop_errors = sum(int(h.get("errors") or 0) for h in hops)
    hop_last = max((h.get("last_seen") or 0) for h in hops) if hops else None
    if hop_last == 0:
        hop_last = None

    agg = _aggregate_users([r for r in rows if r["email"] != HOP_EMAIL])
    nicks = _nick_map(db)

    err_tot: dict[str, int] = defaultdict(int)
    for a in agg.values():
        for k, v in a["errors"].items():
            err_tot[k] += v

    lines: list[str] = [
        f"# XENO connection digest · {kind} · {period_key}",
        "",
        f"UTC window · `{d0}` → `{d1}`",
        f"Generated · `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`",
        "",
    ]

    smoke = db.diag_smoke_latest()
    if smoke:
        smoke_ts = _ts(int(smoke.get("created_at") or 0) or None)
        ok = "OK" if int(smoke.get("ok") or 0) else "FAIL"
        lines += [
            "## Smoke",
            "",
            f"- Latest · **{ok}** · `{smoke.get('summary')}` · {smoke_ts}",
            "- File · `/var/log/xeno/digests/smoke/latest.md`",
            "",
        ]

    lines += [
        "## Network",
        "",
        f"- Hop (`xeno-relay-hop`) · accepts **{hop_accepts}** · errors **{hop_errors}** · last `{_ts(hop_last)}`",
        f"- Users with RU accepts · **{sum(1 for a in agg.values() if a['accepts_ru'])}**",
        f"- Users with NL Direct accepts · **{sum(1 for a in agg.values() if a['accepts_nl_direct'])}**",
        f"- Tracked emails · **{len(agg)}**",
        "",
        "## Error classes",
        "",
    ]
    if err_tot:
        for k, v in sorted(err_tot.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}` · **{v}**")
    else:
        lines.append("- none recorded")

    lines += ["", "## Hints (manual fixes)", ""]
    for h in _hints(agg, hop_accepts, hop_last):
        lines.append(f"- {h}")

    lines += [""] + render_stability_section(hours=24)

    lines += ["", "## Per user", "", "| User | RU | Direct | Rejects | Errors | Last RU | Last Direct | Traffic |", "|---|---:|---:|---:|---|---|---|---|"]

    def sort_key(item: tuple[str, dict[str, Any]]) -> tuple:
        e, a = item
        return (-(a["accepts_ru"] + a["accepts_nl_direct"]), e)

    for email, a in sorted(agg.items(), key=sort_key):
        nick = nicks.get(email, email)
        errs = ", ".join(f"{k}:{v}" for k, v in sorted(a["errors"].items())) or "—"
        traffic = f"↑{_fmt_bytes(a['bytes_up'])} ↓{_fmt_bytes(a['bytes_down'])}"
        lines.append(
            f"| {nick} (`{email}`) | {a['accepts_ru']} | {a['accepts_nl_direct']} | {a['rejects']} | {errs} | "
            f"{_ts(a['last_seen_ru'])} | {_ts(a['last_seen_nl_direct'])} | {traffic} |"
        )

    if not agg:
        lines.append("| — | 0 | 0 | 0 | — | — | — | — |")

    lines += [
        "",
        "## Notes",
        "",
        "- Destinations / browsing history are **not** stored.",
        "- Cascade hop is aggregate-only (shared relay UUID).",
        "- 3x-ui lastOnline is not authoritative for RU cascade path.",
        "",
    ]
    return "\n".join(lines)


def emit_digest(db: Database, *, kind: str, day: date, root: Path) -> Path:
    period_key, _, _ = _period_bounds(kind, day)
    sub = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}[kind]
    out_dir = root / sub
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{period_key}.md"
    body = render_digest(db, kind=kind, day=day)
    path.write_text(body, encoding="utf-8")
    latest = root / f"latest-{sub}.md"
    shutil.copyfile(path, latest)
    db.diag_record_run(kind=kind, period_key=period_key, path=str(path), status="ok")
    return path


def retention_cleanup(root: Path, *, keep_days: int = 90) -> None:
    if not root.exists():
        return
    cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
    for path in root.rglob("*.md"):
        if path.name.startswith("latest-"):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass
