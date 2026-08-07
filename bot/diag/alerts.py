"""Admin Telegram alerts — state transitions, stable fingerprints, recovery.

Collect runs every 5m with --alerts. Fingerprints MUST be stable for ongoing
conditions (never embed rising counters / new smoke row ids), otherwise cooldown
is bypassed and admins get spammed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config import Settings
from db import Database
from diag import HOP_EMAIL
from diag.classify import REALITY_HANDSHAKE, SNI_MISMATCH
from ops_events import (
    KIND_ALERT_OPEN,
    KIND_ALERT_RECOVER,
    KIND_ALERT_REMIND,
    emit as emit_ops,
)


# First notify immediately on open; remind while still bad; recover once.
REMIND_SEC = 6 * 3600
SNI_SPIKE_THRESHOLD = 50
REALITY_HANDSHAKE_SPIKE_THRESHOLD = 100
DISK_PCT_THRESHOLD = 90
HOP_STALE_SEC = 6 * 3600
# Entry accepts but hop silent — classic hung-steal / dead :8443 cascade break.
# Quiet is measured on RU-sourced hop accepts only (local canary ignored).
CASCADE_SPLIT_HOP_SILENT_SEC = 45 * 60
CASCADE_SPLIT_MIN_RU_ACCEPTS = 15

# Keys we manage recovery for
TRACKED_KEYS = (
    "hop_reality",
    "hop_path_ru",
    "hop_stale",
    "cascade_split",
    "cascade_ratio_break",
    "short_session_spike",
    "direct_migration",
    "unit_xeno_relay",
    "unit_xeno_steal",
    "steal_https",
    "unit_bot",
    "unit_sub",
    "disk",
    "sni_spike",
    "reality_handshake_spike",
    "sub_404_spike",
    "smoke_fail",
)


@dataclass
class Alert:
    key: str
    fingerprint: str  # stable for the open incident
    text: str


def _unit_active(unit: str) -> bool:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r.returncode == 0 and (r.stdout or "").strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _steal_https_ok(listen: str = "127.0.0.1:9443", timeout: float = 5.0) -> bool:
    try:
        r = subprocess.run(
            [
                "curl",
                "-sk",
                "--max-time",
                str(int(timeout)),
                f"https://{listen}/",
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        return r.returncode == 0 and bool((r.stdout or "").strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def _disk_pct(path: str = "/") -> float:
    try:
        u = shutil.disk_usage(path)
        return 100.0 * (u.used / u.total) if u.total else 0.0
    except OSError:
        return 0.0


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fmt(ts: int | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _hop_last_seen(db: Database, *, lookback_days: int = 3) -> int | None:
    """Latest hop accept across recent UTC days (includes local canary)."""
    now = datetime.now(timezone.utc)
    best: int | None = None
    for i in range(lookback_days):
        day = (now.timestamp() - i * 86400)
        day_s = datetime.fromtimestamp(day, tz=timezone.utc).strftime("%Y-%m-%d")
        hops = db.diag_get_hop_days(day_s, day_s)
        if not hops:
            continue
        ls = hops[0].get("last_seen")
        if ls is None:
            continue
        ls_i = int(ls)
        if best is None or ls_i > best:
            best = ls_i
    return best


def _hop_ru_sourced_last_seen(db: Database, *, lookback_days: int = 3) -> int | None:
    """Latest hop accept from entry IP — ignores local canary masking."""
    now = datetime.now(timezone.utc)
    best: int | None = None
    for i in range(lookback_days):
        day = (now.timestamp() - i * 86400)
        day_s = datetime.fromtimestamp(day, tz=timezone.utc).strftime("%Y-%m-%d")
        hops = db.diag_get_hop_days(day_s, day_s)
        if not hops:
            continue
        ls = hops[0].get("last_seen_ru_sourced")
        if ls is None:
            continue
        ls_i = int(ls)
        if best is None or ls_i > best:
            best = ls_i
    return best


def evaluate_alerts(db: Database, settings: Settings) -> list[Alert]:
    now = int(datetime.now(timezone.utc).timestamp())
    day = _today()
    out: list[Alert] = []

    steal_unit = _unit_active("xeno-steal-nl")
    steal_https = _steal_https_ok()
    relay_ok = _unit_active("xeno-relay")

    if not relay_ok:
        out.append(
            Alert(
                key="unit_xeno_relay",
                fingerprint="down",
                text="XENO alert · systemd unit xeno-relay is not active.",
            )
        )
    if not steal_unit:
        out.append(
            Alert(
                key="unit_xeno_steal",
                fingerprint="down",
                text="XENO alert · systemd unit xeno-steal-nl is not active (Reality hop dest).",
            )
        )
    elif not steal_https:
        # Unit may show active while HTTPS is wedged (Recv-Q hung) — this was the cascade outage.
        out.append(
            Alert(
                key="steal_https",
                fingerprint="hung",
                text="XENO alert · SelfSteal :9443 not answering HTTPS. "
                "RU cascade hop broken; NL Direct may still work. "
                "Run: systemctl restart xeno-steal-nl — docs/ops/incident-cascade.md",
            )
        )

    # Cascade health chain (first match wins for overlapping symptoms):
    # 1) units / steal HTTPS  2) live hop Reality canary  3) log-derived split/stale
    hop_reality_down = False
    if steal_unit and steal_https and relay_ok:
        try:
            from diag.hop_probe import canary_alerting, read_state

            st = read_state()
            if canary_alerting(st):
                hop_reality_down = True
                detail = str(st.get("detail") or "fail")
                fails = int(st.get("consecutive_fail") or 0)
                out.append(
                    Alert(
                        key="hop_reality",
                        fingerprint="down",
                        text=(
                            f"XENO alert · hop Reality canary FAIL ×{fails} "
                            f"(:8443 → :19443, detail={detail}). "
                            "RU cascade broken; Direct may work. "
                            "See docs/ops/incident-cascade.md"
                        ),
                    )
                )
        except Exception:
            hop_reality_down = False

        hop_ru_last = _hop_ru_sourced_last_seen(db)
        # Log-derived signals: hop_stale / cascade_split use RU-sourced hop only
        # so local canary cannot mask a dead client cascade.
        if not hop_reality_down:
            if hop_ru_last and (now - hop_ru_last) > HOP_STALE_SEC:
                age_h = (now - hop_ru_last) / 3600
                out.append(
                    Alert(
                        key="hop_stale",
                        fingerprint="stale",
                        text=(
                            f"XENO alert · RU-sourced hop quiet ~{age_h:.0f}h "
                            f"(last {_fmt(hop_ru_last)}). Local canary ignored — check clients / path."
                        ),
                    )
                )

            ru_accepts = 0
            for r in db.diag_list_user_days(day, day):
                if r.get("email") == HOP_EMAIL:
                    continue
                ru_accepts += int(r.get("accepts_ru") or 0)
            hop_silent = (hop_ru_last is None) or (
                (now - int(hop_ru_last)) > CASCADE_SPLIT_HOP_SILENT_SEC
            )
            if ru_accepts >= CASCADE_SPLIT_MIN_RU_ACCEPTS and hop_silent:
                out.append(
                    Alert(
                        key="cascade_split",
                        fingerprint="split",
                        text=(
                            f"XENO alert · cascade split: RU accepts ×{ru_accepts} today but "
                            f"RU-sourced hop quiet (last {_fmt(hop_ru_last)}). "
                            "Local canary does not count — see incident-cascade.md"
                        ),
                    )
                )

        try:
            from diag.ru_hop_probe import ru_hop_alerting, read_state as read_ru_hop

            rst = read_ru_hop()
            if ru_hop_alerting(rst):
                out.append(
                    Alert(
                        key="hop_path_ru",
                        fingerprint="down",
                        text=(
                            f"XENO alert · RU→NL hop path FAIL ×{int(rst.get('consecutive_fail') or 0)} "
                            f"(detail={rst.get('detail')}). Local canary may still be green."
                        ),
                    )
                )
        except Exception:
            pass

        try:
            from diag.path_stats import read_state as read_path

            pst = read_path()
            sig = pst.get("signals") or {}
            if sig.get("cascade_ratio_break"):
                out.append(
                    Alert(
                        key="cascade_ratio_break",
                        fingerprint="ratio",
                        text=(
                            "XENO alert · path_stats: nl-exit accepts without RU-sourced hop (15m). "
                            "Cascade broken for clients; local canary may be green."
                        ),
                    )
                )
            if sig.get("short_session_spike"):
                out.append(
                    Alert(
                        key="short_session_spike",
                        fingerprint="flap",
                        text="XENO alert · path_stats: short RU session spike (15m) — XHTTP/Happ flap?",
                    )
                )
            if sig.get("direct_migration_hint"):
                out.append(
                    Alert(
                        key="direct_migration",
                        fingerprint="migrate",
                        text=(
                            "XENO alert · path_stats: Direct unique ≫ RU unique (15m) — "
                            "clients on backup profile or entry unreachable."
                        ),
                    )
                )
        except Exception:
            pass

    if not _unit_active("xenonet-bot"):
        out.append(
            Alert(
                key="unit_bot",
                fingerprint="down",
                text="XENO alert · xenonet-bot is not active.",
            )
        )
    if not _unit_active("xenonet-sub"):
        out.append(
            Alert(
                key="unit_sub",
                fingerprint="down",
                text="XENO alert · xenonet-sub is not active (Happ subscriptions).",
            )
        )

    disk = _disk_pct("/")
    if disk >= DISK_PCT_THRESHOLD:
        out.append(
            Alert(
                key="disk",
                fingerprint="high",
                text=f"XENO alert · disk {disk:.0f}% used on NL root.",
            )
        )

    rows = db.diag_list_user_days(day, day)
    sni = 0
    hs = 0
    for r in rows:
        if r.get("email") == HOP_EMAIL:
            continue
        errors = json.loads(r.get("error_classes") or "{}")
        sni += int(errors.get(SNI_MISMATCH, 0))
        hs += int(errors.get(REALITY_HANDSHAKE, 0))
    if sni >= SNI_SPIKE_THRESHOLD:
        out.append(
            Alert(
                key="sni_spike",
                fingerprint=f"day:{day}",
                text=f"XENO alert · sni_mismatch ×{sni} today — likely stale Happ profiles.",
            )
        )
    if hs >= REALITY_HANDSHAKE_SPIKE_THRESHOLD:
        out.append(
            Alert(
                key="reality_handshake_spike",
                fingerprint=f"day:{day}",
                text=(
                    f"XENO alert · reality_handshake ×{hs} today — "
                    "check Reality SNI/dest/fp / SelfSteal :9443 (sacred inbounds untouched)."
                ),
            )
        )

    # Sub token scan / 404 spike from events.jsonl (last hour)
    try:
        from ops_events import summarize_last_hours

        win = summarize_last_hours(hours=1)
        n404 = int(win.get("sub_404") or 0)
        if n404 >= 30:
            out.append(
                Alert(
                    key="sub_404_spike",
                    fingerprint="scan",
                    text=(
                        f"XENO alert · sub 404 ×{n404}/1h — possible token scan. "
                        "See events.jsonl kind=sub_404 (IP truncated)."
                    ),
                )
            )
    except Exception:
        pass

    # Smoke: require 2 consecutive failures (avoid single blip after deploy/sync restart).
    recent = db.diag_smoke_recent(limit=2)
    if (
        len(recent) >= 2
        and all(not int(r.get("ok") or 0) for r in recent)
        and now - int(recent[0].get("created_at") or 0) < 2 * 3600
    ):
        summary = recent[0].get("summary") or "FAIL"
        out.append(
            Alert(
                key="smoke_fail",
                fingerprint="down",
                text=f"XENO alert · smoke FAIL ×2: {summary}",
            )
        )

    return out


def send_telegram(settings: Settings, chat_id: int, text: str) -> None:
    token = settings.bot_token
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"alert send fail chat={chat_id}: {exc}")


def _broadcast(settings: Settings, text: str) -> None:
    for admin in sorted(settings.admin_ids):
        send_telegram(settings, admin, text)


def maybe_send_alerts(
    db: Database,
    settings: Settings,
    *,
    remind_sec: int = REMIND_SEC,
) -> list[str]:
    """Notify on open / remind / recover. Stable fingerprints + remind interval.

    Returns list of actions like ``open:smoke_fail``, ``remind:disk``, ``recover:hop_stale``.
    """
    now = int(datetime.now(timezone.utc).timestamp())
    alerts = evaluate_alerts(db, settings)
    active = {a.key: a for a in alerts}
    actions: list[str] = []

    for key in TRACKED_KEYS:
        if key in active:
            continue
        st = db.diag_alert_get(key)
        # Was open (non-empty fingerprint) → recovered
        if st and (st.get("fingerprint") or "").strip():
            _broadcast(settings, f"XENO recover · {key} back to OK.")
            db.diag_alert_mark_ok(key)
            actions.append(f"recover:{key}")
            emit_ops(KIND_ALERT_RECOVER, key=key)

    for key, a in active.items():
        st = db.diag_alert_get(key)
        prev_fp = (st.get("fingerprint") if st else "") or ""
        last_sent = int(st.get("last_sent_at") or 0) if st else 0

        if prev_fp != a.fingerprint:
            # New incident or fingerprint schema change
            _broadcast(settings, a.text)
            db.diag_alert_mark_sent(key, a.fingerprint)
            actions.append(f"open:{key}")
            emit_ops(KIND_ALERT_OPEN, key=key, fingerprint=a.fingerprint)
            continue

        if now - last_sent >= remind_sec:
            _broadcast(settings, f"XENO still · {a.text}")
            db.diag_alert_mark_sent(key, a.fingerprint)
            actions.append(f"remind:{key}")
            emit_ops(KIND_ALERT_REMIND, key=key, fingerprint=a.fingerprint)

    return actions
