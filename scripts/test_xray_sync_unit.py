import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))

from xray_sync import (
    DOMESTIC_BYPASS_DOMAINS,
    build_happ_balancer_config,
    build_profile_links,
    build_ru_config,
    build_vless_link,
    flagged_profile_name,
)

cfg = build_ru_config(
    ["11111111-1111-1111-1111-111111111111"],
    bridge_private_key="x",
    bridge_short_id="abcd1234abcd1234",
    bridge_dest="www.cloudflare.com:443",
    bridge_sni="www.cloudflare.com",
    bridge_path="/bridgepath",
    nl_exit_ip="1.2.3.4",
    relay_uuid="22222222-2222-2222-2222-222222222222",
    relay_public_key="y",
    relay_short_id="deadbeefdeadbeef",
    relay_sni="www.cloudflare.com",
    relay_path="/relaypath",
    client_emails={"11111111-1111-1111-1111-111111111111": "tg-42"},
)
ib = next(i for i in cfg["inbounds"] if i["tag"] == "client-in")
ob = next(o for o in cfg["outbounds"] if o["tag"] == "nl-exit")
assert "StatsService" in cfg["api"]["services"]
assert cfg.get("stats") == {}
assert any(i.get("tag") == "api" for i in cfg["inbounds"])
assert ib["settings"]["clients"][0]["id"].startswith("1111")
assert ib["settings"]["clients"][0]["email"] == "tg-42"
assert "flow" not in ib["settings"]["clients"][0]
assert ib["streamSettings"]["network"] == "xhttp"
assert ib["streamSettings"]["xhttpSettings"]["path"] == "/bridgepath"
assert cfg["log"]["access"] == "/var/log/xeno/ru-access.log"
assert ob["streamSettings"]["security"] == "reality"
assert ob["streamSettings"]["network"] == "xhttp"
assert ob["streamSettings"]["xhttpSettings"]["path"] == "/relaypath"
assert "flow" not in ob["settings"]["vnext"][0]["users"][0]

link = build_vless_link(
    client_uuid="11111111-1111-1111-1111-111111111111",
    address="203.0.113.30",
    port=443,
    sni="www.cloudflare.com",
    pbk="pbk",
    sid="sid",
    path="/bridgepath",
)
assert "type=xhttp" in link
assert "flow" not in link
assert "vision" not in link.lower()
assert "path=%2Fbridgepath" in link or "path=/bridgepath" in link

from urllib.parse import unquote

assert flagged_profile_name("XENO RU", "RU") == "🇷🇺XENO RU"
assert flagged_profile_name("🇷🇺XENO RU", "RU") == "🇷🇺XENO RU"
profiles = build_profile_links(
    client_uuid="11111111-1111-1111-1111-111111111111",
    name="XENO",
    ru_host="ru.example",
    ru_port=443,
    ru_sni="dl.google.com",
    ru_pbk="pbk",
    ru_sid="sid",
    ru_path="/",
    xhttp_mode="auto",
    backups_enabled=True,
    nl_host="nl.example",
    nl_direct_port=2053,
    direct_sni="dl.google.com",
    direct_pbk="pbk2",
    direct_sid="sid2",
    direct_path="/",
    hy2_port=0,
)
assert unquote(profiles[0].split("#", 1)[1]) == "🇷🇺XENO RU"
assert unquote(profiles[1].split("#", 1)[1]) == "🇳🇱XENO NL Direct"

# Domestic shop bypass (Ozon/Magnit CDN) on RU + Happ balancer
dom_rules = [r for r in cfg["routing"]["rules"] if r.get("domain")]
flat = []
for r in dom_rules:
    flat.extend(r["domain"])
assert "geosite:category-ru" in flat
for d in DOMESTIC_BYPASS_DOMAINS:
    assert d in flat, d
assert "domain:ozon.ru" in flat and "domain:magnit.ru" in flat
assert "domain:wildberries.ru" in flat
assert "domain:wbstatic.net" in flat
assert "domain:5ka.ru" in flat and "domain:perekrestok.ru" in flat
assert "domain:vkusvill.ru" in flat and "domain:samokat.ru" in flat
assert "domain:sbermarket.ru" in flat and "domain:cdek.ru" in flat
assert "domain:yandex.go" not in flat
assert len(flat) >= 80

bal = build_happ_balancer_config(
    client_uuid="11111111-1111-1111-1111-111111111111",
    display_name="XENO",
    ru_host="ru.example",
    ru_port=443,
    ru_sni="dl.google.com",
    ru_pbk="pbk",
    ru_sid="sid",
    ru_path="/",
    backups_enabled=False,
    nl_host="nl.example",
    nl_direct_port=2053,
    direct_sni="",
    direct_pbk="",
    direct_sid="",
    direct_path="",
)
bal_dom = []
for r in bal["routing"]["rules"]:
    bal_dom.extend(r.get("domain") or [])
assert "domain:ozon.ru" in bal_dom and "domain:magnit.ru" in bal_dom

print("OK build_ru_config + build_vless_link XHTTP + flags + domestic bypass")
