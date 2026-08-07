from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class User:
    tg_id: int
    username: str | None
    client_uuid: str
    sub_token: str
    plan: str
    created_at: int
    expires_at: int
    demo_claimed: int
    active: int

    @property
    def is_expired(self) -> bool:
        return int(time.time()) >= self.expires_at

    @property
    def is_active(self) -> bool:
        return bool(self.active) and not self.is_expired


@dataclass
class IssuedLink:
    id: int
    created_by: int
    label: str | None
    client_uuid: str
    sub_token: str
    plan: str
    created_at: int
    expires_at: int
    active: int
    assigned_tg_id: int | None = None
    assigned_username: str | None = None

    @property
    def is_expired(self) -> bool:
        return int(time.time()) >= self.expires_at

    @property
    def is_active(self) -> bool:
        return bool(self.active) and not self.is_expired


# Extra device links beyond the primary users.* credentials (slot 1).
# Slot 2 lives here; primary UUID/token stay on users forever (never rotated for "second link").
MAX_LINKS_PER_USER = 2
SECOND_LINK_SLOT = 2


@dataclass
class UserLink:
    """Secondary (or future) device link; expiry follows parent users.expires_at."""

    id: int
    tg_id: int
    slot: int
    client_uuid: str
    sub_token: str
    created_at: int
    active: int

    @property
    def profile_name(self) -> str:
        return f"XENO #{self.slot}"


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=60)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=60000")
        return con

    def _init(self) -> None:
        with self._conn() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                  tg_id INTEGER PRIMARY KEY,
                  username TEXT,
                  client_uuid TEXT NOT NULL UNIQUE,
                  sub_token TEXT NOT NULL UNIQUE,
                  plan TEXT NOT NULL DEFAULT 'demo',
                  created_at INTEGER NOT NULL,
                  expires_at INTEGER NOT NULL,
                  demo_claimed INTEGER NOT NULL DEFAULT 0,
                  active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS issued_links (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_by INTEGER NOT NULL,
                  label TEXT,
                  client_uuid TEXT NOT NULL UNIQUE,
                  sub_token TEXT NOT NULL UNIQUE,
                  plan TEXT NOT NULL DEFAULT 'issued',
                  created_at INTEGER NOT NULL,
                  expires_at INTEGER NOT NULL,
                  active INTEGER NOT NULL DEFAULT 1,
                  assigned_tg_id INTEGER,
                  assigned_username TEXT
                )
                """
            )
            cols = {r["name"] for r in con.execute("PRAGMA table_info(issued_links)").fetchall()}
            if "assigned_tg_id" not in cols:
                con.execute("ALTER TABLE issued_links ADD COLUMN assigned_tg_id INTEGER")
            if "assigned_username" not in cols:
                con.execute("ALTER TABLE issued_links ADD COLUMN assigned_username TEXT")

            con.execute(
                """
                CREATE TABLE IF NOT EXISTS diag_user_daily (
                  day TEXT NOT NULL,
                  email TEXT NOT NULL,
                  accepts_ru INTEGER NOT NULL DEFAULT 0,
                  accepts_nl_direct INTEGER NOT NULL DEFAULT 0,
                  rejects INTEGER NOT NULL DEFAULT 0,
                  error_classes TEXT NOT NULL DEFAULT '{}',
                  last_seen_ru INTEGER,
                  last_seen_nl_direct INTEGER,
                  src_ip_samples TEXT NOT NULL DEFAULT '[]',
                  bytes_up INTEGER NOT NULL DEFAULT 0,
                  bytes_down INTEGER NOT NULL DEFAULT 0,
                  provisioned_at INTEGER,
                  PRIMARY KEY (day, email)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS diag_hop_daily (
                  day TEXT NOT NULL PRIMARY KEY,
                  accepts INTEGER NOT NULL DEFAULT 0,
                  errors INTEGER NOT NULL DEFAULT 0,
                  last_seen INTEGER
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS diag_ingest_cursor (
                  source TEXT NOT NULL PRIMARY KEY,
                  inode TEXT,
                  offset INTEGER NOT NULL DEFAULT 0,
                  updated_at INTEGER NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS diag_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  kind TEXT NOT NULL,
                  period_key TEXT NOT NULL,
                  path TEXT NOT NULL,
                  status TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  UNIQUE(kind, period_key)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS diag_alert_state (
                  alert_key TEXT NOT NULL PRIMARY KEY,
                  fingerprint TEXT NOT NULL DEFAULT '',
                  last_sent_at INTEGER NOT NULL DEFAULT 0,
                  last_ok_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS diag_smoke_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at INTEGER NOT NULL,
                  ok INTEGER NOT NULL,
                  summary TEXT NOT NULL,
                  detail_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            # Additive only: slot>=2 device links. Primary stays on users.* (untouched).
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS user_links (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  tg_id INTEGER NOT NULL,
                  slot INTEGER NOT NULL,
                  client_uuid TEXT NOT NULL UNIQUE,
                  sub_token TEXT NOT NULL UNIQUE,
                  created_at INTEGER NOT NULL,
                  active INTEGER NOT NULL DEFAULT 1,
                  UNIQUE(tg_id, slot),
                  CHECK(slot >= 2)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS expiry_nags (
                  tg_id INTEGER NOT NULL,
                  nag_kind TEXT NOT NULL,
                  sent_at INTEGER NOT NULL,
                  PRIMARY KEY (tg_id, nag_kind)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS waitlist (
                  tg_id INTEGER NOT NULL PRIMARY KEY,
                  username TEXT,
                  created_at INTEGER NOT NULL,
                  source TEXT NOT NULL DEFAULT 'pricing'
                )
                """
            )
            # Support DM bridge («Диалог»): metadata only — no message bodies.
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS support_dialogs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_tg_id INTEGER NOT NULL,
                  status TEXT NOT NULL DEFAULT 'open',
                  opened_at INTEGER NOT NULL,
                  closed_at INTEGER,
                  last_user_at INTEGER,
                  last_admin_at INTEGER
                )
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_support_dialogs_user
                ON support_dialogs(user_tg_id, status)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS support_bridges (
                  admin_chat_id INTEGER NOT NULL,
                  admin_message_id INTEGER NOT NULL,
                  dialog_id INTEGER NOT NULL,
                  PRIMARY KEY (admin_chat_id, admin_message_id)
                )
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_support_bridges_dialog
                ON support_bridges(dialog_id)
                """
            )
            hop_cols = {r["name"] for r in con.execute("PRAGMA table_info(diag_hop_daily)").fetchall()}
            for col, decl in (
                ("accepts_canary", "INTEGER NOT NULL DEFAULT 0"),
                ("accepts_ru_sourced", "INTEGER NOT NULL DEFAULT 0"),
                ("last_seen_canary", "INTEGER"),
                ("last_seen_ru_sourced", "INTEGER"),
            ):
                if col not in hop_cols:
                    con.execute(f"ALTER TABLE diag_hop_daily ADD COLUMN {col} {decl}")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS banned_users (
                  tg_id INTEGER NOT NULL PRIMARY KEY,
                  username TEXT,
                  reason TEXT NOT NULL DEFAULT '',
                  banned_at INTEGER NOT NULL,
                  banned_by INTEGER
                )
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_banned_users_username
                ON banned_users(username)
                """
            )

    def _user(self, row: sqlite3.Row | None) -> User | None:
        return User(**dict(row)) if row else None

    def _user_link(self, row: sqlite3.Row | None) -> UserLink | None:
        return UserLink(**dict(row)) if row else None

    def _link(self, row: sqlite3.Row | None) -> IssuedLink | None:
        if not row:
            return None
        d = dict(row)
        d.setdefault("assigned_tg_id", None)
        d.setdefault("assigned_username", None)
        return IssuedLink(**d)

    def get(self, tg_id: int) -> User | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        return self._user(row)

    def get_by_username(self, username: str) -> User | None:
        uname = username.lstrip("@").lower()
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM users WHERE lower(username) = ? ORDER BY created_at DESC LIMIT 1",
                (uname,),
            ).fetchone()
        return self._user(row)

    def is_banned(self, tg_id: int | None = None, username: str | None = None) -> bool:
        """Permanent ban: by tg_id and/or username (case-insensitive, no @)."""
        uname = username.lstrip("@").lower() if username else None
        with self._conn() as con:
            if tg_id is not None:
                row = con.execute(
                    "SELECT 1 FROM banned_users WHERE tg_id = ? LIMIT 1", (int(tg_id),)
                ).fetchone()
                if row:
                    return True
            if uname:
                row = con.execute(
                    "SELECT 1 FROM banned_users WHERE lower(username) = ? LIMIT 1",
                    (uname,),
                ).fetchone()
                if row:
                    return True
        return False

    def ban_user(
        self,
        *,
        tg_id: int,
        username: str | None = None,
        reason: str = "",
        banned_by: int | None = None,
    ) -> None:
        """Record permanent ban and soft-disable access rows (caller must sync Xray)."""
        now = int(time.time())
        uname = username.lstrip("@").lower() if username else None
        existing = self.get(tg_id)
        if existing and existing.username and not uname:
            uname = existing.username.lstrip("@").lower()
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO banned_users (tg_id, username, reason, banned_at, banned_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET
                  username = COALESCE(excluded.username, banned_users.username),
                  reason = excluded.reason,
                  banned_at = excluded.banned_at,
                  banned_by = excluded.banned_by
                """,
                (int(tg_id), uname, reason or "", now, banned_by),
            )
            con.execute(
                "UPDATE users SET active = 0, expires_at = ? WHERE tg_id = ?",
                (now, int(tg_id)),
            )
            con.execute(
                "UPDATE user_links SET active = 0 WHERE tg_id = ?",
                (int(tg_id),),
            )
            con.execute(
                "UPDATE issued_links SET active = 0 WHERE assigned_tg_id = ?",
                (int(tg_id),),
            )

    def count_active_users(self) -> int:
        now = int(time.time())
        with self._conn() as con:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM users WHERE active = 1 AND expires_at > ?",
                (now,),
            ).fetchone()
        return int(row["c"])

    def count_active_issued(self) -> int:
        now = int(time.time())
        with self._conn() as con:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM issued_links WHERE active = 1 AND expires_at > ?",
                (now,),
            ).fetchone()
        return int(row["c"])

    def list_active_client_uuids(self) -> list[str]:
        now = int(time.time())
        with self._conn() as con:
            users = con.execute(
                "SELECT client_uuid FROM users WHERE active = 1 AND expires_at > ?",
                (now,),
            ).fetchall()
            # Extra slots inherit parent users.expires_at / active (shared demo window).
            extras = con.execute(
                """
                SELECT ul.client_uuid
                FROM user_links ul
                JOIN users u ON u.tg_id = ul.tg_id
                WHERE ul.active = 1 AND u.active = 1 AND u.expires_at > ?
                """,
                (now,),
            ).fetchall()
            issued = con.execute(
                "SELECT client_uuid FROM issued_links WHERE active = 1 AND expires_at > ?",
                (now,),
            ).fetchall()
        out: list[str] = []
        for r in list(users) + list(extras) + list(issued):
            u = r["client_uuid"]
            if u not in out:
                out.append(u)
        return out

    def get_user_link(self, tg_id: int, slot: int = SECOND_LINK_SLOT) -> UserLink | None:
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM user_links WHERE tg_id = ? AND slot = ? AND active = 1",
                (tg_id, slot),
            ).fetchone()
        return self._user_link(row)

    def list_user_links(self, tg_id: int) -> list[UserLink]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM user_links WHERE tg_id = ? AND active = 1 ORDER BY slot",
                (tg_id,),
            ).fetchall()
        return [self._user_link(r) for r in rows]  # type: ignore

    def list_active_extra_links(self) -> list[tuple[User, UserLink]]:
        """Active secondary links paired with parent user (for sync / expiry)."""
        now = int(time.time())
        with self._conn() as con:
            rows = con.execute(
                """
                SELECT u.*, ul.id AS _ul_id, ul.slot AS _ul_slot,
                       ul.client_uuid AS _ul_uuid, ul.sub_token AS _ul_token,
                       ul.created_at AS _ul_created, ul.active AS _ul_active
                FROM user_links ul
                JOIN users u ON u.tg_id = ul.tg_id
                WHERE ul.active = 1 AND u.active = 1 AND u.expires_at > ?
                ORDER BY ul.tg_id, ul.slot
                """,
                (now,),
            ).fetchall()
        out: list[tuple[User, UserLink]] = []
        for r in rows:
            d = dict(r)
            link = UserLink(
                id=int(d.pop("_ul_id")),
                tg_id=int(d["tg_id"]),
                slot=int(d.pop("_ul_slot")),
                client_uuid=str(d.pop("_ul_uuid")),
                sub_token=str(d.pop("_ul_token")),
                created_at=int(d.pop("_ul_created")),
                active=int(d.pop("_ul_active")),
            )
            out.append((User(**d), link))
        return out

    def count_user_links(self, tg_id: int) -> int:
        """Primary (users row) + active extra slots. 0 if no user / inactive primary ignored for count of issued."""
        user = self.get(tg_id)
        if not user:
            return 0
        n = 1
        with self._conn() as con:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM user_links WHERE tg_id = ? AND active = 1",
                (tg_id,),
            ).fetchone()
        return n + int(row["c"])

    def can_claim_second_link(self, tg_id: int) -> bool:
        user = self.get(tg_id)
        if not user or not user.is_active:
            return False
        return self.count_user_links(tg_id) < MAX_LINKS_PER_USER

    def claim_second_link(self, tg_id: int) -> UserLink:
        """Mint an independent UUID/token for slot 2. Does not touch users.* or expires_at.

        If slot 2 was revoked earlier (active=0), revive the row with fresh credentials
        so UNIQUE(tg_id, slot) does not block a new device.
        """
        if self.is_banned(tg_id):
            raise PermissionError("banned")
        user = self.get(tg_id)
        if not user or not user.is_active:
            raise PermissionError("no active access")
        if self.get_user_link(tg_id, SECOND_LINK_SLOT):
            raise PermissionError("second link already issued")
        if self.count_user_links(tg_id) >= MAX_LINKS_PER_USER:
            raise PermissionError("link limit reached")
        now = int(time.time())
        client_uuid = str(uuid.uuid4())
        sub_token = uuid.uuid4().hex + uuid.uuid4().hex[:8]
        with self._conn() as con:
            inactive = con.execute(
                "SELECT id FROM user_links WHERE tg_id = ? AND slot = ? AND active = 0",
                (tg_id, SECOND_LINK_SLOT),
            ).fetchone()
            if inactive:
                con.execute(
                    """
                    UPDATE user_links
                    SET client_uuid = ?, sub_token = ?, created_at = ?, active = 1
                    WHERE id = ?
                    """,
                    (client_uuid, sub_token, now, int(inactive["id"])),
                )
            else:
                con.execute(
                    """
                    INSERT INTO user_links (tg_id, slot, client_uuid, sub_token, created_at, active)
                    VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    (tg_id, SECOND_LINK_SLOT, client_uuid, sub_token, now),
                )
        link = self.get_user_link(tg_id, SECOND_LINK_SLOT)
        assert link is not None
        return link

    def deactivate_user_link(self, link_id: int) -> None:
        with self._conn() as con:
            con.execute("UPDATE user_links SET active = 0 WHERE id = ?", (link_id,))

    def revoke_second_link(self, tg_id: int) -> UserLink:
        """Soft-disable slot 2 only. Never touches users.* (primary UUID/token)."""
        link = self.get_user_link(tg_id, SECOND_LINK_SLOT)
        if not link:
            raise PermissionError("no second link")
        primary = self.get(tg_id)
        if primary and (
            link.client_uuid == primary.client_uuid or link.sub_token == primary.sub_token
        ):
            raise RuntimeError("refusing to revoke: slot 2 credentials match primary")
        self.deactivate_user_link(link.id)
        return link

    def reactivate_user_link(
        self,
        link_id: int,
        *,
        client_uuid: str,
        sub_token: str,
    ) -> None:
        """Rollback helper after a failed revoke sync — restore the same credentials."""
        with self._conn() as con:
            con.execute(
                """
                UPDATE user_links
                SET active = 1, client_uuid = ?, sub_token = ?
                WHERE id = ?
                """,
                (client_uuid, sub_token, link_id),
            )

    def expiry_nag_sent(self, tg_id: int, nag_kind: str) -> bool:
        with self._conn() as con:
            row = con.execute(
                "SELECT 1 AS o FROM expiry_nags WHERE tg_id = ? AND nag_kind = ?",
                (tg_id, nag_kind),
            ).fetchone()
        return row is not None

    def mark_expiry_nag(self, tg_id: int, nag_kind: str) -> None:
        now = int(time.time())
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO expiry_nags (tg_id, nag_kind, sent_at)
                VALUES (?, ?, ?)
                ON CONFLICT(tg_id, nag_kind) DO UPDATE SET sent_at = excluded.sent_at
                """,
                (tg_id, nag_kind, now),
            )

    def list_users_for_expiry_nag(self, *, nag_kind: str) -> list[User]:
        """Active users in soft windows: d3 = (1d, 3d], d1 = (0, 1d]. Once per kind."""
        now = int(time.time())
        if nag_kind == "d3":
            lo, hi = now + 86400, now + 3 * 86400
        elif nag_kind == "d1":
            lo, hi = now, now + 86400
        else:
            return []
        with self._conn() as con:
            rows = con.execute(
                """
                SELECT u.* FROM users u
                WHERE u.active = 1
                  AND u.expires_at > ?
                  AND u.expires_at <= ?
                  AND NOT EXISTS (
                    SELECT 1 FROM expiry_nags n
                    WHERE n.tg_id = u.tg_id AND n.nag_kind = ?
                  )
                ORDER BY u.expires_at
                """,
                (lo, hi, nag_kind),
            ).fetchall()
        return [self._user(r) for r in rows]  # type: ignore

    def is_on_waitlist(self, tg_id: int) -> bool:
        with self._conn() as con:
            row = con.execute(
                "SELECT 1 AS o FROM waitlist WHERE tg_id = ?", (tg_id,)
            ).fetchone()
        return row is not None

    def join_waitlist(
        self, tg_id: int, username: str | None, *, source: str = "pricing"
    ) -> bool:
        """Return True if newly added, False if already on the list."""
        if self.is_on_waitlist(tg_id):
            return False
        now = int(time.time())
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO waitlist (tg_id, username, created_at, source)
                VALUES (?, ?, ?, ?)
                """,
                (tg_id, username, now, source),
            )
        return True

    def count_waitlist(self) -> int:
        with self._conn() as con:
            row = con.execute("SELECT COUNT(*) AS c FROM waitlist").fetchone()
        return int(row["c"])

    def list_active_users(self) -> list[User]:
        now = int(time.time())
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM users WHERE active = 1 AND expires_at > ?",
                (now,),
            ).fetchall()
        return [self._user(r) for r in rows]  # type: ignore

    def list_issued(self, *, limit: int = 50) -> list[IssuedLink]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM issued_links ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._link(r) for r in rows]  # type: ignore

    def list_active_issued(self) -> list[IssuedLink]:
        now = int(time.time())
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM issued_links WHERE active = 1 AND expires_at > ?",
                (now,),
            ).fetchall()
        return [self._link(r) for r in rows]  # type: ignore

    def get_issued(self, link_id: int) -> IssuedLink | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM issued_links WHERE id = ?", (link_id,)).fetchone()
        return self._link(row)

    def deactivate_issued(self, link_id: int) -> None:
        with self._conn() as con:
            con.execute("UPDATE issued_links SET active = 0 WHERE id = ?", (link_id,))

    def claim_demo(self, tg_id: int, username: str | None, days: int) -> tuple[User, bool]:
        if self.is_banned(tg_id, username):
            raise PermissionError("banned")
        existing = self.get(tg_id)
        if existing and existing.demo_claimed:
            raise PermissionError("demo already claimed")
        now = int(time.time())
        expires = now + days * 86400
        uname = username.lstrip("@") if username else None
        if existing:
            with self._conn() as con:
                con.execute(
                    """
                    UPDATE users
                    SET username = ?, plan = 'demo', expires_at = ?, demo_claimed = 1, active = 1
                    WHERE tg_id = ?
                    """,
                    (uname, expires, tg_id),
                )
            return self.get(tg_id), False  # type: ignore

        client_uuid = str(uuid.uuid4())
        sub_token = uuid.uuid4().hex + uuid.uuid4().hex[:8]
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO users (
                  tg_id, username, client_uuid, sub_token, plan,
                  created_at, expires_at, demo_claimed, active
                ) VALUES (?, ?, ?, ?, 'demo', ?, ?, 1, 1)
                """,
                (tg_id, uname, client_uuid, sub_token, now, expires),
            )
        return self.get(tg_id), True  # type: ignore

    def grant_access(
        self,
        *,
        tg_id: int,
        username: str | None,
        days: int,
        plan: str,
    ) -> User:
        """Admin/godmode grant or extend access for a concrete Telegram user."""
        if self.is_banned(tg_id, username):
            raise PermissionError("banned")
        now = int(time.time())
        expires = now + days * 86400
        uname = username.lstrip("@") if username else None
        existing = self.get(tg_id)
        if existing:
            with self._conn() as con:
                con.execute(
                    """
                    UPDATE users
                    SET username = COALESCE(?, username),
                        plan = ?, expires_at = ?, active = 1
                    WHERE tg_id = ?
                    """,
                    (uname, plan, expires, tg_id),
                )
            return self.get(tg_id)  # type: ignore

        client_uuid = str(uuid.uuid4())
        sub_token = uuid.uuid4().hex + uuid.uuid4().hex[:8]
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO users (
                  tg_id, username, client_uuid, sub_token, plan,
                  created_at, expires_at, demo_claimed, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1)
                """,
                (tg_id, uname, client_uuid, sub_token, plan, now, expires),
            )
        return self.get(tg_id)  # type: ignore

    def create_issued_link(
        self,
        *,
        created_by: int,
        days: int,
        label: str | None = None,
        assigned_tg_id: int | None = None,
        assigned_username: str | None = None,
    ) -> IssuedLink:
        now = int(time.time())
        expires = now + days * 86400
        client_uuid = str(uuid.uuid4())
        sub_token = uuid.uuid4().hex + uuid.uuid4().hex[:8]
        plan = f"issued-{days}"
        uname = assigned_username.lstrip("@") if assigned_username else None
        with self._conn() as con:
            cur = con.execute(
                """
                INSERT INTO issued_links (
                  created_by, label, client_uuid, sub_token, plan,
                  created_at, expires_at, active, assigned_tg_id, assigned_username
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (created_by, label, client_uuid, sub_token, plan, now, expires, assigned_tg_id, uname),
            )
            link_id = int(cur.lastrowid)
        return self.get_issued(link_id)  # type: ignore

    def update_user_creds(self, tg_id: int, *, client_uuid: str, sub_token: str) -> None:
        with self._conn() as con:
            con.execute(
                "UPDATE users SET client_uuid = ?, sub_token = ? WHERE tg_id = ?",
                (client_uuid, sub_token, tg_id),
            )

    def list_active_clients(self) -> list[User]:
        return self.list_active_users()

    # --- diagnostics rollups ---

    def diag_get_cursor(self, source: str) -> tuple[str | None, int]:
        with self._conn() as con:
            row = con.execute(
                "SELECT inode, offset FROM diag_ingest_cursor WHERE source = ?",
                (source,),
            ).fetchone()
        if not row:
            return None, 0
        return row["inode"], int(row["offset"])

    def diag_set_cursor(self, source: str, *, inode: str | None, offset: int) -> None:
        now = int(time.time())
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO diag_ingest_cursor (source, inode, offset, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                  inode = excluded.inode,
                  offset = excluded.offset,
                  updated_at = excluded.updated_at
                """,
                (source, inode, offset, now),
            )

    def diag_ensure_user_day(self, day: str, email: str, *, provisioned_at: int | None = None) -> None:
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO diag_user_daily (day, email, provisioned_at)
                VALUES (?, ?, ?)
                ON CONFLICT(day, email) DO UPDATE SET
                  provisioned_at = COALESCE(diag_user_daily.provisioned_at, excluded.provisioned_at)
                """,
                (day, email, provisioned_at),
            )

    def diag_bump_user(
        self,
        *,
        day: str,
        email: str,
        accepts_ru: int = 0,
        accepts_nl_direct: int = 0,
        rejects: int = 0,
        error_class: str | None = None,
        last_seen_ru: int | None = None,
        last_seen_nl_direct: int | None = None,
        src_ip_masked: str | None = None,
    ) -> None:
        self.diag_ensure_user_day(day, email)
        with self._conn() as con:
            row = con.execute(
                "SELECT error_classes, src_ip_samples FROM diag_user_daily WHERE day = ? AND email = ?",
                (day, email),
            ).fetchone()
            errors: dict[str, int] = json.loads(row["error_classes"] or "{}")
            samples: list[str] = json.loads(row["src_ip_samples"] or "[]")
            if error_class:
                errors[error_class] = int(errors.get(error_class, 0)) + 1
            if src_ip_masked and src_ip_masked not in samples:
                samples = (samples + [src_ip_masked])[-8:]
            sets = [
                "accepts_ru = accepts_ru + ?",
                "accepts_nl_direct = accepts_nl_direct + ?",
                "rejects = rejects + ?",
                "error_classes = ?",
                "src_ip_samples = ?",
            ]
            args: list[Any] = [accepts_ru, accepts_nl_direct, rejects, json.dumps(errors), json.dumps(samples)]
            if last_seen_ru is not None:
                sets.append("last_seen_ru = CASE WHEN last_seen_ru IS NULL OR last_seen_ru < ? THEN ? ELSE last_seen_ru END")
                args.extend([last_seen_ru, last_seen_ru])
            if last_seen_nl_direct is not None:
                sets.append(
                    "last_seen_nl_direct = CASE WHEN last_seen_nl_direct IS NULL OR last_seen_nl_direct < ? "
                    "THEN ? ELSE last_seen_nl_direct END"
                )
                args.extend([last_seen_nl_direct, last_seen_nl_direct])
            args.extend([day, email])
            con.execute(
                f"UPDATE diag_user_daily SET {', '.join(sets)} WHERE day = ? AND email = ?",
                args,
            )

    def diag_bump_hop(
        self,
        *,
        day: str,
        accepts: int = 0,
        errors: int = 0,
        last_seen: int | None = None,
        accepts_canary: int = 0,
        accepts_ru_sourced: int = 0,
        last_seen_canary: int | None = None,
        last_seen_ru_sourced: int | None = None,
    ) -> None:
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO diag_hop_daily (
                  day, accepts, errors, last_seen,
                  accepts_canary, accepts_ru_sourced, last_seen_canary, last_seen_ru_sourced
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(day) DO UPDATE SET
                  accepts = diag_hop_daily.accepts + excluded.accepts,
                  errors = diag_hop_daily.errors + excluded.errors,
                  accepts_canary = diag_hop_daily.accepts_canary + excluded.accepts_canary,
                  accepts_ru_sourced = diag_hop_daily.accepts_ru_sourced + excluded.accepts_ru_sourced,
                  last_seen = CASE
                    WHEN excluded.last_seen IS NOT NULL AND (
                      diag_hop_daily.last_seen IS NULL OR diag_hop_daily.last_seen < excluded.last_seen
                    ) THEN excluded.last_seen
                    ELSE diag_hop_daily.last_seen
                  END,
                  last_seen_canary = CASE
                    WHEN excluded.last_seen_canary IS NOT NULL AND (
                      diag_hop_daily.last_seen_canary IS NULL
                      OR diag_hop_daily.last_seen_canary < excluded.last_seen_canary
                    ) THEN excluded.last_seen_canary
                    ELSE diag_hop_daily.last_seen_canary
                  END,
                  last_seen_ru_sourced = CASE
                    WHEN excluded.last_seen_ru_sourced IS NOT NULL AND (
                      diag_hop_daily.last_seen_ru_sourced IS NULL
                      OR diag_hop_daily.last_seen_ru_sourced < excluded.last_seen_ru_sourced
                    ) THEN excluded.last_seen_ru_sourced
                    ELSE diag_hop_daily.last_seen_ru_sourced
                  END
                """,
                (
                    day,
                    accepts,
                    errors,
                    last_seen,
                    accepts_canary,
                    accepts_ru_sourced,
                    last_seen_canary,
                    last_seen_ru_sourced,
                ),
            )

    def diag_set_user_bytes(self, day: str, email: str, *, bytes_up: int, bytes_down: int) -> None:
        self.diag_ensure_user_day(day, email)
        with self._conn() as con:
            con.execute(
                """
                UPDATE diag_user_daily
                SET bytes_up = ?, bytes_down = ?
                WHERE day = ? AND email = ?
                """,
                (bytes_up, bytes_down, day, email),
            )

    def diag_list_user_days(self, day_from: str, day_to: str) -> list[dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute(
                """
                SELECT * FROM diag_user_daily
                WHERE day >= ? AND day <= ?
                ORDER BY day, email
                """,
                (day_from, day_to),
            ).fetchall()
        return [dict(r) for r in rows]

    def diag_get_hop_days(self, day_from: str, day_to: str) -> list[dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute(
                """
                SELECT * FROM diag_hop_daily
                WHERE day >= ? AND day <= ?
                ORDER BY day
                """,
                (day_from, day_to),
            ).fetchall()
        return [dict(r) for r in rows]

    def diag_record_run(self, *, kind: str, period_key: str, path: str, status: str = "ok") -> None:
        now = int(time.time())
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO diag_runs (kind, period_key, path, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(kind, period_key) DO UPDATE SET
                  path = excluded.path,
                  status = excluded.status,
                  created_at = excluded.created_at
                """,
                (kind, period_key, path, status, now),
            )

    def diag_apply_events(self, events: list[dict]) -> int:
        """Apply many bump ops in one transaction (avoids open-storm on large logs)."""
        if not events:
            return 0
        with self._conn() as con:
            for ev in events:
                kind = ev.get("kind")
                if kind == "hop":
                    con.execute(
                        """
                        INSERT INTO diag_hop_daily (
                          day, accepts, errors, last_seen,
                          accepts_canary, accepts_ru_sourced, last_seen_canary, last_seen_ru_sourced
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(day) DO UPDATE SET
                          accepts = diag_hop_daily.accepts + excluded.accepts,
                          errors = diag_hop_daily.errors + excluded.errors,
                          accepts_canary = diag_hop_daily.accepts_canary + excluded.accepts_canary,
                          accepts_ru_sourced = diag_hop_daily.accepts_ru_sourced + excluded.accepts_ru_sourced,
                          last_seen = CASE
                            WHEN excluded.last_seen IS NOT NULL AND (
                              diag_hop_daily.last_seen IS NULL OR diag_hop_daily.last_seen < excluded.last_seen
                            ) THEN excluded.last_seen
                            ELSE diag_hop_daily.last_seen
                          END,
                          last_seen_canary = CASE
                            WHEN excluded.last_seen_canary IS NOT NULL AND (
                              diag_hop_daily.last_seen_canary IS NULL
                              OR diag_hop_daily.last_seen_canary < excluded.last_seen_canary
                            ) THEN excluded.last_seen_canary
                            ELSE diag_hop_daily.last_seen_canary
                          END,
                          last_seen_ru_sourced = CASE
                            WHEN excluded.last_seen_ru_sourced IS NOT NULL AND (
                              diag_hop_daily.last_seen_ru_sourced IS NULL
                              OR diag_hop_daily.last_seen_ru_sourced < excluded.last_seen_ru_sourced
                            ) THEN excluded.last_seen_ru_sourced
                            ELSE diag_hop_daily.last_seen_ru_sourced
                          END
                        """,
                        (
                            ev["day"],
                            int(ev.get("accepts") or 0),
                            int(ev.get("errors") or 0),
                            ev.get("last_seen"),
                            int(ev.get("accepts_canary") or 0),
                            int(ev.get("accepts_ru_sourced") or 0),
                            ev.get("last_seen_canary"),
                            ev.get("last_seen_ru_sourced"),
                        ),
                    )
                elif kind == "user":
                    day, email = ev["day"], ev["email"]
                    con.execute(
                        """
                        INSERT INTO diag_user_daily (day, email)
                        VALUES (?, ?)
                        ON CONFLICT(day, email) DO NOTHING
                        """,
                        (day, email),
                    )
                    row = con.execute(
                        "SELECT error_classes, src_ip_samples FROM diag_user_daily WHERE day = ? AND email = ?",
                        (day, email),
                    ).fetchone()
                    errors: dict[str, int] = json.loads(row["error_classes"] or "{}")
                    samples: list[str] = json.loads(row["src_ip_samples"] or "[]")
                    if ev.get("error_class"):
                        errors[ev["error_class"]] = int(errors.get(ev["error_class"], 0)) + 1
                    sip = ev.get("src_ip_masked")
                    if sip and sip not in samples:
                        samples = (samples + [sip])[-8:]
                    sets = [
                        "accepts_ru = accepts_ru + ?",
                        "accepts_nl_direct = accepts_nl_direct + ?",
                        "rejects = rejects + ?",
                        "error_classes = ?",
                        "src_ip_samples = ?",
                    ]
                    args: list[Any] = [
                        int(ev.get("accepts_ru") or 0),
                        int(ev.get("accepts_nl_direct") or 0),
                        int(ev.get("rejects") or 0),
                        json.dumps(errors),
                        json.dumps(samples),
                    ]
                    if ev.get("last_seen_ru") is not None:
                        sets.append(
                            "last_seen_ru = CASE WHEN last_seen_ru IS NULL OR last_seen_ru < ? "
                            "THEN ? ELSE last_seen_ru END"
                        )
                        args.extend([ev["last_seen_ru"], ev["last_seen_ru"]])
                    if ev.get("last_seen_nl_direct") is not None:
                        sets.append(
                            "last_seen_nl_direct = CASE WHEN last_seen_nl_direct IS NULL OR last_seen_nl_direct < ? "
                            "THEN ? ELSE last_seen_nl_direct END"
                        )
                        args.extend([ev["last_seen_nl_direct"], ev["last_seen_nl_direct"]])
                    args.extend([day, email])
                    con.execute(
                        f"UPDATE diag_user_daily SET {', '.join(sets)} WHERE day = ? AND email = ?",
                        args,
                    )
        return len(events)

    def diag_latest_run(self, kind: str) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute(
                """
                SELECT * FROM diag_runs WHERE kind = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (kind,),
            ).fetchone()
        return dict(row) if row else None

    def diag_alert_get(self, alert_key: str) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM diag_alert_state WHERE alert_key = ?",
                (alert_key,),
            ).fetchone()
        return dict(row) if row else None

    def diag_alert_mark_sent(self, alert_key: str, fingerprint: str) -> None:
        now = int(time.time())
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO diag_alert_state (alert_key, fingerprint, last_sent_at, last_ok_at)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(alert_key) DO UPDATE SET
                  fingerprint = excluded.fingerprint,
                  last_sent_at = excluded.last_sent_at
                """,
                (alert_key, fingerprint, now),
            )

    def diag_alert_mark_ok(self, alert_key: str) -> None:
        now = int(time.time())
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO diag_alert_state (alert_key, fingerprint, last_sent_at, last_ok_at)
                VALUES (?, '', 0, ?)
                ON CONFLICT(alert_key) DO UPDATE SET
                  fingerprint = '',
                  last_ok_at = excluded.last_ok_at
                """,
                (alert_key, now),
            )

    def diag_smoke_record(self, *, ok: bool, summary: str, detail: dict[str, Any]) -> None:
        now = int(time.time())
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO diag_smoke_runs (created_at, ok, summary, detail_json)
                VALUES (?, ?, ?, ?)
                """,
                (now, 1 if ok else 0, summary, json.dumps(detail, ensure_ascii=False)),
            )

    def diag_smoke_latest(self) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM diag_smoke_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def diag_smoke_recent(self, limit: int = 2) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 20))
        with self._conn() as con:
            rows = con.execute(
                """
                SELECT * FROM diag_smoke_runs
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        return [dict(r) for r in rows]

    def diag_user_day(self, day: str, email: str) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM diag_user_daily WHERE day = ? AND email = ?",
                (day, email),
            ).fetchone()
        return dict(row) if row else None

    # --- support DM bridge («Диалог») ---

    def _support_dialog(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def support_get_open(self, user_tg_id: int) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute(
                """
                SELECT * FROM support_dialogs
                WHERE user_tg_id = ? AND status = 'open'
                ORDER BY id DESC LIMIT 1
                """,
                (user_tg_id,),
            ).fetchone()
        return self._support_dialog(row)

    def support_get(self, dialog_id: int) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM support_dialogs WHERE id = ?",
                (dialog_id,),
            ).fetchone()
        return self._support_dialog(row)

    def support_open(self, user_tg_id: int) -> dict[str, Any]:
        existing = self.support_get_open(user_tg_id)
        if existing:
            return existing
        now = int(time.time())
        with self._conn() as con:
            cur = con.execute(
                """
                INSERT INTO support_dialogs
                  (user_tg_id, status, opened_at, closed_at, last_user_at, last_admin_at)
                VALUES (?, 'open', ?, NULL, ?, NULL)
                """,
                (user_tg_id, now, now),
            )
            dialog_id = int(cur.lastrowid)
        dialog = self.support_get(dialog_id)
        assert dialog is not None
        return dialog

    def support_touch_user(self, dialog_id: int) -> None:
        now = int(time.time())
        with self._conn() as con:
            con.execute(
                """
                UPDATE support_dialogs
                SET last_user_at = ?
                WHERE id = ? AND status = 'open'
                """,
                (now, dialog_id),
            )

    def support_touch_admin(self, dialog_id: int) -> None:
        now = int(time.time())
        with self._conn() as con:
            con.execute(
                """
                UPDATE support_dialogs
                SET last_admin_at = ?
                WHERE id = ? AND status = 'open'
                """,
                (now, dialog_id),
            )

    def support_close(self, dialog_id: int) -> bool:
        """Return True if a row transitioned open → closed."""
        now = int(time.time())
        with self._conn() as con:
            cur = con.execute(
                """
                UPDATE support_dialogs
                SET status = 'closed', closed_at = ?
                WHERE id = ? AND status = 'open'
                """,
                (now, dialog_id),
            )
            return cur.rowcount > 0

    def support_close_for_user(self, user_tg_id: int) -> dict[str, Any] | None:
        dialog = self.support_get_open(user_tg_id)
        if not dialog:
            return None
        self.support_close(int(dialog["id"]))
        return self.support_get(int(dialog["id"]))

    def support_can_reopen(self, user_tg_id: int, *, cooldown_sec: int) -> bool:
        """False if a dialog was closed too recently (spam reopen)."""
        with self._conn() as con:
            row = con.execute(
                """
                SELECT closed_at FROM support_dialogs
                WHERE user_tg_id = ? AND status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC LIMIT 1
                """,
                (user_tg_id,),
            ).fetchone()
        if not row or row["closed_at"] is None:
            return True
        return (int(time.time()) - int(row["closed_at"])) >= cooldown_sec

    def support_close_idle(self, *, idle_sec: int) -> list[dict[str, Any]]:
        """Close open dialogs with no user/admin activity for idle_sec. Returns closed rows."""
        now = int(time.time())
        cutoff = now - idle_sec
        with self._conn() as con:
            rows = con.execute(
                """
                SELECT * FROM support_dialogs
                WHERE status = 'open'
                  AND MAX(
                    COALESCE(last_user_at, 0),
                    COALESCE(last_admin_at, 0),
                    opened_at
                  ) < ?
                """,
                (cutoff,),
            ).fetchall()
            closed: list[dict[str, Any]] = []
            for r in rows:
                con.execute(
                    """
                    UPDATE support_dialogs
                    SET status = 'closed', closed_at = ?
                    WHERE id = ? AND status = 'open'
                    """,
                    (now, int(r["id"])),
                )
                closed.append(dict(r))
                closed[-1]["status"] = "closed"
                closed[-1]["closed_at"] = now
        return closed

    def support_add_bridge(
        self, *, admin_chat_id: int, admin_message_id: int, dialog_id: int
    ) -> None:
        with self._conn() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO support_bridges
                  (admin_chat_id, admin_message_id, dialog_id)
                VALUES (?, ?, ?)
                """,
                (admin_chat_id, admin_message_id, dialog_id),
            )

    def support_lookup_bridge(
        self, *, admin_chat_id: int, admin_message_id: int
    ) -> int | None:
        with self._conn() as con:
            row = con.execute(
                """
                SELECT dialog_id FROM support_bridges
                WHERE admin_chat_id = ? AND admin_message_id = ?
                """,
                (admin_chat_id, admin_message_id),
            ).fetchone()
        return int(row["dialog_id"]) if row else None

    def support_count_open(self) -> int:
        with self._conn() as con:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM support_dialogs WHERE status = 'open'"
            ).fetchone()
        return int(row["c"])
