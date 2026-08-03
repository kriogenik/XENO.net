"""Ingest access/error logs into SQLite rollups; optional digest emit."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import paramiko

# Allow `python -m diag` from bot/ WorkingDirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, load_env_files, load_settings  # noqa: E402
from db import Database  # noqa: E402
from diag import (  # noqa: E402
    DIGEST_ROOT,
    HOP_EMAIL,
    NL_ACCESS_LOG,
    NL_ERROR_LOG,
    RU_ACCESS_LOG,
    RU_ERROR_LOG,
)
from diag.digest import emit_digest, retention_cleanup  # noqa: E402
from diag.parse import ParsedEvent, parse_access_line, parse_error_line  # noqa: E402
from diag.stats import merge_traffic, query_user_traffic_local, query_user_traffic_remote  # noqa: E402
from xray_sync import client_email  # noqa: E402


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _event_from_parsed(ev: ParsedEvent, *, host: str) -> dict | None:
    if ev.hop or (ev.email and ev.email == HOP_EMAIL):
        if ev.action == "accepted":
            return {"kind": "hop", "day": ev.day, "accepts": 1, "errors": 0, "last_seen": ev.ts}
        return {"kind": "hop", "day": ev.day, "accepts": 0, "errors": 1, "last_seen": ev.ts}
    if not ev.email:
        return None
    accepts_ru = 0
    accepts_nl = 0
    rejects = 0
    last_ru = None
    last_nl = None
    if host == "ru":
        if ev.action == "accepted":
            accepts_ru = 1
            last_ru = ev.ts
        else:
            rejects = 1
    elif host == "nl":
        if ev.action == "accepted":
            accepts_nl = 1
            last_nl = ev.ts
        else:
            rejects = 1
    else:
        return None
    return {
        "kind": "user",
        "day": ev.day,
        "email": ev.email,
        "accepts_ru": accepts_ru,
        "accepts_nl_direct": accepts_nl,
        "rejects": rejects,
        "error_class": ev.error_class,
        "last_seen_ru": last_ru,
        "last_seen_nl_direct": last_nl,
        "src_ip_masked": ev.src_ip_masked,
    }


def _apply_event(db: Database, ev: ParsedEvent, *, host: str) -> None:
    row = _event_from_parsed(ev, host=host)
    if not row:
        return
    if row["kind"] == "hop":
        db.diag_bump_hop(
            day=row["day"],
            accepts=int(row.get("accepts") or 0),
            errors=int(row.get("errors") or 0),
            last_seen=row.get("last_seen"),
        )
    else:
        db.diag_bump_user(
            day=row["day"],
            email=row["email"],
            accepts_ru=int(row.get("accepts_ru") or 0),
            accepts_nl_direct=int(row.get("accepts_nl_direct") or 0),
            rejects=int(row.get("rejects") or 0),
            error_class=row.get("error_class"),
            last_seen_ru=row.get("last_seen_ru"),
            last_seen_nl_direct=row.get("last_seen_nl_direct"),
            src_ip_masked=row.get("src_ip_masked"),
        )


def _ingest_text(db: Database, text: str, *, kind: str, host: str) -> int:
    batch: list[dict] = []
    for line in text.splitlines():
        ev = parse_access_line(line) if kind == "access" else parse_error_line(line)
        if not ev:
            continue
        row = _event_from_parsed(ev, host=host)
        if row:
            batch.append(row)
    return db.diag_apply_events(batch)


def _read_local_chunk(path: str, *, offset: int) -> tuple[str, int, str | None]:
    p = Path(path)
    if not p.exists():
        return "", 0, None
    st = p.stat()
    inode = str(getattr(st, "st_ino", int(st.st_mtime)))
    size = st.st_size
    if offset > size:
        offset = 0
    with p.open("rb") as f:
        f.seek(offset)
        data = f.read()
        new_off = f.tell()
    text = data.decode("utf-8", "replace")
    return text, new_off, inode


def ingest_local_file(db: Database, source: str, path: str, *, kind: str, host: str) -> int:
    _inode, offset = db.diag_get_cursor(source)
    text, new_off, inode = _read_local_chunk(path, offset=offset)
    if inode is not None and _inode and _inode != inode and offset > 0:
        # rotated
        text, new_off, inode = _read_local_chunk(path, offset=0)
    n = _ingest_text(db, text, kind=kind, host=host)
    db.diag_set_cursor(source, inode=inode, offset=new_off)
    return n


def ingest_remote_file(
    db: Database,
    ssh: paramiko.SSHClient,
    source: str,
    path: str,
    *,
    kind: str,
    host: str,
) -> int:
    _inode, offset = db.diag_get_cursor(source)
    # stat + read from offset via python on remote for binary-safe seek
    script = (
        "python3 - <<'PY'\n"
        "import os,sys\n"
        f"path={path!r}\n"
        f"offset={int(offset)}\n"
        "if not os.path.exists(path):\n"
        "  print('INODE='); print('OFF=0'); sys.exit(0)\n"
        "st=os.stat(path)\n"
        "inode=str(st.st_ino); size=st.st_size\n"
        "if offset>size: offset=0\n"
        "print('INODE='+inode)\n"
        "with open(path,'rb') as f:\n"
        "  f.seek(offset)\n"
        "  data=f.read()\n"
        "  print('OFF='+str(f.tell()))\n"
        "sys.stdout.buffer.write(b'---DATA---\\n')\n"
        "sys.stdout.buffer.write(data)\n"
        "PY"
    )
    _i, o, e = ssh.exec_command(script, timeout=120)
    raw = o.read()
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"remote ingest {path} failed: {err[:500]}")
    text_blob = raw.decode("utf-8", "replace")
    if "---DATA---\n" not in text_blob:
        return 0
    header, data = text_blob.split("---DATA---\n", 1)
    inode = None
    new_off = offset
    for line in header.splitlines():
        if line.startswith("INODE="):
            inode = line[6:] or None
        if line.startswith("OFF="):
            new_off = int(line[4:] or 0)
    if inode and _inode and inode != _inode and offset > 0:
        # rotated — re-read from 0 next time by resetting once
        db.diag_set_cursor(source, inode=inode, offset=0)
        return ingest_remote_file(db, ssh, source, path, kind=kind, host=host)
    n = _ingest_text(db, data, kind=kind, host=host)
    db.diag_set_cursor(source, inode=inode, offset=new_off)
    return n


def seed_active_users(db: Database) -> int:
    day = _today()
    n = 0
    now = int(datetime.now(timezone.utc).timestamp())
    for u in db.list_active_users():
        email = client_email(u.client_uuid, tg_id=u.tg_id, slot=1)
        db.diag_ensure_user_day(day, email, provisioned_at=u.created_at or now)
        n += 1
    for parent, ulink in db.list_active_extra_links():
        email = client_email(ulink.client_uuid, tg_id=parent.tg_id, slot=ulink.slot)
        db.diag_ensure_user_day(day, email, provisioned_at=ulink.created_at or now)
        n += 1
    for link in db.list_active_issued():
        email = client_email(link.client_uuid, issued_id=link.id)
        db.diag_ensure_user_day(day, email, provisioned_at=link.created_at or now)
        n += 1
    return n


def apply_stats(db: Database, traffic: dict[str, dict[str, int]]) -> int:
    day = _today()
    n = 0
    for email, vals in traffic.items():
        if email == HOP_EMAIL:
            continue
        db.diag_set_user_bytes(day, email, bytes_up=int(vals.get("up", 0)), bytes_down=int(vals.get("down", 0)))
        n += 1
    return n


def open_ru_ssh(settings: Settings) -> paramiko.SSHClient:
    host = os.environ.get("RU_SSH_HOST") or settings.ru_public_ip
    user = os.environ.get("RU_SSH_USER", "root")
    password = os.environ.get("RU_SSH_PASS", "")
    if not password:
        raise RuntimeError("RU_SSH_PASS unset")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=password, timeout=30, allow_agent=False, look_for_keys=False)
    return c


def run_collect(db: Database, settings: Settings) -> dict[str, int]:
    stats = {
        "seed": seed_active_users(db),
        "nl_access": ingest_local_file(db, "nl-access", NL_ACCESS_LOG, kind="access", host="nl"),
        "nl_error": ingest_local_file(db, "nl-error", NL_ERROR_LOG, kind="error", host="nl"),
        "ru_access": 0,
        "ru_error": 0,
        "stats_users": 0,
    }
    traffic_local = query_user_traffic_local()
    ssh = None
    try:
        ssh = open_ru_ssh(settings)
        stats["ru_access"] = ingest_remote_file(
            db, ssh, "ru-access", RU_ACCESS_LOG, kind="access", host="ru"
        )
        stats["ru_error"] = ingest_remote_file(
            db, ssh, "ru-error", RU_ERROR_LOG, kind="error", host="ru"
        )
        traffic_ru = query_user_traffic_remote(ssh)
        traffic = merge_traffic(traffic_local, traffic_ru)
    except Exception as exc:
        print(f"ru ingest/stats warn: {exc}", file=sys.stderr)
        traffic = traffic_local
    finally:
        if ssh:
            ssh.close()
    stats["stats_users"] = apply_stats(db, traffic)
    return stats


def main(argv: list[str] | None = None) -> int:
    load_env_files()
    p = argparse.ArgumentParser(description="XENO connection diag collector")
    p.add_argument("--emit", choices=["day", "week", "month", "all"], default=None)
    p.add_argument("--day", default=None, help="UTC day YYYY-MM-DD for digest")
    p.add_argument("--collect-only", action="store_true")
    p.add_argument("--digest-only", action="store_true")
    p.add_argument("--smoke-only", action="store_true")
    p.add_argument("--alerts", action="store_true", help="evaluate + send admin Telegram alerts")
    args = p.parse_args(argv)

    settings = load_settings(require_token=False)
    db = Database(settings.db_path)

    if args.smoke_only:
        from diag.smoke import run_smoke

        result = run_smoke(db, settings)
        print("smoke", result.get("summary"), result.get("checks"))
        if args.alerts:
            from diag.alerts import maybe_send_alerts

            sent = maybe_send_alerts(db, settings)
            print("alerts", sent)
        return 0

    if not args.digest_only:
        counts = run_collect(db, settings)
        print("collect", counts)
        if args.alerts or args.collect_only:
            from diag.alerts import maybe_send_alerts

            sent = maybe_send_alerts(db, settings)
            if sent:
                print("alerts", sent)

    if args.collect_only:
        return 0

    emit = args.emit
    if emit is None and not args.digest_only:
        emit = "day"
    if emit:
        root = Path(DIGEST_ROOT)
        day = args.day or _today()
        if emit in ("day", "all"):
            path = emit_digest(db, kind="daily", day=date.fromisoformat(day), root=root)
            print("wrote", path)
        if emit in ("week", "all"):
            path = emit_digest(db, kind="weekly", day=date.fromisoformat(day), root=root)
            print("wrote", path)
        if emit in ("month", "all"):
            path = emit_digest(db, kind="monthly", day=date.fromisoformat(day), root=root)
            print("wrote", path)
        retention_cleanup(root, keep_days=90)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
