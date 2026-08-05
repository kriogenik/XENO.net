from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

from config import Settings, load_settings
from db import Database
import keyboards as kb
import messages as msg
from provision import (
    provision_demo,
    provision_issued,
    provision_remove_second_link,
    provision_second_link,
    sub_url_issued,
    sub_url_user,
    sub_url_user_link,
    sync_all,
    vless_for,
    vless_issued,
    vless_user_link,
    write_access_sub,
    xui,
)
from support import RateLimiter, classify_message, public_id as support_public_id
from ops_events import (
    KIND_BOT_UNHANDLED,
    KIND_GODMODE,
    KIND_SUPPORT_FLOOD,
    emit as emit_ops,
)
from xray_sync import client_email

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("xenonet-bot")
API = "https://api.telegram.org"


def _refresh_user_access(db: Database, settings: Settings, user) -> None:
    """Rewrite Happ sub(s) + ensure UUID(s) exist on RU/NL backups after topology changes."""
    write_access_sub(settings, user.sub_token, user.client_uuid, name="XENO")
    extra = db.get_user_link(user.tg_id)
    if extra:
        write_access_sub(
            settings, extra.sub_token, extra.client_uuid, name=extra.profile_name
        )
    sync_all(db, settings)


class TgBot:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.token = settings.bot_token
        self.offset = 0
        # admin_id -> {"days": int}
        self.pending_god: dict[int, dict[str, int]] = {}
        self.pending_diag: set[int] = set()
        self.pending_support: set[int] = set()
        # admin_id -> dialog_id
        self.pending_support_reply: dict[int, int] = {}
        self.support_rate = RateLimiter(
            limit=settings.support_rate_limit,
            window_sec=settings.support_rate_window_sec,
        )
        self._idle_sweep_at = 0

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.settings.admin_ids

    def is_god(self, tg_id: int) -> bool:
        return self.is_admin(tg_id)

    def has_donate(self) -> bool:
        return bool(self.settings.donate_wallets)

    def help_body(self, tg_id: int) -> str:
        return msg.help_text(
            demo_days=self.settings.demo_days,
            admin=self.is_admin(tg_id),
            donate=self.has_donate(),
        )

    def help_markup(self) -> dict:
        return kb.help_actions(donate=self.has_donate())

    def has_access(self, tg_id: int) -> bool:
        user = self.db.get(tg_id)
        return user is not None and user.is_active

    def sub_url_for(self, tg_id: int) -> str | None:
        user = self.db.get(tg_id)
        if user and user.is_active:
            return sub_url_user(self.settings, user)
        return None

    def _access_markup(self, tg_id: int, *, sub: str | None = None) -> dict:
        user = self.db.get(tg_id)
        primary = sub
        if primary is None and user and user.is_active:
            primary = sub_url_user(self.settings, user)
        extra = self.db.get_user_link(tg_id) if user and user.is_active else None
        sub2 = sub_url_user_link(self.settings, extra) if extra else None
        return kb.after_access(
            sub_url=primary,
            sub_url_2=sub2,
            can_add_second=self.db.can_claim_second_link(tg_id),
        )

    def _url(self, method: str) -> str:
        return f"{API}/bot{self.token}/{method}"

    async def api(self, session: aiohttp.ClientSession, method: str, **payload: Any) -> Any:
        async with session.post(self._url(method), json=payload, timeout=aiohttp.ClientTimeout(total=90)) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"TG API {method}: {data}")
            return data["result"]

    async def send(
        self,
        session: aiohttp.ClientSession,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self.api(session, "sendMessage", **payload)

    async def copy_message(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        from_chat_id: int,
        message_id: int,
        caption: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id,
        }
        if caption is not None:
            payload["caption"] = caption
            payload["parse_mode"] = "HTML"
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self.api(session, "copyMessage", **payload)

    async def edit_reply_markup(
        self,
        session: aiohttp.ClientSession,
        chat_id: int,
        message_id: int,
        reply_markup: dict,
    ) -> None:
        try:
            await self.api(
                session,
                "editMessageReplyMarkup",
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
        except RuntimeError as exc:
            if "not modified" not in str(exc).lower():
                raise

    async def edit(
        self,
        session: aiohttp.ClientSession,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            await self.api(session, "editMessageText", **payload)
        except RuntimeError as exc:
            err = str(exc).lower()
            if "not modified" in err and reply_markup is not None:
                await self.edit_reply_markup(
                    session, chat_id, message_id, reply_markup
                )
            elif "not modified" not in err:
                raise

    async def answer_callback(
        self,
        session: aiohttp.ClientSession,
        callback_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
            payload["show_alert"] = show_alert
        await self.api(session, "answerCallbackQuery", **payload)

    async def show_home(
        self,
        session: aiohttp.ClientSession,
        chat_id: int,
        tg_id: int,
        *,
        message_id: int | None = None,
        strip_reply_kb: bool = False,
        force_new: bool = False,
    ) -> None:
        has = self.has_access(tg_id)
        text = msg.home(
            demo_days=self.settings.demo_days,
            has_access=has,
        )
        markup = kb.home(godmode=self.is_god(tg_id), has_access=has)
        if force_new:
            message_id = None
        if message_id is not None:
            try:
                await self.edit(session, chat_id, message_id, text, reply_markup=markup)
                return
            except RuntimeError:
                log.warning(
                    "home edit failed chat=%s mid=%s; sending fresh",
                    chat_id,
                    message_id,
                    exc_info=True,
                )
        if strip_reply_kb or force_new:
            # remove_keyboard messages cannot be edited (TG 400). Strip silently, then home+inline.
            try:
                cleared = await self.send(
                    session,
                    chat_id,
                    "\u200b",
                    reply_markup=kb.remove_reply_keyboard(),
                )
                mid = cleared.get("message_id")
                if mid:
                    await self.api(
                        session,
                        "deleteMessage",
                        chat_id=chat_id,
                        message_id=mid,
                    )
            except Exception:
                log.debug("reply keyboard cleanup skipped", exc_info=True)
        await self.send(session, chat_id, text, reply_markup=markup)

    async def screen(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        text: str,
        markup: dict,
        force_new: bool = False,
    ) -> None:
        """Show a screen. Prefer edit when message_id is set; fall back to send.

        force_new=True always sends a fresh message (needed after keyboard-breaking
        deploys so users do not keep staring at stale inline ReplyMarkup).
        """
        if force_new:
            message_id = None
        if message_id is not None:
            try:
                await self.edit(session, chat_id, message_id, text, reply_markup=markup)
                return
            except RuntimeError:
                log.warning(
                    "screen edit failed chat=%s mid=%s; sending fresh",
                    chat_id,
                    message_id,
                    exc_info=True,
                )
        await self.send(session, chat_id, text, reply_markup=markup)

    async def action_demo(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
        username: str | None,
    ) -> None:
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.connecting(),
            markup=kb.back_home(),
        )
        try:
            user = await asyncio.to_thread(
                provision_demo, self.db, self.settings, tg_id, username
            )
        except PermissionError:
            existing = self.db.get(tg_id)
            if existing and existing.is_active:
                try:
                    # Re-publish current cascade params for already-claimed demo
                    await asyncio.to_thread(
                        _refresh_user_access, self.db, self.settings, existing
                    )
                    existing = self.db.get(tg_id) or existing
                except Exception:
                    log.exception("refresh claimed demo failed tg=%s", tg_id)
                sub = sub_url_user(self.settings, existing)
                await self.screen(
                    session,
                    chat_id=chat_id,
                    message_id=message_id,
                    text=msg.demo_reuse_active(
                        expires_at=existing.expires_at,
                        sub=sub,
                    ),
                    markup=self._access_markup(tg_id, sub=sub),
                )
            else:
                await self.screen(
                    session,
                    chat_id=chat_id,
                    message_id=message_id,
                    text=msg.demo_reuse_spent(),
                    markup=kb.pricing(on_waitlist=self.db.is_on_waitlist(tg_id)),
                )
            return
        except Exception:
            log.exception("demo failed tg=%s", tg_id)
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.demo_fail(),
                markup=kb.home(
                    godmode=self.is_god(tg_id),
                    has_access=self.has_access(tg_id),
                ),
            )
            return

        sub = sub_url_user(self.settings, user)
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.demo_ok(
                expires_at=user.expires_at,
                sub=sub,
                vless=vless_for(self.settings, user),
                demo_days=self.settings.demo_days,
            ),
            markup=self._access_markup(tg_id, sub=sub),
        )

    async def action_access(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
    ) -> None:
        user = self.db.get(tg_id)
        if not user:
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.access_none(),
                markup=kb.no_access(),
                force_new=True,
            )
            return
        if not user.is_active:
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.access_expired(user=user),
                markup=kb.pricing(on_waitlist=self.db.is_on_waitlist(tg_id)),
                force_new=True,
            )
            return
        # Always rewrite primary (and secondary if any) with current Reality params
        try:
            write_access_sub(
                self.settings, user.sub_token, user.client_uuid, name="XENO"
            )
            extra = self.db.get_user_link(tg_id)
            if extra:
                write_access_sub(
                    self.settings,
                    extra.sub_token,
                    extra.client_uuid,
                    name=extra.profile_name,
                )
        except Exception:
            log.exception("rewrite sub failed tg=%s", tg_id)
        sub = sub_url_user(self.settings, user)
        extra = self.db.get_user_link(tg_id)
        sub2 = sub_url_user_link(self.settings, extra) if extra else None
        vless2 = vless_user_link(self.settings, extra) if extra else None
        # Always send a fresh message: old access screens may still show
        # pre-deploy inline buttons (Устройство 1/2) that edit would leave in place
        # if the client is looking at a historical message, or if edit fails.
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.access_active(
                user=user,
                sub=sub,
                vless=vless_for(self.settings, user),
                sub_2=sub2,
                vless_2=vless2,
                can_add_second=self.db.can_claim_second_link(tg_id),
            ),
            markup=self._access_markup(tg_id, sub=sub),
            force_new=True,
        )

    async def action_second(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
    ) -> None:
        user = self.db.get(tg_id)
        if not user or not user.is_active:
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.second_need_first(),
                markup=kb.no_access(),
            )
            return
        if not self.db.can_claim_second_link(tg_id):
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.second_already(),
                markup=self._access_markup(tg_id),
            )
            return
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.connecting_second(),
            markup=kb.back_home(),
        )
        try:
            link = await asyncio.to_thread(
                provision_second_link, self.db, self.settings, tg_id
            )
        except PermissionError as exc:
            err = str(exc)
            if "already" in err or "limit" in err:
                text = msg.second_already()
            else:
                text = msg.second_need_first()
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                markup=self._access_markup(tg_id),
            )
            return
        except Exception:
            log.exception("second link failed tg=%s", tg_id)
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.demo_fail(),
                markup=self._access_markup(tg_id),
            )
            return
        user = self.db.get(tg_id) or user
        sub1 = sub_url_user(self.settings, user)
        sub2 = sub_url_user_link(self.settings, link)
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.second_ok(
                expires_at=user.expires_at,
                sub=sub2,
                vless=vless_user_link(self.settings, link),
                sub_1=sub1,
            ),
            markup=self._access_markup(tg_id, sub=sub1),
            force_new=True,
        )

    async def action_second_delete_ask(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
    ) -> None:
        user = self.db.get(tg_id)
        if not user or not user.is_active:
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.second_need_first(),
                markup=kb.no_access(),
            )
            return
        if not self.db.get_user_link(tg_id):
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.second_delete_none(),
                markup=self._access_markup(tg_id),
            )
            return
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.second_delete_confirm(),
            markup=kb.second_delete_confirm(),
        )

    async def action_second_delete_confirm(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
    ) -> None:
        user = self.db.get(tg_id)
        if not user or not user.is_active:
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.second_need_first(),
                markup=kb.no_access(),
            )
            return
        if not self.db.get_user_link(tg_id):
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.second_delete_none(),
                markup=self._access_markup(tg_id),
            )
            return
        primary_uuid, primary_token = user.client_uuid, user.sub_token
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.second_deleting(),
            markup=kb.back_home(),
        )
        try:
            await asyncio.to_thread(
                provision_remove_second_link, self.db, self.settings, tg_id
            )
        except PermissionError:
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.second_delete_none(),
                markup=self._access_markup(tg_id),
            )
            return
        except Exception:
            log.exception("second link remove failed tg=%s", tg_id)
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.demo_fail(),
                markup=self._access_markup(tg_id),
            )
            return
        user = self.db.get(tg_id) or user
        # Sacred: primary credentials must be unchanged after revoke.
        if user.client_uuid != primary_uuid or user.sub_token != primary_token:
            log.error(
                "primary creds changed after second revoke tg=%s — refusing UI lie",
                tg_id,
            )
        sub = sub_url_user(self.settings, user)
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.access_active(
                user=user,
                sub=sub,
                vless=vless_for(self.settings, user),
                can_add_second=self.db.can_claim_second_link(tg_id),
                notice=msg.second_deleted(),
            ),
            markup=self._access_markup(tg_id, sub=sub),
            force_new=True,
        )

    async def action_god_create(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
        days: int,
    ) -> None:
        if not self.is_god(tg_id):
            emit_ops(
                KIND_GODMODE,
                action="denied",
                admin_tg_id=tg_id,
                detail="god_create",
            )
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.denied(),
                markup=kb.back_home(),
            )
            return
        self.pending_god[tg_id] = {"days": days}
        self.pending_diag.discard(tg_id)
        self.pending_support.discard(tg_id)
        self.pending_support_reply.pop(tg_id, None)
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.god_ask_target(days=days),
            markup=kb.god_home(),
        )

    async def resolve_tg_target(
        self,
        session: aiohttp.ClientSession,
        raw: str,
    ) -> tuple[int | None, str | None, str | None]:
        """Returns (tg_id|None, username|None, error|None). '-' means unbound."""
        text = raw.strip()
        if text in ("-", "—", "нет", "no"):
            return None, None, None
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text), None, None
        uname = text.lstrip("@").strip()
        if not uname or " " in uname:
            return None, None, "Укажите @username, user id или «-»."
        # 1) already in our DB
        known = self.db.get_by_username(uname)
        if known:
            return known.tg_id, uname, None
        # 2) Telegram getChat — works if user interacted with bot / public
        try:
            chat = await self.api(session, "getChat", chat_id=f"@{uname}")
            tid = int(chat["id"])
            return tid, uname, None
        except Exception as exc:
            log.info("getChat @%s failed: %s", uname, exc)
            return None, uname, "resolve_fail"

    async def finish_god_issue(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        admin_id: int,
        days: int,
        assigned_tg_id: int | None,
        assigned_username: str | None,
    ) -> None:
        await self.send(session, chat_id, msg.connecting(), reply_markup=kb.back_home())
        try:
            link = await asyncio.to_thread(
                provision_issued,
                self.db,
                self.settings,
                admin_id=admin_id,
                days=days,
                assigned_tg_id=assigned_tg_id,
                assigned_username=assigned_username,
            )
        except Exception as exc:
            log.exception("god create failed")
            emit_ops(
                KIND_GODMODE,
                action="issue_fail",
                admin_tg_id=admin_id,
                days=days,
                detail=type(exc).__name__,
            )
            await self.send(
                session,
                chat_id,
                msg.god_fail(detail=str(exc)),
                reply_markup=kb.god_home(),
            )
            return
        emit_ops(
            KIND_GODMODE,
            action="issue_ok",
            admin_tg_id=admin_id,
            days=days,
            issued_id=link.id,
            assigned_tg_id=assigned_tg_id,
        )
        await self.send(
            session,
            chat_id,
            msg.god_created(
                link=link,
                sub=sub_url_issued(self.settings, link),
                vless=vless_issued(self.settings, link),
                assigned_tg_id=assigned_tg_id,
                assigned_username=assigned_username,
            ),
            reply_markup=kb.god_after_create(),
        )

    async def handle_god_pending_text(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        tg_id: int,
        text: str,
    ) -> bool:
        pending = self.pending_god.get(tg_id)
        if not pending:
            return False
        days = int(pending["days"])
        self.pending_god.pop(tg_id, None)

        assigned_tg_id, assigned_username, err = await self.resolve_tg_target(session, text)
        if err == "resolve_fail":
            await self.send(
                session,
                chat_id,
                msg.god_resolve_fail(username=assigned_username or text),
                reply_markup=kb.god_home(),
            )
            return True
        if err:
            await self.send(session, chat_id, msg.god_fail(detail=err), reply_markup=kb.god_home())
            return True

        await self.finish_god_issue(
            session,
            chat_id=chat_id,
            admin_id=tg_id,
            days=days,
            assigned_tg_id=assigned_tg_id,
            assigned_username=assigned_username,
        )
        return True

    async def action_diag_ask(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
    ) -> None:
        if not self.is_admin(tg_id):
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.denied(),
                markup=kb.back_home(),
            )
            return
        self.pending_god.pop(tg_id, None)
        self.pending_diag.add(tg_id)
        self.pending_support.discard(tg_id)
        self.pending_support_reply.pop(tg_id, None)
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.diag_ask(),
            markup=kb.diag_actions(),
        )

    async def handle_diag_pending_text(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        tg_id: int,
        text: str,
    ) -> bool:
        if tg_id not in self.pending_diag:
            return False
        self.pending_diag.discard(tg_id)

        target_id, username, err = await self.resolve_tg_target(session, text)
        if err == "resolve_fail":
            await self.send(
                session,
                chat_id,
                msg.god_resolve_fail(username=username or text),
                reply_markup=kb.diag_actions(),
            )
            return True
        if err:
            await self.send(
                session,
                chat_id,
                msg.god_fail(detail=err),
                reply_markup=kb.diag_actions(),
            )
            return True
        if target_id is None:
            await self.send(
                session,
                chat_id,
                msg.god_fail(detail="нужен @username или numeric id"),
                reply_markup=kb.diag_actions(),
            )
            return True

        user = self.db.get(target_id)
        label = f"@{user.username}" if user and user.username else (f"@{username}" if username else str(target_id))
        email = f"tg-{target_id}"
        if user:
            email = client_email(user.client_uuid, tg_id=target_id, slot=1)
        extra = self.db.get_user_link(target_id) if user else None
        emails = [email]
        if extra:
            emails.append(client_email(extra.client_uuid, tg_id=target_id, slot=extra.slot))

        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Prefer primary slot; if empty, show slot 2.
        row = None
        chosen_email = email
        for em in emails:
            row = self.db.diag_user_day(day, em)
            if row:
                chosen_email = em
                break
        email = chosen_email
        smoke = self.db.diag_smoke_latest()
        if smoke:
            smoke_line = (
                f"<b>{'OK' if int(smoke.get('ok') or 0) else 'FAIL'}</b> · "
                f"{msg.esc(str(smoke.get('summary') or ''))}"
            )
        else:
            smoke_line = "—"

        if not row:
            hint = email
            if len(emails) > 1:
                hint = " · ".join(emails)
            await self.send(
                session,
                chat_id,
                msg.diag_none(label=label, email=hint),
                reply_markup=kb.diag_actions(),
            )
            return True

        errors = json.loads(row.get("error_classes") or "{}")
        err_s = ", ".join(f"{k}:{v}" for k, v in sorted(errors.items())) or "—"

        def _ts(v: Any) -> str:
            if not v:
                return "—"
            return datetime.fromtimestamp(int(v), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        up = int(row.get("bytes_up") or 0)
        down = int(row.get("bytes_down") or 0)
        bytes_line = f"↑{up} ↓{down} B"

        await self.send(
            session,
            chat_id,
            msg.diag_report(
                label=label,
                email=email,
                day=day,
                accepts_ru=int(row.get("accepts_ru") or 0),
                accepts_nl=int(row.get("accepts_nl_direct") or 0),
                rejects=int(row.get("rejects") or 0),
                error_classes=err_s,
                last_seen_ru=_ts(row.get("last_seen_ru")),
                last_seen_nl=_ts(row.get("last_seen_nl_direct")),
                bytes_line=bytes_line,
                smoke_line=smoke_line,
            ),
            reply_markup=kb.diag_actions(),
        )
        return True

    def _clear_pending(self, tg_id: int) -> None:
        self.pending_god.pop(tg_id, None)
        self.pending_diag.discard(tg_id)
        self.pending_support.discard(tg_id)
        self.pending_support_reply.pop(tg_id, None)

    def _support_context(self, tg_id: int, username: str | None) -> dict[str, Any]:
        user = self.db.get(tg_id)
        on_wl = self.db.is_on_waitlist(tg_id)
        devices = self.db.count_user_links(tg_id) if user else 0
        if user and user.is_active:
            access_label = "активен"
            expires_label = msg.format_expiry(user.expires_at)
            plan = msg.plan_label(user.plan)
        elif user:
            access_label = "истёк"
            expires_label = msg.format_expiry(user.expires_at)
            plan = msg.plan_label(user.plan)
        else:
            access_label = "нет доступа"
            expires_label = "—"
            plan = "—"
        handle = username or (user.username if user else None)
        return {
            "username": handle,
            "tg_id": tg_id,
            "plan": plan,
            "access_label": access_label,
            "expires_label": expires_label,
            "waitlist": on_wl,
            "devices": devices,
        }

    async def maybe_support_idle_close(self, session: aiohttp.ClientSession) -> None:
        now_i = int(time.time())
        if now_i - self._idle_sweep_at < 300:
            return
        self._idle_sweep_at = now_i
        closed = self.db.support_close_idle(idle_sec=self.settings.support_idle_close_sec)
        for d in closed:
            try:
                await self.send(
                    session,
                    int(d["user_tg_id"]),
                    msg.support_closed_idle(),
                    reply_markup=kb.support_closed_actions(),
                )
            except Exception:
                log.debug("idle-close notify skipped", exc_info=True)

    async def action_support_intro(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
    ) -> None:
        if not self.settings.support_enabled:
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=self.help_body(tg_id),
                markup=self.help_markup(),
            )
            return
        self.pending_support.discard(tg_id)
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.support_intro(),
            markup=kb.support_intro_actions(),
        )

    async def action_support_compose(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
    ) -> None:
        open_d = self.db.support_get_open(tg_id)
        if not open_d and not self.db.support_can_reopen(
            tg_id, cooldown_sec=self.settings.support_reopen_cooldown_sec
        ):
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.support_reopen_limited(),
                markup=kb.support_closed_actions(),
            )
            return
        self.pending_god.pop(tg_id, None)
        self.pending_diag.discard(tg_id)
        self.pending_support_reply.pop(tg_id, None)
        self.pending_support.add(tg_id)
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.support_compose_prompt(),
            markup=kb.support_compose_actions(),
        )

    async def action_support_close_user(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
    ) -> None:
        self.pending_support.discard(tg_id)
        closed = self.db.support_close_for_user(tg_id)
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.support_closed_user(),
            markup=kb.support_closed_actions(),
        )
        if closed:
            pid = support_public_id(int(closed["id"]))
            for admin_id in self.settings.admin_ids:
                try:
                    await self.send(
                        session,
                        admin_id,
                        msg.support_admin_closed(public_id=pid),
                    )
                except Exception:
                    log.debug("admin close notify skipped", exc_info=True)

    async def notify_admins_support(
        self,
        session: aiohttp.ClientSession,
        *,
        dialog_id: int,
        user_tg_id: int,
        username: str | None,
        src_chat_id: int,
        src_message_id: int,
    ) -> None:
        ctx = self._support_context(user_tg_id, username)
        pid = support_public_id(dialog_id)
        card = msg.support_admin_card(public_id=pid, **ctx)
        for admin_id in self.settings.admin_ids:
            try:
                card_msg = await self.send(
                    session,
                    admin_id,
                    card,
                    reply_markup=kb.support_admin_actions(dialog_id),
                )
                self.db.support_add_bridge(
                    admin_chat_id=admin_id,
                    admin_message_id=int(card_msg["message_id"]),
                    dialog_id=dialog_id,
                )
                copied = await self.copy_message(
                    session,
                    chat_id=admin_id,
                    from_chat_id=src_chat_id,
                    message_id=src_message_id,
                )
                mid = copied.get("message_id")
                if mid:
                    self.db.support_add_bridge(
                        admin_chat_id=admin_id,
                        admin_message_id=int(mid),
                        dialog_id=dialog_id,
                    )
            except Exception:
                log.exception("support notify admin %s failed", admin_id)

    async def handle_support_inbound(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        tg_id: int,
        username: str | None,
        raw: dict,
    ) -> bool:
        """Client → admins. True if consumed."""
        if not self.settings.support_enabled:
            return False
        kind = classify_message(raw)
        open_d = self.db.support_get_open(tg_id)
        in_compose = tg_id in self.pending_support
        if not in_compose and not open_d:
            return False
        if kind == "rejected":
            await self.send(
                session,
                chat_id,
                msg.support_media_rejected(),
                reply_markup=kb.support_after_send_actions()
                if open_d
                else kb.support_compose_actions(),
            )
            return True
        if kind == "empty":
            return False
        if not self.support_rate.allow(tg_id):
            emit_ops(
                KIND_SUPPORT_FLOOD,
                tg_id=tg_id,
                limit=self.settings.support_rate_limit,
                window_sec=self.settings.support_rate_window_sec,
            )
            await self.send(
                session,
                chat_id,
                msg.support_rate_limited(),
                reply_markup=kb.support_after_send_actions()
                if open_d
                else kb.support_compose_actions(),
            )
            return True
        if not open_d:
            if not self.db.support_can_reopen(
                tg_id, cooldown_sec=self.settings.support_reopen_cooldown_sec
            ):
                self.pending_support.discard(tg_id)
                await self.send(
                    session,
                    chat_id,
                    msg.support_reopen_limited(),
                    reply_markup=kb.support_closed_actions(),
                )
                return True
            open_d = self.db.support_open(tg_id)
        dialog_id = int(open_d["id"])
        self.db.support_touch_user(dialog_id)
        self.support_rate.record(tg_id)
        self.pending_support.discard(tg_id)
        src_mid = int(raw["message_id"])
        await self.notify_admins_support(
            session,
            dialog_id=dialog_id,
            user_tg_id=tg_id,
            username=username,
            src_chat_id=chat_id,
            src_message_id=src_mid,
        )
        await self.send(
            session,
            chat_id,
            msg.support_accepted(public_id=support_public_id(dialog_id)),
            reply_markup=kb.support_after_send_actions(),
        )
        return True

    async def handle_admin_support_reply(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        tg_id: int,
        raw: dict,
    ) -> bool:
        if not self.is_admin(tg_id):
            return False
        dialog_id: int | None = None
        reply = raw.get("reply_to_message") or {}
        if reply.get("message_id"):
            dialog_id = self.db.support_lookup_bridge(
                admin_chat_id=chat_id,
                admin_message_id=int(reply["message_id"]),
            )
        if dialog_id is None and tg_id in self.pending_support_reply:
            dialog_id = self.pending_support_reply.pop(tg_id)
        if dialog_id is None:
            return False
        return await self._deliver_admin_reply(
            session,
            dialog_id=dialog_id,
            admin_chat_id=chat_id,
            raw=raw,
        )

    async def _deliver_admin_reply(
        self,
        session: aiohttp.ClientSession,
        *,
        dialog_id: int,
        admin_chat_id: int,
        raw: dict,
    ) -> bool:
        dialog = self.db.support_get(dialog_id)
        if not dialog or dialog.get("status") != "open":
            await self.send(
                session,
                admin_chat_id,
                msg.support_admin_closed(public_id=support_public_id(dialog_id)),
            )
            return True
        user_tg_id = int(dialog["user_tg_id"])
        kind = classify_message(raw)
        if kind == "rejected":
            await self.send(session, admin_chat_id, msg.support_media_rejected())
            return True
        if kind == "empty":
            return False
        pid = support_public_id(dialog_id)
        try:
            text = (raw.get("text") or "").strip()
            if kind == "text" and text:
                await self.send(
                    session,
                    user_tg_id,
                    msg.support_deliver_prefix() + msg.esc(text),
                    reply_markup=kb.support_after_send_actions(),
                )
            else:
                await self.send(
                    session,
                    user_tg_id,
                    msg.support_deliver_prefix() + "Вложение от поддержки.",
                    reply_markup=kb.support_after_send_actions(),
                )
                await self.copy_message(
                    session,
                    chat_id=user_tg_id,
                    from_chat_id=admin_chat_id,
                    message_id=int(raw["message_id"]),
                )
        except Exception:
            log.exception("support admin→user failed")
            await self.send(
                session,
                admin_chat_id,
                msg.support_admin_deliver_fail(public_id=pid),
            )
            return True
        self.db.support_touch_admin(dialog_id)
        return True

    async def action_status(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
    ) -> None:
        xray_state = "unknown"
        public_ip = self.settings.nl_exit_ip
        try:
            if self.settings.xui_base_url and self.settings.xui_api_token:
                st = await asyncio.to_thread(xui(self.settings).server_status)
                xray_state = st.xray_state
                public_ip = st.public_ip or public_ip
            else:
                xray_state = "running"
        except Exception as exc:
            log.exception("status failed")
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.status_fail(detail=str(exc)),
                markup=kb.back_home(),
            )
            return
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=msg.status_ok(
                xray_state=xray_state,
                public_ip=public_ip,
                entry_host=self.settings.ru_public_host,
            ),
            markup=kb.status_actions(admin=self.is_admin(tg_id)),
        )

    async def action_metrics(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
    ) -> None:
        if not self.is_admin(tg_id):
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.denied(),
                markup=kb.back_home(),
            )
            return
        try:
            if not (self.settings.xui_base_url and self.settings.xui_api_token):
                raise RuntimeError("XUI metrics not configured")
            api = xui(self.settings)
            st = await asyncio.to_thread(api.server_status)
            text = msg.metrics(
                cpu=st.cpu,
                mem_used=st.mem_used,
                mem_total=st.mem_total,
                disk_used=st.disk_used,
                disk_total=st.disk_total,
                uptime=st.uptime,
                load1=st.loads[0],
                load5=st.loads[1],
                load15=st.loads[2],
                xray_state=st.xray_state,
                xray_version=st.xray_version,
                inbound_up=0,
                inbound_down=0,
                inbound_clients=self.db.count_active_users() + self.db.count_active_issued(),
                active_users=self.db.count_active_users(),
                issued_active=self.db.count_active_issued(),
                tcp=st.tcp_count,
                udp=st.udp_count,
                net_up=st.net_up,
                net_down=st.net_down,
            )
        except Exception as exc:
            log.exception("metrics failed")
            text = msg.status_fail(detail=str(exc))
        await self.screen(
            session,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            markup=kb.back_home(),
        )

    async def route(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: int,
        message_id: int | None,
        tg_id: int,
        username: str | None,
        data: str,
    ) -> None:
        if data == kb.CB_HOME:
            self.pending_support.discard(tg_id)
            self.pending_support_reply.pop(tg_id, None)
            await self.show_home(session, chat_id, tg_id, message_id=message_id)
        elif data == kb.CB_DEMO:
            await self.action_demo(
                session,
                chat_id=chat_id,
                message_id=message_id,
                tg_id=tg_id,
                username=username,
            )
        elif data == kb.CB_ACCESS:
            await self.action_access(
                session, chat_id=chat_id, message_id=message_id, tg_id=tg_id
            )
        elif data == kb.CB_SECOND:
            await self.action_second(
                session, chat_id=chat_id, message_id=message_id, tg_id=tg_id
            )
        elif data == kb.CB_SECOND_DEL:
            await self.action_second_delete_ask(
                session, chat_id=chat_id, message_id=message_id, tg_id=tg_id
            )
        elif data == kb.CB_SECOND_DEL_YES:
            await self.action_second_delete_confirm(
                session, chat_id=chat_id, message_id=message_id, tg_id=tg_id
            )
        elif data == kb.CB_SECOND_DEL_NO:
            await self.action_access(
                session, chat_id=chat_id, message_id=message_id, tg_id=tg_id
            )
        elif data == kb.CB_ONBOARD or data == kb.CB_CONNECT:
            has = self.has_access(tg_id)
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.onboard_pick_platform(),
                markup=kb.platform_pick(has_access=has),
            )
        elif data in kb.PLATFORM_CALLBACKS:
            platform = kb.PLATFORM_CALLBACKS[data]
            has = self.has_access(tg_id)
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.connect_guide_platform(platform, has_access=has),
                markup=kb.platform_guide_actions(
                    platform=platform,
                    has_access=has,
                    sub_url=self.sub_url_for(tg_id) if has else None,
                ),
            )
        elif data == kb.CB_PRICING:
            on_wl = self.db.is_on_waitlist(tg_id)
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.pricing(on_waitlist=on_wl),
                markup=kb.pricing(on_waitlist=on_wl),
            )
        elif data == kb.CB_WAITLIST:
            created = self.db.join_waitlist(tg_id, username, source="pricing")
            on_wl = True
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.waitlist_joined() if created else msg.waitlist_already(),
                markup=kb.pricing(on_waitlist=on_wl),
            )
        elif data == kb.CB_STATUS:
            await self.action_status(
                session, chat_id=chat_id, message_id=message_id, tg_id=tg_id
            )
        elif data == kb.CB_METRICS:
            await self.action_metrics(
                session, chat_id=chat_id, message_id=message_id, tg_id=tg_id
            )
        elif data == kb.CB_HELP:
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=self.help_body(tg_id),
                markup=self.help_markup(),
            )
        elif data == kb.CB_DONATE:
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.donate_text(self.settings.donate_wallets),
                markup=kb.donate_actions(),
            )
        elif data == kb.CB_POLICY:
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.policy_text(),
                markup=kb.policy_actions(),
            )
        elif data == kb.CB_GOD:
            if not self.is_god(tg_id):
                await self.screen(
                    session,
                    chat_id=chat_id,
                    message_id=message_id,
                    text=msg.denied(),
                    markup=kb.back_home(),
                )
                return
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.god_home(
                    active_users=self.db.count_active_users(),
                    issued_active=self.db.count_active_issued(),
                    waitlist=self.db.count_waitlist(),
                ),
                markup=kb.god_home(),
            )
        elif data == kb.CB_GOD_LIST:
            if not self.is_god(tg_id):
                await self.screen(
                    session,
                    chat_id=chat_id,
                    message_id=message_id,
                    text=msg.denied(),
                    markup=kb.back_home(),
                )
                return
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.god_list(self.db.list_issued()),
                markup=kb.god_home(),
            )
        elif data in (kb.CB_GOD_CREATE_30, kb.CB_GOD_CREATE_90, kb.CB_GOD_CREATE_365):
            days = {kb.CB_GOD_CREATE_30: 30, kb.CB_GOD_CREATE_90: 90, kb.CB_GOD_CREATE_365: 365}[data]
            await self.action_god_create(
                session,
                chat_id=chat_id,
                message_id=message_id,
                tg_id=tg_id,
                days=days,
            )
        elif data == kb.CB_DIAG:
            await self.action_diag_ask(
                session,
                chat_id=chat_id,
                message_id=message_id,
                tg_id=tg_id,
            )
        elif data == kb.CB_SUPPORT:
            await self.action_support_intro(
                session,
                chat_id=chat_id,
                message_id=message_id,
                tg_id=tg_id,
            )
        elif data == kb.CB_SUPPORT_COMPOSE or data == kb.CB_SUPPORT_MORE:
            await self.action_support_compose(
                session,
                chat_id=chat_id,
                message_id=message_id,
                tg_id=tg_id,
            )
        elif data == kb.CB_SUPPORT_HELP:
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=self.help_body(tg_id),
                markup=self.help_markup(),
            )
        elif data == kb.CB_SUPPORT_CLOSE:
            await self.action_support_close_user(
                session,
                chat_id=chat_id,
                message_id=message_id,
                tg_id=tg_id,
            )
        elif data.startswith(kb.CB_SUPPORT_REPLY):
            if not self.is_admin(tg_id):
                await self.screen(
                    session,
                    chat_id=chat_id,
                    message_id=message_id,
                    text=msg.denied(),
                    markup=kb.back_home(),
                )
                return
            try:
                dialog_id = int(data[len(kb.CB_SUPPORT_REPLY) :])
            except ValueError:
                return
            self.pending_god.pop(tg_id, None)
            self.pending_diag.discard(tg_id)
            self.pending_support.discard(tg_id)
            self.pending_support_reply[tg_id] = dialog_id
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.support_admin_reply_prompt(
                    public_id=support_public_id(dialog_id)
                ),
                markup=kb.back_home(),
            )
        elif data.startswith(kb.CB_SUPPORT_ADMIN_CLOSE):
            if not self.is_admin(tg_id):
                await self.screen(
                    session,
                    chat_id=chat_id,
                    message_id=message_id,
                    text=msg.denied(),
                    markup=kb.back_home(),
                )
                return
            try:
                dialog_id = int(data[len(kb.CB_SUPPORT_ADMIN_CLOSE) :])
            except ValueError:
                return
            dialog = self.db.support_get(dialog_id)
            closed = self.db.support_close(dialog_id)
            pid = support_public_id(dialog_id)
            await self.screen(
                session,
                chat_id=chat_id,
                message_id=message_id,
                text=msg.support_admin_closed(public_id=pid),
                markup=kb.back_home(),
            )
            if closed and dialog:
                try:
                    await self.send(
                        session,
                        int(dialog["user_tg_id"]),
                        msg.support_closed_user(),
                        reply_markup=kb.support_closed_actions(),
                    )
                except Exception:
                    log.debug("user close notify skipped", exc_info=True)

    async def handle_message(self, session: aiohttp.ClientSession, raw: dict) -> None:
        chat_id = raw["chat"]["id"]
        from_user = raw.get("from") or {}
        tg_id = int(from_user.get("id", chat_id))
        username = from_user.get("username")
        text = (raw.get("text") or "").strip()

        await self.maybe_support_idle_close(session)

        if text.startswith("/start"):
            self._clear_pending(tg_id)
            # Fresh home every /start so deploy-critical keyboards are never
            # trapped inside an unedited historical message.
            await self.show_home(
                session, chat_id, tg_id, strip_reply_kb=True, force_new=True
            )
            return
        if text.startswith("/help"):
            await self.send(
                session,
                chat_id,
                self.help_body(tg_id),
                reply_markup=self.help_markup(),
            )
            return
        if text.startswith("/dialog") or text.startswith("/support"):
            await self.action_support_intro(
                session, chat_id=chat_id, message_id=None, tg_id=tg_id
            )
            return
        if text.startswith("/status"):
            await self.action_status(
                session, chat_id=chat_id, message_id=None, tg_id=tg_id
            )
            return
        if text.startswith("/metrics"):
            await self.action_metrics(
                session, chat_id=chat_id, message_id=None, tg_id=tg_id
            )
            return
        if text.startswith("/god") and self.is_god(tg_id):
            await self.send(
                session,
                chat_id,
                msg.god_home(
                    active_users=self.db.count_active_users(),
                    issued_active=self.db.count_active_issued(),
                    waitlist=self.db.count_waitlist(),
                ),
                reply_markup=kb.god_home(),
            )
            return
        if text.startswith("/cancel"):
            self._clear_pending(tg_id)
            await self.show_home(session, chat_id, tg_id)
            return

        # Admin reply to support bridge (reply-to or pending compose)
        if self.is_admin(tg_id):
            handled = await self.handle_admin_support_reply(
                session, chat_id=chat_id, tg_id=tg_id, raw=raw
            )
            if handled:
                return

        if self.is_god(tg_id) and tg_id in self.pending_diag:
            handled = await self.handle_diag_pending_text(
                session, chat_id=chat_id, tg_id=tg_id, text=text
            )
            if handled:
                return

        if self.is_god(tg_id) and tg_id in self.pending_god:
            handled = await self.handle_god_pending_text(
                session, chat_id=chat_id, tg_id=tg_id, text=text
            )
            if handled:
                return

        # Client support compose / open-dialog continuation (text + media)
        handled = await self.handle_support_inbound(
            session,
            chat_id=chat_id,
            tg_id=tg_id,
            username=username,
            raw=raw,
        )
        if handled:
            return

        # Non-text without support context — ignore quietly (don't dump home)
        if not text and classify_message(raw) != "text":
            return

        await self.show_home(session, chat_id, tg_id)

    async def handle_callback(self, session: aiohttp.ClientSession, cq: dict) -> None:
        data = (cq.get("data") or "").strip()
        callback_id = cq["id"]
        from_user = cq.get("from") or {}
        tg_id = int(from_user.get("id", 0))
        username = from_user.get("username")
        msg_obj = cq.get("message") or {}
        chat = msg_obj.get("chat") or {}
        chat_id = int(chat.get("id", tg_id))
        message_id = msg_obj.get("message_id")

        await self.answer_callback(session, callback_id)
        if not data.startswith("x:"):
            return
        await self.route(
            session,
            chat_id=chat_id,
            message_id=int(message_id) if message_id is not None else None,
            tg_id=tg_id,
            username=username,
            data=data,
        )

    async def run(self) -> None:
        log.info(
            "xenonet-bot cascade entry=%s hop=%s:%s godmode ids=%s",
            self.settings.ru_public_host,
            self.settings.nl_exit_ip,
            self.settings.relay_port,
            sorted(self.settings.admin_ids),
        )
        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            me = await self.api(session, "getMe")
            log.info("bot @%s", me.get("username"))
            while True:
                try:
                    updates = await self.api(
                        session,
                        "getUpdates",
                        offset=self.offset,
                        timeout=50,
                        allowed_updates=["message", "callback_query"],
                    )
                except Exception as exc:
                    log.exception("getUpdates: %s", exc)
                    emit_ops(
                        KIND_BOT_UNHANDLED,
                        where="getUpdates",
                        error=type(exc).__name__,
                    )
                    await asyncio.sleep(3)
                    continue
                for upd in updates:
                    self.offset = upd["update_id"] + 1
                    try:
                        if "callback_query" in upd:
                            await self.handle_callback(session, upd["callback_query"])
                        elif "message" in upd:
                            await self.handle_message(session, upd["message"])
                    except Exception as exc:
                        log.exception("update failed")
                        emit_ops(
                            KIND_BOT_UNHANDLED,
                            where="update",
                            error=type(exc).__name__,
                            update_id=upd.get("update_id"),
                        )


async def amain() -> None:
    settings = load_settings(require_token=True)
    db = Database(settings.db_path)
    await TgBot(settings, db).run()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
