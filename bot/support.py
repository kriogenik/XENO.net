"""Premium DM support bridge («Диалог») — ids, rate limits, media policy.

Telegram history holds message bodies; SQLite keeps dialog metadata + bridge map only.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque

# Defaults (overridable via Settings / env)
RATE_LIMIT_COUNT = 5
RATE_LIMIT_WINDOW_SEC = 10 * 60
REOPEN_COOLDOWN_SEC = 60 * 60
IDLE_CLOSE_SEC = 72 * 60 * 60

ALLOWED_MEDIA = frozenset({"photo", "document"})
REJECTED_MEDIA = frozenset(
    {
        "sticker",
        "animation",
        "video",
        "video_note",
        "voice",
        "audio",
        "contact",
        "location",
        "venue",
        "poll",
        "dice",
    }
)


@dataclass(frozen=True)
class SupportDialog:
    id: int
    user_tg_id: int
    status: str  # open | closed
    opened_at: int
    closed_at: int | None
    last_user_at: int | None
    last_admin_at: int | None


def public_id(dialog_id: int) -> str:
    """Short human id for cards — not a ticket workflow."""
    return f"D-{dialog_id:04x}"


def parse_public_id(raw: str) -> int | None:
    s = (raw or "").strip()
    if s.lower().startswith("d-"):
        s = s[2:]
    try:
        return int(s, 16)
    except ValueError:
        return None


def classify_message(raw: dict) -> str:
    """Return kind: text|photo|document|rejected|empty."""
    if raw.get("photo"):
        return "photo"
    if raw.get("document"):
        return "document"
    for key in REJECTED_MEDIA:
        if raw.get(key):
            return "rejected"
    text = (raw.get("text") or raw.get("caption") or "").strip()
    if text:
        return "text"
    return "empty"


class RateLimiter:
    """In-memory sliding window per user (same spirit as pending_*)."""

    def __init__(
        self,
        *,
        limit: int = RATE_LIMIT_COUNT,
        window_sec: int = RATE_LIMIT_WINDOW_SEC,
    ) -> None:
        self.limit = limit
        self.window_sec = window_sec
        self._hits: dict[int, Deque[int]] = defaultdict(deque)

    def _prune(self, tg_id: int, now: int) -> None:
        q = self._hits[tg_id]
        cutoff = now - self.window_sec
        while q and q[0] < cutoff:
            q.popleft()

    def allow(self, tg_id: int, *, now: int | None = None) -> bool:
        now = now or int(time.time())
        self._prune(tg_id, now)
        return len(self._hits[tg_id]) < self.limit

    def record(self, tg_id: int, *, now: int | None = None) -> None:
        now = now or int(time.time())
        self._prune(tg_id, now)
        self._hits[tg_id].append(now)

    def remaining(self, tg_id: int, *, now: int | None = None) -> int:
        now = now or int(time.time())
        self._prune(tg_id, now)
        return max(0, self.limit - len(self._hits[tg_id]))
