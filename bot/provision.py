from __future__ import annotations

import shutil
import time
from pathlib import Path

from config import Settings
from db import Database, IssuedLink, User, UserLink
from ops_events import (
    KIND_SYNC_ALL_END,
    KIND_SYNC_ALL_ERROR,
    KIND_SYNC_ALL_START,
    emit as emit_ops,
)
from xray_sync import (
    build_happ_balancer_config,
    build_profile_links,
    client_email,
    sync_hy2_users_local,
    sync_nl_direct_clients_local,
    sync_xray_clients_remote,
    write_user_sub_file,
)
from xui import SACRED_INBOUND_IDS, XuiClient, XuiError

PROVISION_LOG = Path("/var/log/xeno/provision.log")


def _append_provision_log(line: str) -> None:
    try:
        PROVISION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with PROVISION_LOG.open("a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except OSError:
        pass


def xui(settings: Settings) -> XuiClient:
    """3x-ui API — managed XENO inbound only (never sacred/foreign inbounds)."""
    return XuiClient(settings.xui_base_url, settings.xui_api_token)


def _sub_id(token: str) -> str:
    return (token or "")[:16] or "xeno"


def _panel_comment(
    *,
    username: str | None = None,
    tg_id: int | None = None,
    issued_id: int | None = None,
    slot: int = 1,
) -> str:
    """Human-visible 3x-ui comment — match admin template (@nick) when possible."""
    suffix = f" ·{slot}" if int(slot or 1) > 1 else ""
    if username:
        return f"@{username.lstrip('@')}{suffix}"
    if issued_id is not None:
        return f"issued-{issued_id}"
    if tg_id is not None:
        return f"tg-{tg_id}{suffix}"
    return "xeno"


def sync_xui_clients(db: Database, settings: Settings) -> tuple[int, int]:
    """Mirror active bot users into 3x-ui XENO inbound (panel visibility, hot-reload).

    RU cascade still uses standalone xray JSON; this only updates the managed NL inbound.
    Returns (clients_added, clients_updated) this run.
    """
    if not (settings.xui_base_url and settings.xui_api_token and settings.xui_inbound_id):
        _append_provision_log("xui sync skip: XUI_BASE_URL / XUI_API_TOKEN / XUI_INBOUND_ID unset")
        return 0, 0
    if settings.xui_inbound_id in SACRED_INBOUND_IDS:
        raise RuntimeError(f"XUI_INBOUND_ID={settings.xui_inbound_id} is sacred — refused")

    api = xui(settings)
    existing = api.inbound_clients(settings.xui_inbound_id)
    added = 0
    updated = 0
    active_uuids = set(db.list_active_client_uuids())

    def _ensure(
        *,
        email: str,
        client_uuid: str,
        sub_token: str,
        expires_at: int,
        tg_id: int | None,
        comment: str,
    ) -> None:
        nonlocal added, updated
        if email not in existing:
            api.add_client(
                inbound_id=settings.xui_inbound_id,
                email=email,
                client_uuid=client_uuid,
                sub_id=_sub_id(sub_token),
                expiry_ms=int(expires_at) * 1000,
                tg_id=tg_id,
                comment=comment,
            )
            existing[email] = {"email": email, "id": client_uuid, "comment": comment}
            added += 1
            return
        cur = existing[email]
        if cur.get("comment") != comment:
            api.update_client(
                inbound_id=settings.xui_inbound_id,
                email=email,
                client=cur,
                comment=comment,
            )
            cur["comment"] = comment
            updated += 1

    for u in db.list_active_users():
        _ensure(
            email=client_email(u.client_uuid, tg_id=u.tg_id, slot=1),
            client_uuid=u.client_uuid,
            sub_token=u.sub_token,
            expires_at=u.expires_at,
            tg_id=u.tg_id,
            comment=_panel_comment(username=u.username, tg_id=u.tg_id, slot=1),
        )

    for parent, ulink in db.list_active_extra_links():
        _ensure(
            email=client_email(ulink.client_uuid, tg_id=parent.tg_id, slot=ulink.slot),
            client_uuid=ulink.client_uuid,
            sub_token=ulink.sub_token,
            expires_at=parent.expires_at,
            tg_id=parent.tg_id,
            comment=_panel_comment(
                username=parent.username,
                tg_id=parent.tg_id,
                slot=ulink.slot,
            ),
        )

    seen_uuids = set(active_uuids)
    for link in db.list_active_issued():
        if link.client_uuid in seen_uuids:
            continue
        _ensure(
            email=client_email(
                link.client_uuid,
                tg_id=link.assigned_tg_id,
                issued_id=link.id,
            ),
            client_uuid=link.client_uuid,
            sub_token=link.sub_token,
            expires_at=link.expires_at,
            tg_id=link.assigned_tg_id,
            comment=_panel_comment(
                username=link.assigned_username,
                tg_id=link.assigned_tg_id,
                issued_id=link.id,
            ),
        )

    if settings.bootstrap_client_uuid:
        boot_email = client_email(settings.bootstrap_client_uuid)
        if (
            boot_email not in existing
            and settings.bootstrap_client_uuid not in active_uuids
        ):
            try:
                api.add_client(
                    inbound_id=settings.xui_inbound_id,
                    email=boot_email,
                    client_uuid=settings.bootstrap_client_uuid,
                    sub_id="bootstrap",
                    expiry_ms=0,
                    comment="xenonet-bootstrap",
                )
                added += 1
            except XuiError as exc:
                _append_provision_log(f"xui bootstrap warn: {exc}")

    if added or updated:
        _append_provision_log(
            f"xui sync: added {added} updated {updated} client(s) on inbound {settings.xui_inbound_id}"
        )
    return added, updated


def _https_sub_base(settings: Settings) -> str:
    """Public subscription origin — always https (any OS / Happ)."""
    base = (settings.sub_public_base or "").strip().rstrip("/")
    if base:
        if base.startswith("http://"):
            base = "https://" + base[len("http://") :]
        elif not base.startswith("https://"):
            base = "https://" + base.lstrip("/")
        return base.rstrip("/")
    host = (settings.sub_public_ip or "").strip()
    if not host:
        raise RuntimeError("SUB_PUBLIC_BASE or SUB_PUBLIC_IP required for subscription URLs")
    return f"https://{host}:{settings.sub_port}"


def sub_url(settings: Settings, token: str) -> str:
    return f"{_https_sub_base(settings)}/sub/{token}/"


def sub_url_user(settings: Settings, user: User) -> str:
    return sub_url(settings, user.sub_token)


def sub_url_user_link(settings: Settings, link: UserLink) -> str:
    return sub_url(settings, link.sub_token)


def sub_url_issued(settings: Settings, link: IssuedLink) -> str:
    return sub_url(settings, link.sub_token)


def balancer_for_uuid(settings: Settings, client_uuid: str, name: str = "XENO") -> dict:
    return build_happ_balancer_config(
        client_uuid=client_uuid,
        display_name=name,
        ru_host=settings.ru_public_host,
        ru_port=settings.client_port,
        ru_sni=settings.reality_sni,
        ru_pbk=settings.reality_pbk,
        ru_sid=settings.reality_sid,
        ru_path=settings.bridge_path,
        xhttp_mode=settings.xhttp_mode,
        backups_enabled=settings.backups_enabled,
        nl_host=settings.nl_public_host,
        nl_direct_port=settings.nl_direct_port,
        direct_sni=settings.direct_sni,
        direct_pbk=settings.direct_pbk,
        direct_sid=settings.direct_sid,
        direct_path=settings.direct_path,
        fingerprint=settings.reality_client_fp,
    )


def write_access_sub(settings: Settings, token: str, client_uuid: str, name: str = "XENO") -> None:
    if settings.sub_format == "balancer":
        write_user_sub_file(
            settings.sub_root,
            token,
            balancer_config=balancer_for_uuid(settings, client_uuid, name=name),
        )
    else:
        # URI list without HY2 (parked). RU first; client picks 🇷🇺 manually in Happ.
        links = build_profile_links(
            client_uuid=client_uuid,
            name=name,
            ru_host=settings.ru_public_host,
            ru_port=settings.client_port,
            ru_sni=settings.reality_sni,
            ru_pbk=settings.reality_pbk,
            ru_sid=settings.reality_sid,
            ru_path=settings.bridge_path,
            xhttp_mode=settings.xhttp_mode,
            backups_enabled=settings.backups_enabled,
            nl_host=settings.nl_public_host,
            nl_direct_port=settings.nl_direct_port,
            direct_sni=settings.direct_sni,
            direct_pbk=settings.direct_pbk,
            direct_sid=settings.direct_sid,
            direct_path=settings.direct_path,
            hy2_port=0,
            fingerprint=settings.reality_client_fp,
        )
        write_user_sub_file(settings.sub_root, token, links)


def remove_access_sub(settings: Settings, token: str) -> None:
    """Delete Happ subscription directory for a token. Never touch other tokens."""
    token = (token or "").strip()
    if not token or "/" in token or "\\" in token or token in (".", ".."):
        raise ValueError("refusing remove_access_sub with unsafe token")
    d = settings.sub_root / token
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=False)


def profiles_for_uuid(settings: Settings, client_uuid: str, name: str = "XENO") -> list[str]:
    return build_profile_links(
        client_uuid=client_uuid,
        name=name,
        ru_host=settings.ru_public_host,
        ru_port=settings.client_port,
        ru_sni=settings.reality_sni,
        ru_pbk=settings.reality_pbk,
        ru_sid=settings.reality_sid,
        ru_path=settings.bridge_path,
        xhttp_mode=settings.xhttp_mode,
        backups_enabled=settings.backups_enabled,
        nl_host=settings.nl_public_host,
        nl_direct_port=settings.nl_direct_port,
        direct_sni=settings.direct_sni,
        direct_pbk=settings.direct_pbk,
        direct_sid=settings.direct_sid,
        direct_path=settings.direct_path,
        hy2_port=settings.hy2_port if settings.backups_enabled else 0,
        fingerprint=settings.reality_client_fp,
    )


def vless_for_uuid(settings: Settings, client_uuid: str, name: str = "XENO") -> str:
    """Primary RU link (first profile). Kept for Telegram message display."""
    return profiles_for_uuid(settings, client_uuid, name=name)[0]


def vless_for(settings: Settings, user: User) -> str:
    return vless_for_uuid(settings, user.client_uuid, name="XENO")


def vless_user_link(settings: Settings, link: UserLink) -> str:
    return vless_for_uuid(settings, link.client_uuid, name=link.profile_name)


def vless_issued(settings: Settings, link: IssuedLink) -> str:
    return vless_for_uuid(settings, link.client_uuid, name=f"XENO #{link.id}")


def profiles_for(settings: Settings, user: User) -> list[str]:
    return profiles_for_uuid(settings, user.client_uuid, name="XENO")


def profiles_issued(settings: Settings, link: IssuedLink) -> list[str]:
    return profiles_for_uuid(settings, link.client_uuid, name=f"XENO #{link.id}")


def balancer_for(settings: Settings, user: User) -> dict:
    return balancer_for_uuid(settings, user.client_uuid, name="XENO")


def balancer_issued(settings: Settings, link: IssuedLink) -> dict:
    return balancer_for_uuid(settings, link.client_uuid, name=f"XENO #{link.id}")


def _email_map(db: Database) -> dict[str, str]:
    emails: dict[str, str] = {}
    for link in db.list_active_issued():
        emails[link.client_uuid] = client_email(
            link.client_uuid,
            tg_id=link.assigned_tg_id,
            issued_id=link.id,
        )
    for u in db.list_active_users():
        emails[u.client_uuid] = client_email(u.client_uuid, tg_id=u.tg_id, slot=1)
    for parent, ulink in db.list_active_extra_links():
        emails[ulink.client_uuid] = client_email(
            ulink.client_uuid, tg_id=parent.tg_id, slot=ulink.slot
        )
    return emails


def _seed_diag_row(db: Database, email: str, *, provisioned_at: int | None = None) -> None:
    day = time.strftime("%Y-%m-%d", time.gmtime())
    db.diag_ensure_user_day(day, email, provisioned_at=provisioned_at or int(time.time()))


def sync_all(db: Database, settings: Settings, *, rewrite_subs: bool = True) -> None:
    """Push active clients to 3x-ui XENO inbound + RU Xray + NL backups.

    rewrite_subs=False: still hot-adds clients, but skips rewriting Happ sub files
    (use when adding a second link so existing device URLs stay untouched on disk).
    """
    t0 = time.monotonic()
    uuids = db.list_active_client_uuids()
    if settings.bootstrap_client_uuid and settings.bootstrap_client_uuid not in uuids:
        uuids.insert(0, settings.bootstrap_client_uuid)
    emails = _email_map(db)
    if settings.bootstrap_client_uuid and settings.bootstrap_client_uuid not in emails:
        emails[settings.bootstrap_client_uuid] = client_email(settings.bootstrap_client_uuid)

    emit_ops(
        KIND_SYNC_ALL_START,
        clients=len(uuids),
        rewrite_subs=bool(rewrite_subs),
        backups=bool(settings.backups_enabled),
    )
    try:
        try:
            sync_xui_clients(db, settings)
        except Exception as e:
            _append_provision_log(f"xui sync warn: {e}")
            raise

        sync_xray_clients_remote(
            ru_host=settings.ru_ssh_host,
            ru_user=settings.ru_ssh_user,
            ru_password=settings.ru_ssh_pass,
            remote_config_path=settings.ru_xray_config,
            client_uuids=uuids,
            bridge_private_key=settings.reality_private_key,
            bridge_short_id=settings.reality_sid,
            bridge_dest=settings.reality_dest,
            bridge_sni=settings.reality_sni,
            bridge_path=settings.bridge_path,
            nl_exit_ip=settings.nl_exit_ip,
            relay_uuid=settings.relay_uuid,
            relay_public_key=settings.relay_public_key,
            relay_short_id=settings.relay_short_id,
            relay_sni=settings.relay_sni,
            relay_path=settings.relay_path,
            relay_port=settings.relay_port,
            client_port=settings.client_port,
            client_emails=emails,
        )
        if settings.backups_enabled:
            try:
                sync_nl_direct_clients_local(
                    uuids,
                    config_path=settings.nl_relay_config,
                    client_emails=emails,
                )
                if settings.hy2_enabled:
                    sync_hy2_users_local(uuids, config_path=settings.hy2_config)
                else:
                    _append_provision_log("hy2 parked (HY2_ENABLED=0) — skip sync/restart")
            except Exception as e:
                _append_provision_log(f"backup sync warn: {e}")
                raise
        if rewrite_subs:
            for u in db.list_active_users():
                write_access_sub(settings, u.sub_token, u.client_uuid, name="XENO")
                _seed_diag_row(
                    db,
                    client_email(u.client_uuid, tg_id=u.tg_id, slot=1),
                    provisioned_at=u.created_at,
                )
            for parent, ulink in db.list_active_extra_links():
                write_access_sub(
                    settings, ulink.sub_token, ulink.client_uuid, name=ulink.profile_name
                )
                _seed_diag_row(
                    db,
                    client_email(ulink.client_uuid, tg_id=parent.tg_id, slot=ulink.slot),
                    provisioned_at=ulink.created_at,
                )
            for link in db.list_active_issued():
                write_access_sub(
                    settings, link.sub_token, link.client_uuid, name=f"XENO #{link.id}"
                )
                _seed_diag_row(
                    db,
                    client_email(
                        link.client_uuid, tg_id=link.assigned_tg_id, issued_id=link.id
                    ),
                    provisioned_at=link.created_at,
                )
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for u in db.list_active_users():
            _append_provision_log(
                f"{ts} user tg_id={u.tg_id} uuid={u.client_uuid} "
                f"email={client_email(u.client_uuid, tg_id=u.tg_id)} sni={settings.reality_sni} "
                f"backups={int(settings.backups_enabled)} sub={settings.sub_format}"
            )
        for parent, ulink in db.list_active_extra_links():
            _append_provision_log(
                f"{ts} user_link tg_id={parent.tg_id} slot={ulink.slot} uuid={ulink.client_uuid} "
                f"email={client_email(ulink.client_uuid, tg_id=parent.tg_id, slot=ulink.slot)} "
                f"sni={settings.reality_sni}"
            )
    except Exception as e:
        emit_ops(
            KIND_SYNC_ALL_ERROR,
            error=type(e).__name__,
            detail=str(e)[:200],
            duration_ms=int((time.monotonic() - t0) * 1000),
            clients=len(uuids),
        )
        raise
    else:
        emit_ops(
            KIND_SYNC_ALL_END,
            ok=True,
            clients=len(uuids),
            rewrite_subs=bool(rewrite_subs),
            duration_ms=int((time.monotonic() - t0) * 1000),
        )


def ban_and_purge(
    db: Database,
    settings: Settings,
    *,
    tg_id: int | None = None,
    username: str | None = None,
    reason: str = "",
    banned_by: int | None = None,
) -> dict:
    """Permanent ban: DB row, deactivate access, delete sub dirs, sync Xray/3x-ui."""
    user = db.get(tg_id) if tg_id is not None else None
    if user is None and username:
        user = db.get_by_username(username)
    if user is None and tg_id is None:
        raise LookupError(f"user not found: {username!r}")
    if user is None:
        # Ban by tg_id even if never claimed — still blocks future demo.
        assert tg_id is not None
        db.ban_user(tg_id=tg_id, username=username, reason=reason, banned_by=banned_by)
        sync_all(db, settings, rewrite_subs=True)
        return {"tg_id": tg_id, "username": username, "had_access": False}

    tokens = [user.sub_token]
    for link in db.list_user_links(user.tg_id):
        tokens.append(link.sub_token)
    # list_user_links only active — also collect inactive after ban? ban deactivates first
    # Get tokens BEFORE ban
    with db._conn() as con:  # noqa: SLF001
        rows = con.execute(
            "SELECT sub_token FROM user_links WHERE tg_id = ?", (user.tg_id,)
        ).fetchall()
    for r in rows:
        if r["sub_token"] not in tokens:
            tokens.append(r["sub_token"])

    db.ban_user(
        tg_id=user.tg_id,
        username=username or user.username,
        reason=reason,
        banned_by=banned_by,
    )
    for tok in tokens:
        try:
            remove_access_sub(settings, tok)
        except (FileNotFoundError, ValueError, OSError) as exc:
            _append_provision_log(f"ban remove sub warn tg={user.tg_id}: {exc}")
    sync_all(db, settings, rewrite_subs=True)
    _append_provision_log(
        f"BAN tg_id={user.tg_id} username={username or user.username} reason={reason!r}"
    )
    emit_ops(
        "user_ban",
        tg_id=user.tg_id,
        username=(username or user.username or "")[:64],
        reason=(reason or "")[:200],
        banned_by=banned_by,
    )
    return {
        "tg_id": user.tg_id,
        "username": username or user.username,
        "had_access": True,
        "tokens_removed": len(tokens),
    }


def provision_demo(db: Database, settings: Settings, tg_id: int, username: str | None) -> User:
    user, _created = db.claim_demo(tg_id, username, settings.demo_days)
    assert user is not None
    try:
        write_access_sub(settings, user.sub_token, user.client_uuid, name="XENO")
        sync_all(db, settings)
    except Exception:
        raise
    return user


def provision_second_link(db: Database, settings: Settings, tg_id: int) -> UserLink:
    """Issue slot-2 device link. Does not rotate primary UUID/token or demo expiry."""
    link = db.claim_second_link(tg_id)
    try:
        write_access_sub(
            settings, link.sub_token, link.client_uuid, name=link.profile_name
        )
        # Hot-add new UUID; skip rewriting other users' sub files on disk.
        sync_all(db, settings, rewrite_subs=False)
        parent = db.get(tg_id)
        _seed_diag_row(
            db,
            client_email(link.client_uuid, tg_id=tg_id, slot=link.slot),
            provisioned_at=link.created_at,
        )
        _append_provision_log(
            f"second_link tg_id={tg_id} slot={link.slot} uuid={link.client_uuid} "
            f"expires_with={parent.expires_at if parent else '?'}"
        )
    except Exception:
        # Soft-disable the new row only — never touch primary users.*
        db.deactivate_user_link(link.id)
        raise
    return db.get_user_link(tg_id, link.slot)  # type: ignore


def provision_remove_second_link(db: Database, settings: Settings, tg_id: int) -> UserLink:
    """Revoke slot-2 only: DB, Happ sub dir, 3x-ui client, Xray UUID list.

    Primary users.* UUID/token and sub file stay untouched.
    """
    parent = db.get(tg_id)
    if not parent or not parent.is_active:
        raise PermissionError("no active access")
    link = db.revoke_second_link(tg_id)
    email = client_email(link.client_uuid, tg_id=tg_id, slot=link.slot)
    try:
        try:
            remove_access_sub(settings, link.sub_token)
        except FileNotFoundError:
            pass
        except ValueError:
            raise
        except OSError as exc:
            _append_provision_log(f"second_link remove sub warn tg_id={tg_id}: {exc}")

        if settings.xui_base_url and settings.xui_api_token and settings.xui_inbound_id:
            if settings.xui_inbound_id in SACRED_INBOUND_IDS:
                raise RuntimeError(
                    f"XUI_INBOUND_ID={settings.xui_inbound_id} is sacred — refused"
                )
            try:
                xui(settings).delete_client(email)
            except XuiError as exc:
                msg = str(exc).lower()
                if "not found" not in msg and "record not found" not in msg:
                    raise
                _append_provision_log(
                    f"second_link xui del skip tg_id={tg_id} email={email}: {exc}"
                )

        # UUID gone from active set → decide_sync_action restarts inbound (safe remove).
        sync_all(db, settings, rewrite_subs=False)
        _append_provision_log(
            f"second_link_removed tg_id={tg_id} slot={link.slot} uuid={link.client_uuid} "
            f"email={email} primary_uuid={parent.client_uuid}"
        )
    except Exception:
        # Restore the same slot-2 credentials so the user is not left half-revoked.
        db.reactivate_user_link(
            link.id, client_uuid=link.client_uuid, sub_token=link.sub_token
        )
        try:
            write_access_sub(
                settings, link.sub_token, link.client_uuid, name=link.profile_name
            )
        except Exception as restore_exc:
            _append_provision_log(
                f"second_link restore sub warn tg_id={tg_id}: {restore_exc}"
            )
        raise
    return link


def provision_issued(
    db: Database,
    settings: Settings,
    *,
    admin_id: int,
    days: int,
    assigned_tg_id: int | None = None,
    assigned_username: str | None = None,
) -> IssuedLink:
    link = db.create_issued_link(
        created_by=admin_id,
        days=days,
        label=assigned_username,
        assigned_tg_id=assigned_tg_id,
        assigned_username=assigned_username,
    )
    try:
        if assigned_tg_id is not None:
            db.grant_access(
                tg_id=assigned_tg_id,
                username=assigned_username,
                days=days,
                plan=f"issued-{days}",
            )
            db.update_user_creds(
                assigned_tg_id,
                client_uuid=link.client_uuid,
                sub_token=link.sub_token,
            )
        write_access_sub(settings, link.sub_token, link.client_uuid, name=f"XENO #{link.id}")
        sync_all(db, settings)
    except Exception:
        db.deactivate_issued(link.id)
        raise
    return db.get_issued(link.id)  # type: ignore


def provision_grant_user(
    db: Database,
    settings: Settings,
    *,
    tg_id: int,
    username: str | None,
    days: int,
    admin_id: int,
) -> tuple[User, IssuedLink]:
    link = provision_issued(
        db,
        settings,
        admin_id=admin_id,
        days=days,
        assigned_tg_id=tg_id,
        assigned_username=username,
    )
    user = db.get(tg_id)
    assert user is not None
    return user, link
