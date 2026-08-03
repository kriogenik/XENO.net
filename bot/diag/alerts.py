"""Admin Telegram alerts (debounced) from diag rollups + host health."""
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
from diag import DIGEST_ROOT, HOP_EMAIL
from diag.classify import SNI_MISMATCH


@dataclass
class Alert:
    key: str
    fingerprint: str
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


def _disk_pct(path: str = "/") -> float:
    try:
        u = shutil.disk_usage(path)
        return 100.0 * (u.used / u.total) if u.total else 0.0
    except OSError:
        return 0.0


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def evaluate_alerts(db: Database, settings: Settings) -> list[Alert]:
    now = int(datetime.now(timezone.utc).timestamp())
    day = _today()
    out: list[Alert] = []

    # Hop freshness
    hops = db.diag_get_hop_days(day, day)
    hop_last = hops[0].get("last_seen") if hops else None
    if hop_last and (now - int(hop_last)) > 6 * 3600:
        age_h = (now - int(hop_last)) / 3600
        out.append(
            Alert(
                key="hop_stale",
                fingerprint=f"hop:{hop_last}",
                text=f"XENO alert · hop quiet ~{age_h:.0f}h (last_seen {_fmt(hop_last)}). Check xeno-relay :8443.",
            )
        )
    elif not hop_last and hops:
        out.append(
            Alert(
                key="hop_stale",
                fingerprint="hop:none",
                text="XENO alert · no hop last_seen today. Check cascade / xeno-relay.",
            )
        )

    # Units
    if not _unit_active("xeno-relay"):
        out.append(
            Alert(
                key="unit_xeno_relay",
                fingerprint="down",
                text="XENO alert · systemd unit xeno-relay is not active.",
            )
        )
    if not _unit_active("xenonet-bot"):
        out.append(
            Alert(
                key="unit_bot",
                fingerprint="down",
                text="XENO alert · xenonet-bot is not active.",
            )
        )

    # Disk
    disk = _disk_pct("/")
    if disk >= 90:
        out.append(
            Alert(
                key="disk",
                fingerprint=f"disk:{int(disk)}",
                text=f"XENO alert · disk {disk:.0f}% used on NL root.",
            )
        )

    # SNI mismatch spike today
    rows = db.diag_list_user_days(day, day)
    sni = 0
    for r in rows:
        if r.get("email") == HOP_EMAIL:
            continue
        errors = json.loads(r.get("error_classes") or "{}")
        sni += int(errors.get(SNI_MISMATCH, 0))
    if sni >= 20:
        out.append(
            Alert(
                key="sni_spike",
                fingerprint=f"sni:{sni}",
                text=f"XENO alert · sni_mismatch ×{sni} today — likely stale Happ profiles.",
            )
        )

    # Latest smoke failure
    smoke = db.diag_smoke_latest()
    if smoke and not int(smoke.get("ok") or 0):
        # only if recent (< 2h)
        if now - int(smoke.get("created_at") or 0) < 2 * 3600:
            out.append(
                Alert(
                    key="smoke_fail",
                    fingerprint=f"smoke:{smoke.get('id')}",
                    text=f"XENO alert · smoke FAIL: {smoke.get('summary')}",
                )
            )

    return out


def _fmt(ts: int | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


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


def maybe_send_alerts(
    db: Database,
    settings: Settings,
    *,
    cooldown_sec: int = 3600,
) -> list[str]:
    """Send new/changed alerts to admin_ids; clear recovered keys. Returns sent keys."""
    now = int(datetime.now(timezone.utc).timestamp())
    alerts = evaluate_alerts(db, settings)
    active_keys = {a.key for a in alerts}
    sent: list[str] = []

    # Mark recovered
    for key in ("hop_stale", "unit_xeno_relay", "unit_bot", "disk", "sni_spike", "smoke_fail"):
        if key not in active_keys:
            st = db.diag_alert_get(key)
            if st and st.get("fingerprint"):
                db.diag_alert_mark_ok(key)

    for a in alerts:
        st = db.diag_alert_get(a.key)
        if st and st.get("fingerprint") == a.fingerprint:
            if now - int(st.get("last_sent_at") or 0) < cooldown_sec:
                continue
        for admin in sorted(settings.admin_ids):
            send_telegram(settings, admin, a.text)
        db.diag_alert_mark_sent(a.key, a.fingerprint)
        sent.append(a.key)
    return sent
