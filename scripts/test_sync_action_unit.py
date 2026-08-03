"""Unit tests for zero-downtime xray sync decision logic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))

from xray_sync import (
    NL_DIRECT_INBOUND,
    RU_CLIENT_INBOUND,
    _adu_payload,
    _ensure_api_block,
    _hot_add_ok,
    build_ru_config,
    decide_sync_action,
)

BASE_KW = dict(
    bridge_private_key="priv",
    bridge_short_id="abcd1234abcd1234",
    bridge_dest="www.cloudflare.com:443",
    bridge_sni="www.cloudflare.com",
    bridge_path="/bridge",
    nl_exit_ip="1.2.3.4",
    relay_uuid="22222222-2222-2222-2222-222222222222",
    relay_public_key="pub",
    relay_short_id="deadbeefdeadbeef",
    relay_sni="www.cloudflare.com",
    relay_path="/relay",
)


def _ru_cfg(*uuids: str) -> dict:
    return build_ru_config(list(uuids), **BASE_KW)


def test_unchanged_skips() -> None:
    old = _ru_cfg("11111111-1111-1111-1111-111111111111")
    new = _ru_cfg("11111111-1111-1111-1111-111111111111")
    action, added = decide_sync_action(old, new, RU_CLIENT_INBOUND)
    assert action == "skip"
    assert added == set()


def test_new_client_hot_add() -> None:
    old = _ensure_api_block(_ru_cfg("11111111-1111-1111-1111-111111111111"))
    new = _ensure_api_block(
        _ru_cfg(
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        )
    )
    action, added = decide_sync_action(old, new, RU_CLIENT_INBOUND)
    assert action == "hot_add"
    assert added == {"22222222-2222-2222-2222-222222222222"}


def test_removed_client_restarts() -> None:
    old = _ensure_api_block(
        _ru_cfg(
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        )
    )
    new = _ensure_api_block(_ru_cfg("11111111-1111-1111-1111-111111111111"))
    action, _ = decide_sync_action(old, new, RU_CLIENT_INBOUND)
    assert action == "restart"


def test_no_api_in_existing_restarts() -> None:
    old = _ru_cfg("11111111-1111-1111-1111-111111111111")
    del old["api"]  # simulate pre-migration live config
    new = _ensure_api_block(
        _ru_cfg(
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        )
    )
    action, _ = decide_sync_action(old, new, RU_CLIENT_INBOUND)
    assert action == "restart"


def test_structural_change_restarts() -> None:
    old = _ensure_api_block(_ru_cfg("11111111-1111-1111-1111-111111111111"))
    new = _ensure_api_block(_ru_cfg("11111111-1111-1111-1111-111111111111"))
    client_in = next(i for i in new["inbounds"] if i["tag"] == RU_CLIENT_INBOUND)
    client_in["streamSettings"]["xhttpSettings"]["path"] = "/other"
    action, _ = decide_sync_action(old, new, RU_CLIENT_INBOUND)
    assert action == "restart"


def test_missing_stats_restarts() -> None:
    old = _ru_cfg("11111111-1111-1111-1111-111111111111")
    old["api"] = {"tag": "api", "services": ["HandlerService"]}
    new = _ensure_api_block(_ru_cfg("11111111-1111-1111-1111-111111111111"))
    action, _ = decide_sync_action(old, new, RU_CLIENT_INBOUND)
    assert action == "restart"


def test_nl_direct_tag() -> None:
    old = _ensure_api_block({"inbounds": [{"tag": NL_DIRECT_INBOUND, "settings": {"clients": [{"id": "11111111-1111-1111-1111-111111111111", "email": "a"}]}}]})
    new = _ensure_api_block({"inbounds": [{"tag": NL_DIRECT_INBOUND, "settings": {"clients": [{"id": "11111111-1111-1111-1111-111111111111", "email": "a"}, {"id": "22222222-2222-2222-2222-222222222222", "email": "b"}]}}]})
    action, added = decide_sync_action(old, new, NL_DIRECT_INBOUND)
    assert action == "hot_add"
    assert "22222222-2222-2222-2222-222222222222" in added


def test_adu_payload_includes_port_and_stream() -> None:
    cfg = _ru_cfg("11111111-1111-1111-1111-111111111111")
    clients = [{"id": "22222222-2222-2222-2222-222222222222", "email": "tg-2"}]
    payload = _adu_payload(cfg, RU_CLIENT_INBOUND, clients)
    inbound = payload["inbounds"][0]
    assert inbound["tag"] == RU_CLIENT_INBOUND
    assert inbound.get("port") == 443
    assert "streamSettings" in inbound
    assert inbound["settings"]["clients"] == clients
    # minimal payload must NOT be what we send
    assert "listen" in inbound or inbound.get("port")


def test_hot_add_ok_rejects_zero() -> None:
    assert not _hot_add_ok("Added 0 user(s) in total.", "failed to build config: no Port")
    assert _hot_add_ok("result: ok\nAdded 1 user(s) in total.", "")


if __name__ == "__main__":
    test_unchanged_skips()
    test_new_client_hot_add()
    test_removed_client_restarts()
    test_no_api_in_existing_restarts()
    test_structural_change_restarts()
    test_missing_stats_restarts()
    test_nl_direct_tag()
    test_adu_payload_includes_port_and_stream()
    test_hot_add_ok_rejects_zero()
    print("OK sync action unit tests")
