#!/usr/bin/env python3
"""Local functional tests for bot DB + messages + godmode (no SSH/TG)."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

from db import Database  # noqa: E402
import messages as msg  # noqa: E402
import keyboards as kb  # noqa: E402
from provision import _panel_comment  # noqa: E402


def test_demo_once() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Database(Path(tmp) / "t.db")
        u, created = db.claim_demo(111, "alice", 30)
        assert created and u.is_active and u.demo_claimed == 1
        try:
            db.claim_demo(111, "alice", 30)
            raise AssertionError("second demo must fail")
        except PermissionError:
            pass
        print("OK demo once-per-tg")


def test_permanent_ban() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Database(Path(tmp) / "t.db")
        u, _ = db.claim_demo(777, "ice1477", 30)
        assert u.is_active
        db.ban_user(tg_id=777, username="ice1477", reason="abuse")
        assert db.is_banned(777)
        assert db.is_banned(username="Ice1477")
        assert db.is_banned(username="@ice1477")
        banned_user = db.get(777)
        assert banned_user is not None and not banned_user.is_active
        try:
            db.claim_demo(777, "ice1477", 30)
            raise AssertionError("banned tg must not reclaim demo")
        except PermissionError as exc:
            assert str(exc) == "banned"
        try:
            db.grant_access(tg_id=888, username="ice1477", days=30, plan="issued-30")
            raise AssertionError("banned username must not get grant")
        except PermissionError as exc:
            assert str(exc) == "banned"
        assert "закрыт" in msg.access_banned().lower()
        assert "Подключиться" not in str(kb.home(banned=True))
        print("OK permanent ban blocks demo/grant")


def test_issued_god() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Database(Path(tmp) / "t.db")
        link = db.create_issued_link(
            created_by=10001,
            days=90,
            assigned_tg_id=111,
            assigned_username="alice",
        )
        assert link.is_active and "90" in link.plan
        assert link.assigned_tg_id == 111
        assert link.client_uuid in db.list_active_client_uuids()
        assert db.count_active_issued() == 1
        user = db.grant_access(tg_id=111, username="alice", days=90, plan="issued-90")
        db.update_user_creds(111, client_uuid=link.client_uuid, sub_token=link.sub_token)
        assert db.get(111).client_uuid == link.client_uuid
        print("OK issued links + bind")


def test_ux_no_reply_kb() -> None:
    assert "remove_keyboard" in kb.remove_reply_keyboard()
    home = kb.home(godmode=True)
    assert "inline_keyboard" in home
    assert "Godmode" in str(home)
    assert "Клиенты" not in str(home)
    assert "Подключить устройство" in str(kb.home(has_access=True))
    assert "Подключиться за 1 минуту" in str(kb.home(has_access=False))
    assert "Обновить" not in str(kb.home(has_access=True))
    assert "Обновить" not in str(kb.after_access(sub_url="https://x"))
    assert "Обновить" not in str(kb.status_actions())
    assert not hasattr(kb, "CB_REFRESH")
    assert not hasattr(msg, "refreshing")
    assert not hasattr(msg, "access_refreshed")
    assert "Второе устройство" in str(
        kb.after_access(sub_url="https://x", can_add_second=True)
    )
    assert "Клиенты" not in str(kb.after_access(sub_url="https://x"))
    assert "remove_keyboard" not in str(home)
    plat = kb.platform_pick()
    assert "iOS" in str(plat) and "Android" in str(plat)
    texts = [
        msg.home(has_access=True),
        msg.home(has_access=False),
        msg.onboard_pick_platform(),
        msg.connect_guide_platform("ios"),
        msg.connect_guide_platform("android", has_access=True),
        msg.pricing(),
        msg.pricing(on_waitlist=True),
        msg.waitlist_joined(),
        msg.expiry_nudge(days=3, expires_at=int(time.time()) + 3 * 86400, plan="demo"),
        msg.god_home(active_users=1, issued_active=2, waitlist=3),
    ]
    for t in texts:
        assert "XENO.net" in t and "<b>" in t
    assert "Приватность — не привилегия." in msg.home()
    assert "Подключиться за 1 минуту" in msg.home(has_access=False)
    assert "Godmode" not in msg.home(has_access=True)
    assert not hasattr(msg, "apps_guide")
    assert not hasattr(kb, "CB_APPS")
    assert not hasattr(kb, "apps_actions")
    assert "Google Play" in str(kb.platform_guide_actions(platform="android"))
    assert "App Store" in str(kb.platform_guide_actions(platform="ios"))
    assert "Узнать первым" in str(kb.pricing(on_waitlist=False))
    assert "листе ожидания" in str(kb.pricing(on_waitlist=True))
    print("OK ux strings + inline home + platform")


def test_waitlist_db() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Database(Path(tmp) / "t.db")
        assert db.join_waitlist(444, "dana") is True
        assert db.is_on_waitlist(444)
        assert db.count_waitlist() == 1
        assert db.join_waitlist(444, "dana") is False
        assert db.count_waitlist() == 1
        print("OK waitlist db")


def test_second_link_additive() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Database(Path(tmp) / "t.db")
        u, _ = db.claim_demo(222, "bob", 30)
        primary_uuid, primary_token = u.client_uuid, u.sub_token
        assert db.count_user_links(222) == 1
        assert db.can_claim_second_link(222)
        assert "Второе устройство" in str(kb.after_access(sub_url="https://x", can_add_second=True))
        assert "Убрать устройство 2" not in str(
            kb.after_access(sub_url="https://x", can_add_second=True)
        )
        assert "Второе устройство" not in str(
            kb.after_access(sub_url="https://x", sub_url_2="https://y", can_add_second=False)
        )
        two = kb.after_access(sub_url="https://x", sub_url_2="https://y", can_add_second=False)
        assert "Убрать устройство 2" in str(two)
        assert kb.CB_SECOND_DEL in str(two)
        assert "Устройство 1" not in str(two)
        assert "Устройство 2" not in str(two).replace("Убрать устройство 2", "")
        assert "Открыть подписку" not in str(two)
        assert "Открыть вторую" not in str(two)
        assert "https://x" not in str(two)
        assert "https://y" not in str(two)
        one = kb.after_access(sub_url="https://x", can_add_second=False)
        assert "Открыть подписку" in str(one)
        assert "Устройство 1" not in str(one)
        assert "https://x" in str(one)
        confirm = kb.second_delete_confirm()
        assert "Да, убрать" in str(confirm) and "Отмена" in str(confirm)
        assert kb.CB_SECOND_DEL_YES in str(confirm) and kb.CB_SECOND_DEL_NO in str(confirm)
        link = db.claim_second_link(222)
        assert link.slot == 2
        assert link.client_uuid != primary_uuid
        assert link.sub_token != primary_token
        u2 = db.get(222)
        assert u2.client_uuid == primary_uuid and u2.sub_token == primary_token
        assert u2.expires_at == u.expires_at
        assert db.count_user_links(222) == 2
        assert not db.can_claim_second_link(222)
        assert link.client_uuid in db.list_active_client_uuids()
        try:
            db.claim_second_link(222)
            raise AssertionError("third link must fail")
        except PermissionError:
            pass
        from xray_sync import client_email

        assert client_email(primary_uuid, tg_id=222, slot=1) == "tg-222"
        assert client_email(link.client_uuid, tg_id=222, slot=2) == "tg-222-2"
        assert "XENO #2" in link.profile_name
        assert "Второе устройство" in msg.demo_ok(
            expires_at=u.expires_at, sub="https://s", vless="vless://", demo_days=30
        )
        print("OK second link additive (primary untouched)")


def test_second_link_revoke() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        db = Database(root / "t.db")
        u, _ = db.claim_demo(333, "cara", 30)
        primary_uuid, primary_token = u.client_uuid, u.sub_token
        link = db.claim_second_link(333)
        slot2_uuid, slot2_token = link.client_uuid, link.sub_token
        # Fake Happ sub dirs for both tokens
        for tok in (primary_token, slot2_token):
            d = root / "sub" / tok
            d.mkdir(parents=True)
            (d / "sub.txt").write_text("x", encoding="utf-8")

        from provision import remove_access_sub

        class S:
            sub_root = root / "sub"

        revoked = db.revoke_second_link(333)
        assert revoked.id == link.id
        assert db.get_user_link(333) is None
        assert db.count_user_links(333) == 1
        assert db.can_claim_second_link(333)
        u2 = db.get(333)
        assert u2.client_uuid == primary_uuid and u2.sub_token == primary_token
        assert slot2_uuid not in db.list_active_client_uuids()
        assert primary_uuid in db.list_active_client_uuids()

        remove_access_sub(S(), slot2_token)
        assert not (root / "sub" / slot2_token).exists()
        assert (root / "sub" / primary_token / "sub.txt").exists()

        try:
            db.revoke_second_link(333)
            raise AssertionError("revoke without slot 2 must fail")
        except PermissionError:
            pass

        # Re-claim after revoke: new credentials, primary still untouched
        link2 = db.claim_second_link(333)
        assert link2.slot == 2
        assert link2.client_uuid != slot2_uuid
        assert link2.sub_token != slot2_token
        u3 = db.get(333)
        assert u3.client_uuid == primary_uuid and u3.sub_token == primary_token
        assert "Устройство 2 отключено" in msg.second_deleted()
        assert "Первая подписка" in msg.second_delete_confirm()
        access = msg.access_active(
            user=u3,
            sub="https://s",
            vless="vless://",
            can_add_second=True,
            notice=msg.second_deleted(),
        )
        assert "Устройство 2 отключено" in access
        assert "Устройство 2</b>" not in access
        print("OK second link revoke (primary untouched, re-claim works)")


def test_subscription_body_plain_uris() -> None:
    import base64

    from sub_server import _normalize_body
    from xray_sync import subscription_body, write_user_sub_file

    links = [
        "vless://11111111-1111-1111-1111-111111111111@ru.example:443?type=xhttp#RU",
        "vless://11111111-1111-1111-1111-111111111111@nl.example:2053?type=xhttp#NL",
    ]
    body = subscription_body(links)
    assert body.startswith("vless://")
    assert "vless://" in body.splitlines()[1]
    assert base64.b64encode(body.encode()).decode() != body

    plain, is_json = _normalize_body(body)
    assert not is_json and plain.startswith("vless://")

    legacy = base64.b64encode(body.encode()).decode()
    decoded, is_json = _normalize_body(legacy)
    assert not is_json and decoded.startswith("vless://")
    assert "NL" in decoded

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = write_user_sub_file(Path(tmp), "tok", links)
        assert path.read_text(encoding="utf-8").startswith("vless://")
    print("OK subscription plain URI + legacy base64 normalize")


def test_sub_url_always_https() -> None:
    from provision import sub_url

    class S:
        sub_public_base = "https://nl.example.com:2080"
        sub_public_ip = "1.2.3.4"
        sub_port = 2080

    assert sub_url(S(), "tok").startswith("https://")
    assert "http://" not in sub_url(S(), "tok").replace("https://", "", 1)

    class SHttp:
        sub_public_base = "http://nl.example.com:2080"
        sub_public_ip = "1.2.3.4"
        sub_port = 2080

    assert sub_url(SHttp(), "tok") == "https://nl.example.com:2080/sub/tok/"

    class SEmpty:
        sub_public_base = ""
        sub_public_ip = "1.2.3.4"
        sub_port = 2080

    assert sub_url(SEmpty(), "tok") == "https://1.2.3.4:2080/sub/tok/"
    print("OK sub_url always https")


def test_expiry_nag_db() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Database(Path(tmp) / "t.db")
        now = int(time.time())
        db.claim_demo(333, "cara", 30)
        with db._conn() as con:
            con.execute(
                "UPDATE users SET expires_at = ? WHERE tg_id = 333",
                (now + 2 * 86400,),
            )
        assert len(db.list_users_for_expiry_nag(nag_kind="d3")) == 1
        assert len(db.list_users_for_expiry_nag(nag_kind="d1")) == 0
        db.mark_expiry_nag(333, "d3")
        assert db.expiry_nag_sent(333, "d3")
        assert len(db.list_users_for_expiry_nag(nag_kind="d3")) == 0
        print("OK expiry nag db")


def test_godmode_id_in_config_source() -> None:
    src = (ROOT / "bot" / "config.py").read_text(encoding="utf-8")
    assert "ADMIN_IDS" in src
    # No hardcoded personal telegram ids in config
    assert "admin_ids = frozenset(set(admin_ids) |" not in src
    print("OK admin ids from env only")


def test_panel_comment() -> None:
    assert _panel_comment(username="alice", tg_id=1001) == "@alice"
    assert _panel_comment(username="@bob", tg_id=1002) == "@bob"
    assert _panel_comment(tg_id=123) == "tg-123"
    assert _panel_comment(tg_id=123, slot=2) == "tg-123 ·2"
    assert _panel_comment(username="alice", slot=2) == "@alice ·2"
    assert _panel_comment(issued_id=7) == "issued-7"
    assert _panel_comment(username="alice", issued_id=7) == "@alice"
    print("OK panel comment template")


def test_help_has_source_link() -> None:
    help_body = msg.help_text(demo_days=30)
    assert "Поддержка" in help_body
    assert "github.com/kriogenik/XENO.net" in help_body
    assert "публичный репозиторий" in help_body
    assert "открытый код" in str(kb.help_actions()).lower()
    assert "Написать нам" in str(kb.help_actions())
    assert "Написать нам" in help_body
    assert "главном меню" in help_body
    help_kb = str(kb.help_actions())
    help_kb_donate = str(kb.help_actions(donate=True))
    # Политика / Тарифы / Статус live on home — not duplicated in Help
    assert "Политика" not in help_kb
    assert "Политика" not in help_kb_donate
    assert "Тарифы" not in help_kb
    assert "Статус" not in help_kb
    assert "Поддержать" not in str(kb.help_actions(donate=False))
    assert "Поддержать" in help_kb_donate
    assert "Поддержать" not in msg.help_text(demo_days=30, donate=False)
    assert "Поддержать" in msg.help_text(demo_days=30, donate=True)
    home = str(kb.home())
    assert "Политика" in home
    assert "Поддержка" in home
    assert "Тарифы" in home
    assert "Статус" in home
    print("OK help source link")


def test_policy_screen() -> None:
    body = msg.policy_text()
    assert "Политика" in body
    assert "Нулевая терпимость" in body
    assert "не продаём" in body.lower() or "не продаем" in body.lower()
    assert "партнёр" in body.lower() or "партнер" in body.lower()
    assert "github.com/kriogenik/XENO.net" in body
    assert "товар" in body.lower()
    assert "TRC20" not in body
    assert "bc1" not in body
    home = str(kb.home())
    assert "Политика" in home
    assert "Поддержка" in home
    actions = str(kb.policy_actions())
    assert "Поддержка" in actions
    assert "В меню" in actions
    print("OK policy screen")


def test_donate_screen() -> None:
    from config import DonateWallet

    empty = msg.donate_text(())
    assert "Поддержать" in empty
    assert "Без обязательств" in empty
    assert "пока не указаны" in empty.lower() or "появятся" in empty.lower()

    filled = msg.donate_text(
        (
            DonateWallet(coin="USDT · TRC20", address="TXenoTestUsdtAddr000000000000001"),
            DonateWallet(coin="TON", address="UQxenoTestTonAddr0000000000000002"),
            DonateWallet(coin="BTC", address="bc1qxenotestbtcaddr000000000000003"),
        )
    )
    assert "USDT · TRC20" in filled
    assert "<code>TXenoTestUsdtAddr000000000000001</code>" in filled
    assert "<code>UQxenoTestTonAddr0000000000000002</code>" in filled
    assert "<code>bc1qxenotestbtcaddr000000000000003</code>" in filled
    assert "скопировать" in filled.lower()
    assert "Поддержка" in str(kb.donate_actions())
    print("OK donate screen")


def test_donate_env_loader() -> None:
    import os

    from config import _load_donate_wallets

    saved = {
        k: os.environ.get(k)
        for k in ("DONATE_USDT_TRC20", "DONATE_TON", "DONATE_BTC")
    }
    try:
        for k in saved:
            os.environ.pop(k, None)
        assert _load_donate_wallets() == ()
        os.environ["DONATE_USDT_TRC20"] = "TOnlyUsdt"
        os.environ["DONATE_TON"] = "  "
        os.environ["DONATE_BTC"] = "bc1qonly"
        wallets = _load_donate_wallets()
        assert len(wallets) == 2
        assert wallets[0].coin == "USDT · TRC20" and wallets[0].address == "TOnlyUsdt"
        assert wallets[1].coin == "BTC" and wallets[1].address == "bc1qonly"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("OK donate env loader")


def test_support_dialog_db() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Database(Path(tmp) / "t.db")
        assert db.support_get_open(9001) is None
        d = db.support_open(9001)
        assert d["status"] == "open"
        assert db.support_get_open(9001)["id"] == d["id"]
        # reopen same open row
        assert db.support_open(9001)["id"] == d["id"]
        db.support_touch_user(int(d["id"]))
        db.support_add_bridge(admin_chat_id=1, admin_message_id=42, dialog_id=int(d["id"]))
        assert db.support_lookup_bridge(admin_chat_id=1, admin_message_id=42) == int(d["id"])
        assert db.support_close(int(d["id"]))
        assert db.support_get_open(9001) is None
        assert not db.support_can_reopen(9001, cooldown_sec=3600)
        assert db.support_can_reopen(9001, cooldown_sec=0)
        # idle close
        d2 = db.support_open(9002)
        with db._conn() as con:
            con.execute(
                "UPDATE support_dialogs SET opened_at = ?, last_user_at = ? WHERE id = ?",
                (1, 1, int(d2["id"])),
            )
        closed = db.support_close_idle(idle_sec=100)
        assert any(int(x["id"]) == int(d2["id"]) for x in closed)
        assert db.support_count_open() == 0
        print("OK support dialog db")


def test_support_ux_and_helpers() -> None:
    from support import RateLimiter, classify_message, public_id

    assert public_id(10) == "D-000a"
    assert "Диалог" in msg.support_intro()
    assert "самопомощ" in msg.support_intro().lower() or "Happ" in msg.support_intro()
    assert "ушло" in msg.support_accepted(public_id="D-0001").lower()
    card = msg.support_admin_card(
        public_id="D-0001",
        username="alice",
        tg_id=111,
        plan="Демо",
        access_label="активен",
        expires_label="01.01.2027",
        waitlist=False,
        devices=2,
    )
    assert "D-0001" in card and "@alice" in card and "111" in card
    assert "sub://" not in card.lower()
    assert "token" not in card.lower()
    assert "Написать" in str(kb.support_intro_actions())
    assert "Закрыть" in str(kb.support_after_send_actions())
    assert classify_message({"text": "hi"}) == "text"
    assert classify_message({"photo": [{"file_id": "x"}]}) == "photo"
    assert classify_message({"document": {"file_id": "x"}}) == "document"
    assert classify_message({"sticker": {"file_id": "x"}}) == "rejected"
    assert classify_message({"voice": {"file_id": "x"}}) == "rejected"
    rl = RateLimiter(limit=2, window_sec=600)
    assert rl.allow(1)
    rl.record(1)
    rl.record(1)
    assert not rl.allow(1)
    print("OK support ux + helpers")


def test_sub_response_headers_no_autoconnect() -> None:
    import sys
    from pathlib import Path

    bot = Path(__file__).resolve().parents[1] / "bot"
    if str(bot) not in sys.path:
        sys.path.insert(0, str(bot))
    from sub_server import build_sub_response_headers

    h = build_sub_response_headers(web_page="https://example.test/sub/tok/?v=1", is_json=False)
    assert h["Content-Type"].startswith("text/plain")
    assert "Content-Disposition" not in h
    assert "subscription-autoconnect" not in h
    assert "subscription-autoconnect-type" not in h
    assert "subscription-ping-onopen-enabled" not in h
    assert h["Cache-Control"].startswith("no-store")
    print("OK sub headers (no autoconnect, plain text)")


def main() -> int:
    test_demo_once()
    test_permanent_ban()
    test_issued_god()
    test_second_link_additive()
    test_second_link_revoke()
    test_subscription_body_plain_uris()
    test_sub_url_always_https()
    test_ux_no_reply_kb()
    test_waitlist_db()
    test_expiry_nag_db()
    test_panel_comment()
    test_godmode_id_in_config_source()
    test_help_has_source_link()
    test_policy_screen()
    test_donate_screen()
    test_donate_env_loader()
    test_support_dialog_db()
    test_support_ux_and_helpers()
    test_sub_response_headers_no_autoconnect()
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
