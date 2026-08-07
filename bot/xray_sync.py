from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Literal, Mapping, Sequence
from urllib.parse import quote, urlencode

import paramiko

# File logs on RU (created by sync / deploy). Keep journald as fallback.
RU_ACCESS_LOG = "/var/log/xeno/ru-access.log"
RU_ERROR_LOG = "/var/log/xeno/ru-error.log"

NL_RELAY_CONFIG = "/usr/local/etc/xray/xeno-relay.json"
HY2_CONFIG = "/etc/hysteria/config.yaml"

XRAY_BIN = "/usr/local/bin/xray"
XRAY_API_SERVER = "127.0.0.1:8080"
RU_CLIENT_INBOUND = "client-in"
NL_DIRECT_INBOUND = "xeno-direct-in"

# Extra domestic bypass beyond geosite:category-ru / geoip:ru.
# Shop / grocery / delivery apps often hit CDN / API hosts outside geoip:ru;
# those would otherwise exit via NL hop and look like a foreign VPN.
# Same for RU geo-locked media (.com film portals hosted in RU but not in geosite).
# domain: matches the apex and all subdomains. Curated from v2fly domain-list
# (ozon, wildberries, x5, magnit, …) + well-known RU retail/delivery brands
# + a tight media list (not mirror farms). Legal streaming mass-cover is
# geosite:category-entertainment-ru (also nested under category-ru).
# Best-effort — lists drift; banks/gosuslugi already covered by geoip:ru.
# Not a full geosite dump (no typosquatting). TUN-level VPN detect is out of scope.
DOMESTIC_BYPASS_DOMAINS: list[str] = [
    # --- Marketplaces ---
    "domain:ozon.ru",
    "domain:ozon.com",
    "domain:ozon.app",
    "domain:ozon.tech",
    "domain:ozon.express",
    "domain:o3.ru",
    "domain:o3t.ru",
    "domain:ozone.ru",
    "domain:ozonusercontent.com",
    "domain:ozonru.me",
    "domain:ozonru.com",
    "domain:ozoncard.ru",
    "domain:ozon-dostavka.ru",
    "domain:o-courier.ru",
    "domain:ocourier.ru",
    "domain:wildberries.ru",
    "domain:wb.ru",
    "domain:wbstatic.net",
    "domain:wbstatic.ru",
    "domain:wbbasket.ru",
    "domain:wb-basket.ru",
    "domain:wbcontent.net",
    "domain:geobasket.ru",
    "domain:paywb.com",
    "domain:paywb.ru",
    "domain:wbpay.ru",
    "domain:wb-bank.ru",
    "domain:wb-cloud.ru",
    "domain:avito.ru",
    "domain:avito.st",
    "domain:youla.ru",
    "domain:lamoda.ru",
    "domain:lamoda.tech",
    # --- Grocery / retail chains ---
    "domain:magnit.ru",
    "domain:magnit.com",
    "domain:magnit-info.ru",
    "domain:magnit.online",
    "domain:mm.ru",
    "domain:kazanexpress.ru",
    "domain:x5.ru",
    "domain:x5.com",
    "domain:x5.tech",
    "domain:x5static.net",
    "domain:x5club.ru",
    "domain:x5id.ru",
    "domain:5ka.ru",
    "domain:perekrestok.ru",
    "domain:perekrestok.com",
    "domain:chizhik.club",
    "domain:fivepost.ru",
    "domain:5post.market",
    "domain:okolo.app",
    "domain:myapelsin.ru",
    "domain:mnogolososya.ru",
    "domain:vkusvill.ru",
    "domain:vkusvill.com",
    "domain:lenta.com",
    "domain:lenta.ru",
    "domain:lentochka.ru",
    "domain:samokat.ru",
    "domain:samokat.io",
    "domain:sbermarket.ru",
    "domain:kuper.ru",
    "domain:instamart.ru",
    "domain:auchan.ru",
    "domain:metro-cc.ru",
    "domain:okeydostavka.ru",
    "domain:okmarket.ru",
    "domain:globus.ru",
    "domain:dixy.ru",
    "domain:bristol.ru",
    "domain:fixprice.ru",
    "domain:ulichnaya.ru",
    # --- Delivery / last mile ---
    "domain:delivery-club.ru",
    "domain:deliveryclub.ru",
    "domain:yandexgo.com",
    "domain:yango.com",
    "domain:yango.taxi",
    "domain:yastatic.net",
    "domain:yastat.net",
    "domain:clstorage.net",
    "domain:turbopages.org",
    "domain:cdek.ru",
    "domain:cdek.shopping",
    "domain:boxberry.ru",
    "domain:pochta.ru",
    "domain:russianpost.ru",
    "domain:dpd.ru",
    "domain:dellin.ru",
    "domain:pecom.ru",
    "domain:hermesrussia.ru",
    "domain:pickpoint.ru",
    "domain:flowwow.com",
    "domain:flowwow-images.com",
    # --- Electronics / specialty retail ---
    "domain:mvideo.ru",
    "domain:eldorado.ru",
    "domain:citilink.ru",
    "domain:dns-shop.ru",
    "domain:technopark.ru",
    "domain:holodilnik.ru",
    "domain:detmir.ru",
    "domain:sportmaster.ru",
    "domain:goldapple.ru",
    "domain:rendez-vous.ru",
    "domain:respect-shoes.ru",
    "domain:letu.ru",
    "domain:podrygka.ru",
    "domain:apteka.ru",
    "domain:eapteka.ru",
    "domain:zdravcity.ru",
    "domain:utkonos.ru",
    "domain:petrovich.ru",
    "domain:leroy.ru",
    "domain:lemanapro.ru",
    "domain:obi.ru",
    "domain:hoff.ru",
    # --- Media / film / series (RU geo) ---
    # Legal majors: geosite:category-entertainment-ru (ivi/okko/kinopoisk/…).
    # Explicit .com portals that geo/VPN-block NL exit; cert SANs = apex+www only.
    "domain:bumazhniy-dom.com",
    "domain:kinorium.com",
    "domain:more.tv",
    "domain:megogo.net",
    "domain:megogo.ru",
    "domain:premier.one",
    "domain:ivicdn.tv",
    "domain:cdnvideohub.com",
    "domain:kinescopecdn.net",
    "domain:trbcdn.net",
]

SyncAction = Literal["skip", "hot_add", "restart"]


def domestic_bypass_routing_rules() -> list[dict]:
    """Field rules: private + RU geosite/geoip + shop/media CDN extras → direct."""
    return [
        {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"},
        {
            "type": "field",
            "domain": [
                "geosite:category-ru",
                # Explicit: legal RU VOD/TV (also nested under category-ru on current
                # geosite.dat; keep tagged so older dat / partial builds still match).
                "geosite:category-entertainment-ru",
            ],
            "outboundTag": "direct",
        },
        {"type": "field", "domain": list(DOMESTIC_BYPASS_DOMAINS), "outboundTag": "direct"},
        {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"},
    ]


def client_email(
    uuid: str,
    *,
    tg_id: int | None = None,
    issued_id: int | None = None,
    slot: int = 1,
) -> str:
    """Stable Xray email for access-log correlation.

    Slot 1 (primary) stays ``tg-{id}`` forever so existing digests/panels keep matching.
    Slot 2+ uses ``tg-{id}-{slot}`` to avoid inbound email collisions.
    """
    if tg_id is not None:
        if int(slot or 1) <= 1:
            return f"tg-{tg_id}"
        return f"tg-{tg_id}-{int(slot)}"
    if issued_id is not None:
        return f"issued-{issued_id}-{uuid[:8]}"
    return f"xeno-{uuid[:8]}"


# ISO 3166-1 alpha-2 → regional-indicator flag (Happ / commercial VPN style).
COUNTRY_FLAGS: dict[str, str] = {
    "RU": "🇷🇺",
    "NL": "🇳🇱",
}


def flagged_profile_name(label: str, country: str) -> str:
    """Prefix profile remark with country flag emoji (no space — commercial style)."""
    flag = COUNTRY_FLAGS.get((country or "").upper().strip())
    if not flag:
        return label
    # Strip a prior flag so rebuilds stay idempotent.
    for f in COUNTRY_FLAGS.values():
        if label.startswith(f):
            label = label[len(f) :].lstrip()
            break
    return f"{flag}{label}"


def build_vless_link(
    *,
    client_uuid: str,
    address: str,
    port: int,
    sni: str,
    pbk: str,
    sid: str,
    name: str = "XENO",
    path: str = "/xeno",
    mode: str = "stream-one",
    fingerprint: str = "chrome",
) -> str:
    """Client entry: VLESS + Reality + XHTTP (no Vision).

    ``mode=stream-one`` — explicit. ``auto`` breaks on Happ / some Xray builds
    with Reality (unexpected response version / silent no-payload after TLS).
    """
    q = urlencode(
        {
            "encryption": "none",
            "security": "reality",
            "sni": sni,
            "fp": fingerprint,
            "pbk": pbk,
            "sid": sid,
            "type": "xhttp",
            "path": path,
            "mode": mode,
        }
    )
    return f"vless://{client_uuid}@{address}:{port}?{q}#{quote(name)}"


def build_hysteria2_link(
    *,
    client_uuid: str,
    address: str,
    port: int,
    sni: str,
    name: str = "XENO HY2",
) -> str:
    user = f"xeno-{client_uuid[:8]}"
    q = urlencode({"sni": sni, "insecure": "0"})
    # Keep hyphens in userinfo unencoded (UUID / xeno-xxxxxxxx)
    return f"hysteria2://{user}:{client_uuid}@{address}:{port}/?{q}#{quote(name)}"


def reality_server_names(bridge_sni: str) -> list[str]:
    """Expand SNI list for common Google/Amazon-style Reality donors."""
    sni = (bridge_sni or "").strip()
    if sni in ("dl.google.com", "google.com", "www.google.com") or sni.endswith(".google.com"):
        return [
            "dl.google.com",
            "google.com",
            "www.google.com",
            "android.com",
            "g.co",
            "goo.gl",
            "www.goo.gl",
            "youtu.be",
            "youtube.com",
            "android.clients.google.com",
        ]
    if "amazon." in sni or sni.endswith("amazon.com"):
        return [
            "www.amazon.com",
            "amazon.com",
            "us.amazon.com",
            "www.m.amazon.com",
            "amzn.com",
        ]
    names = [sni] if sni else []
    if sni == "www.cloudflare.com" and "cloudflare.com" not in names:
        names.append("cloudflare.com")
    return names or ["dl.google.com"]


def build_profile_links(
    *,
    client_uuid: str,
    name: str,
    ru_host: str,
    ru_port: int,
    ru_sni: str,
    ru_pbk: str,
    ru_sid: str,
    ru_path: str,
    xhttp_mode: str,
    backups_enabled: bool,
    nl_host: str,
    nl_direct_port: int,
    direct_sni: str,
    direct_pbk: str,
    direct_sid: str,
    direct_path: str,
    hy2_port: int,
    fingerprint: str = "randomized",
) -> list[str]:
    """RU primary, then NL Direct, then HY2 backup.

    Remarks match advertised hostname/label geography (RU entry / NL Direct),
    with commercial-style country flag prefixes for Happ lists.
    """
    links = [
        build_vless_link(
            client_uuid=client_uuid,
            address=ru_host,
            port=ru_port,
            sni=ru_sni,
            pbk=ru_pbk,
            sid=ru_sid,
            name=flagged_profile_name(f"{name} RU", "RU"),
            path=ru_path,
            mode=xhttp_mode,
            fingerprint=fingerprint,
        )
    ]
    if not backups_enabled:
        return links
    if direct_pbk and direct_sid and direct_path:
        links.append(
            build_vless_link(
                client_uuid=client_uuid,
                address=nl_host,
                port=nl_direct_port,
                sni=direct_sni,
                pbk=direct_pbk,
                sid=direct_sid,
                name=flagged_profile_name(f"{name} NL Direct", "NL"),
                path=direct_path,
                mode=xhttp_mode,
                fingerprint=fingerprint,
            )
        )
    if hy2_port > 0:
        links.append(
            build_hysteria2_link(
                client_uuid=client_uuid,
                address=nl_host,
                port=hy2_port,
                sni=nl_host,
                name=flagged_profile_name(f"{name} HY2", "NL"),
            )
        )
    return links


def build_vless_reality_xhttp_outbound(
    *,
    tag: str,
    client_uuid: str,
    address: str,
    port: int,
    sni: str,
    pbk: str,
    sid: str,
    path: str,
    mode: str = "stream-one",
    fingerprint: str = "chrome",
) -> dict:
    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": address,
                    "port": int(port),
                    "users": [{"id": client_uuid, "encryption": "none"}],
                }
            ]
        },
        "streamSettings": {
            "network": "xhttp",
            "security": "reality",
            "xhttpSettings": {"path": path or "/", "mode": mode or "stream-one"},
            "realitySettings": {
                "serverName": sni,
                "fingerprint": fingerprint,
                "publicKey": pbk,
                "shortId": sid,
            },
        },
    }


def build_happ_balancer_config(
    *,
    client_uuid: str,
    display_name: str = "XENO",
    ru_host: str,
    ru_port: int,
    ru_sni: str,
    ru_pbk: str,
    ru_sid: str,
    ru_path: str,
    xhttp_mode: str = "stream-one",
    backups_enabled: bool,
    nl_host: str,
    nl_direct_port: int,
    direct_sni: str,
    direct_pbk: str,
    direct_sid: str,
    direct_path: str,
    fingerprint: str = "chrome",
) -> dict:
    """One Happ 'server': full Xray JSON with observatory + leastPing balancer.

    HY2 cannot live inside xray-core — kept as server-side backup only.
    Prefix selector matches tags XENO / XENO-NL.
    """
    outbounds: list[dict] = [
        build_vless_reality_xhttp_outbound(
            tag="XENO",
            client_uuid=client_uuid,
            address=ru_host,
            port=ru_port,
            sni=ru_sni,
            pbk=ru_pbk,
            sid=ru_sid,
            path=ru_path,
            mode=xhttp_mode,
            fingerprint=fingerprint,
        )
    ]
    proxy_tags = ["XENO"]
    if (
        backups_enabled
        and direct_pbk
        and direct_sid
        and direct_path is not None
        and nl_host
        and nl_direct_port
    ):
        outbounds.append(
            build_vless_reality_xhttp_outbound(
                tag="XENO-NL",
                client_uuid=client_uuid,
                address=nl_host,
                port=nl_direct_port,
                sni=direct_sni,
                pbk=direct_pbk,
                sid=direct_sid,
                path=direct_path,
                mode=xhttp_mode,
                fingerprint=fingerprint,
            )
        )
        proxy_tags.append("XENO-NL")

    outbounds.extend(
        [
            {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIPv4"}},
            {"tag": "block", "protocol": "blackhole"},
        ]
    )

    routing_rules: list[dict] = domestic_bypass_routing_rules()

    cfg: dict = {
        "remarks": display_name,
        "meta": {
            "serverDescription": "smart · auto failover",
        },
        "log": {"loglevel": "warning"},
        "dns": {
            "servers": ["1.1.1.1", "8.8.8.8"],
            "queryStrategy": "UseIPv4",
        },
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"udp": True},
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": True,
                },
            },
            {
                "tag": "http-in",
                "listen": "127.0.0.1",
                "port": 10809,
                "protocol": "http",
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": True,
                },
            },
        ],
        "outbounds": outbounds,
        "stats": {},
    }

    if len(proxy_tags) >= 2:
        routing_rules.append(
            {"type": "field", "network": "tcp,udp", "balancerTag": "xeno-balance"}
        )
        cfg["routing"] = {
            "domainStrategy": "IPIfNonMatch",
            "balancers": [
                {
                    "tag": "xeno-balance",
                    "selector": ["XENO"],
                    "fallbackTag": "XENO",
                    "strategy": {"type": "leastPing"},
                }
            ],
            "rules": routing_rules,
        }
        cfg["burstObservatory"] = {
            "subjectSelector": ["XENO"],
            "pingConfig": {
                "destination": "http://www.gstatic.com/generate_204",
                "connectivity": "http://connectivitycheck.gstatic.com/generate_204",
                "interval": "1m",
                "sampling": 3,
                "timeout": "5s",
            },
        }
    else:
        routing_rules.append(
            {"type": "field", "network": "tcp,udp", "outboundTag": "XENO"}
        )
        cfg["routing"] = {
            "domainStrategy": "IPIfNonMatch",
            "rules": routing_rules,
        }

    return cfg


def subscription_body_balancer(config: dict | list) -> str:
    """Raw JSON for Happ full-config detection (not base64 URI list)."""
    payload = config if isinstance(config, list) else [config]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def subscription_body(links: str | Sequence[str]) -> str:
    """Plain newline-separated URI list for Happ (iOS rejects opaque base64 as unknown type)."""
    if isinstance(links, str):
        text = links if links.endswith("\n") else links + "\n"
    else:
        text = "\n".join(links) + "\n"
    return text


def write_user_sub_file(
    sub_root: Path,
    token: str,
    links: str | Sequence[str] | None = None,
    *,
    balancer_config: dict | None = None,
) -> Path:
    """Write Happ subscription (plain URI list or balancer JSON)."""
    d = sub_root / token
    d.mkdir(parents=True, exist_ok=True)
    if balancer_config is not None:
        body = subscription_body_balancer(balancer_config)
    elif links is not None:
        body = subscription_body(links)
    else:
        raise ValueError("links or balancer_config required")
    path = d / "sub.txt"
    path.write_text(body, encoding="utf-8")
    (d / "index.txt").write_text(body, encoding="utf-8")
    (d / "index.html").write_text(body, encoding="utf-8")
    return path


def build_ru_config(
    client_uuids: Iterable[str],
    *,
    bridge_private_key: str,
    bridge_short_id: str,
    bridge_dest: str,
    bridge_sni: str,
    bridge_path: str,
    nl_exit_ip: str,
    relay_uuid: str,
    relay_public_key: str,
    relay_short_id: str,
    relay_sni: str,
    relay_path: str,
    relay_port: int = 8443,
    client_port: int = 443,
    client_emails: Mapping[str, str] | None = None,
    loglevel: str = "warning",
) -> dict:
    """RU bridge: client XHTTP+Reality inbound + hop XHTTP+Reality to NL. No Vision."""
    seen: list[str] = []
    for u in client_uuids:
        if u and u not in seen:
            seen.append(u)
    emails = client_emails or {}
    clients = [
        {
            "id": u,
            "email": emails.get(u) or client_email(u),
        }
        for u in seen
    ]
    if not clients:
        clients = [
            {
                "id": "00000000-0000-0000-0000-000000000000",
                "email": "placeholder",
            }
        ]

    server_names = reality_server_names(bridge_sni)

    return {
        "api": {"tag": "api", "services": ["HandlerService", "StatsService"]},
        "stats": {},
        "policy": {
            "levels": {
                "0": {
                    "statsUserUplink": True,
                    "statsUserDownlink": True,
                }
            },
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
                "statsOutboundUplink": True,
                "statsOutboundDownlink": True,
            },
        },
        "log": {
            "loglevel": loglevel,
            "access": RU_ACCESS_LOG,
            "error": RU_ERROR_LOG,
        },
        "inbounds": [
            {
                "tag": "api",
                "listen": "127.0.0.1",
                "port": 8080,
                "protocol": "dokodemo-door",
                "settings": {"address": "127.0.0.1"},
            },
            {
                "tag": "client-in",
                "listen": "0.0.0.0",
                "port": client_port,
                "protocol": "vless",
                "settings": {"clients": clients, "decryption": "none"},
                "streamSettings": {
                    "network": "xhttp",
                    "security": "reality",
                    "xhttpSettings": {
                        "path": bridge_path,
                        "mode": "stream-one",
                        "xPaddingBytes": "100-1000",
                    },
                    "realitySettings": {
                        "show": False,
                        "dest": bridge_dest,
                        "xver": 0,
                        "serverNames": server_names,
                        "privateKey": bridge_private_key,
                        "shortIds": [bridge_short_id],
                    },
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": True,
                },
            }
        ],
        "outbounds": [
            {"tag": "api", "protocol": "freedom", "settings": {}},
            {
                "tag": "nl-exit",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": nl_exit_ip,
                            "port": relay_port,
                            "users": [
                                {
                                    "id": relay_uuid,
                                    "encryption": "none",
                                }
                            ],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "reality",
                    "xhttpSettings": {"path": relay_path, "mode": "stream-one"},
                    "realitySettings": {
                        "serverName": relay_sni,
                        "fingerprint": "chrome",
                        "publicKey": relay_public_key,
                        "shortId": relay_short_id,
                    },
                },
            },
            {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIPv4"}},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                *domestic_bypass_routing_rules(),
                {"type": "field", "network": "tcp,udp", "outboundTag": "nl-exit"},
            ],
        },
    }


def _ensure_api_block(cfg: dict) -> dict:
    """Ensure HandlerService + StatsService, local API inbound, and user traffic policy."""
    out = copy.deepcopy(cfg)
    services = set(out.get("api", {}).get("services") or [])
    services.update(["HandlerService", "StatsService"])
    out["api"] = {"tag": "api", "services": sorted(services)}
    out.setdefault("stats", {})
    policy = out.setdefault("policy", {})
    levels = policy.setdefault("levels", {})
    lvl0 = levels.setdefault("0", {})
    lvl0["statsUserUplink"] = True
    lvl0["statsUserDownlink"] = True
    system = policy.setdefault("system", {})
    system.setdefault("statsInboundUplink", True)
    system.setdefault("statsInboundDownlink", True)
    system.setdefault("statsOutboundUplink", True)
    system.setdefault("statsOutboundDownlink", True)

    inbounds = out.setdefault("inbounds", [])
    if not any(i.get("tag") == "api" for i in inbounds):
        inbounds.insert(
            0,
            {
                "tag": "api",
                "listen": "127.0.0.1",
                "port": 8080,
                "protocol": "dokodemo-door",
                "settings": {"address": "127.0.0.1"},
            },
        )

    outbounds = out.setdefault("outbounds", [])
    if not any(o.get("tag") == "api" for o in outbounds):
        outbounds.insert(0, {"tag": "api", "protocol": "freedom", "settings": {}})

    routing = out.setdefault("routing", {})
    rules = list(routing.get("rules") or [])
    has_api_rule = any(
        "api" in (r.get("inboundTag") or []) and r.get("outboundTag") == "api" for r in rules
    )
    if not has_api_rule:
        rules.insert(0, {"type": "field", "inboundTag": ["api"], "outboundTag": "api"})
    routing["rules"] = rules
    return out


def _canonical_json(cfg: dict) -> str:
    return json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _config_hash(cfg: dict) -> str:
    return hashlib.sha256(_canonical_json(cfg).encode()).hexdigest()


def _client_uuids_in_inbound(cfg: dict, inbound_tag: str) -> set[str]:
    for inbound in cfg.get("inbounds", []):
        if inbound.get("tag") == inbound_tag:
            return {
                c["id"]
                for c in inbound.get("settings", {}).get("clients", [])
                if c.get("id") and not c["id"].startswith("00000000-")
            }
    return set()


def _clients_for_inbound(cfg: dict, inbound_tag: str) -> list[dict]:
    for inbound in cfg.get("inbounds", []):
        if inbound.get("tag") == inbound_tag:
            return list(inbound.get("settings", {}).get("clients", []))
    return []


def _structural_cfg(cfg: dict, inbound_tag: str) -> dict:
    """Same config but inbound clients replaced — detect non-client edits."""
    out = copy.deepcopy(_ensure_api_block(cfg))
    for inbound in out.get("inbounds", []):
        if inbound.get("tag") == inbound_tag:
            inbound.setdefault("settings", {})["clients"] = [
                {"id": "00000000-0000-0000-0000-000000000000", "email": "placeholder"}
            ]
    return out


def _sync_log(line: str) -> None:
    try:
        path = Path("/var/log/xeno/provision.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except OSError:
        pass


def _inbound_by_tag(cfg: dict, inbound_tag: str) -> dict | None:
    for inbound in cfg.get("inbounds", []):
        if inbound.get("tag") == inbound_tag:
            return inbound
    return None


def _adu_payload(cfg: dict, inbound_tag: str, clients: list[dict]) -> dict:
    """Full inbound detour required by Xray 26.x HandlerService adu (port/listen/stream)."""
    base = _inbound_by_tag(cfg, inbound_tag)
    if not base:
        raise RuntimeError(f"inbound {inbound_tag!r} missing for adu payload")
    inbound = copy.deepcopy(base)
    inbound.setdefault("settings", {})["clients"] = clients
    inbound.setdefault("protocol", "vless")
    inbound["tag"] = inbound_tag
    return {"inbounds": [inbound]}


def _hot_add_ok(stdout: str, stderr: str) -> bool:
    text = (stdout + stderr).lower()
    if "failed to build config" in text or "no port" in text:
        return False
    if "result: ok" in text or "already exists" in text:
        return True
    m = re.search(r"added (\d+) user", text)
    return bool(m and int(m.group(1)) > 0)


def _hot_add_users_local(cfg: dict, inbound_tag: str, new_clients: list[dict]) -> tuple[bool, str]:
    if not new_clients:
        return True, "empty"
    payload = _adu_payload(cfg, inbound_tag, new_clients)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        tmp = f.name
    try:
        r = subprocess.run(
            [XRAY_BIN, "api", "adu", f"--server={XRAY_API_SERVER}", tmp],
            capture_output=True,
            text=True,
            timeout=30,
        )
        detail = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
        ok = r.returncode == 0 and _hot_add_ok(r.stdout, r.stderr)
        return ok, detail[:500]
    finally:
        Path(tmp).unlink(missing_ok=True)


def _hot_add_users_remote(
    ssh: paramiko.SSHClient,
    cfg: dict,
    inbound_tag: str,
    new_clients: list[dict],
) -> tuple[bool, str]:
    if not new_clients:
        return True, "empty"
    payload = json.dumps(_adu_payload(cfg, inbound_tag, new_clients), ensure_ascii=False)
    remote_tmp = f"/tmp/xeno-adu-{inbound_tag}.json"
    sftp = ssh.open_sftp()
    try:
        with sftp.file(remote_tmp, "w") as f:
            f.write(payload)
    finally:
        sftp.close()
    cmd = (
        f"{XRAY_BIN} api adu --server={XRAY_API_SERVER} {remote_tmp}; "
        f"rc=$?; rm -f {remote_tmp}; exit $rc"
    )
    _i, o, e = ssh.exec_command(cmd, timeout=60)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    detail = (out + "\n" + err).strip()
    ok = code == 0 and _hot_add_ok(out, err)
    return ok, detail[:500]


def _restart_unit_local(unit: str) -> None:
    subprocess.run(
        ["systemctl", "restart", unit],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    subprocess.run(
        ["systemctl", "is-active", unit],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _restart_unit_remote(ssh: paramiko.SSHClient, unit: str) -> None:
    cmd = f"systemctl restart {unit} && sleep 1 && systemctl is-active {unit}"
    _i, o, e = ssh.exec_command(cmd, timeout=90)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if code != 0 or "active" not in out:
        raise RuntimeError(f"{unit} restart failed ({code}): {out.strip()} {err.strip()}")


def _runtime_ready(cfg: dict) -> bool:
    services = set((cfg.get("api") or {}).get("services") or [])
    if "HandlerService" not in services or "StatsService" not in services:
        return False
    if "stats" not in cfg:
        return False
    if not any(i.get("tag") == "api" for i in cfg.get("inbounds") or []):
        return False
    return True


def decide_sync_action(
    existing_cfg: dict | None,
    new_cfg: dict,
    inbound_tag: str,
) -> tuple[SyncAction, set[str]]:
    """Return action and UUIDs to hot-add (when action is hot_add)."""
    new_cfg = _ensure_api_block(new_cfg)
    if existing_cfg is None or not _runtime_ready(existing_cfg):
        return "restart", set()
    existing_norm = _ensure_api_block(existing_cfg)
    if _config_hash(existing_norm) == _config_hash(new_cfg):
        return "skip", set()
    old_uuids = _client_uuids_in_inbound(existing_norm, inbound_tag)
    new_uuids = _client_uuids_in_inbound(new_cfg, inbound_tag)
    if old_uuids - new_uuids:
        return "restart", set()
    if _config_hash(_structural_cfg(existing_norm, inbound_tag)) != _config_hash(
        _structural_cfg(new_cfg, inbound_tag)
    ):
        return "restart", set()
    added = new_uuids - old_uuids
    if added:
        return "hot_add", added
    return "skip", set()


def _write_config_atomic(path: Path, cfg: dict) -> None:
    payload = _canonical_json(cfg)
    tmp = path.with_suffix(path.suffix + ".xeno.tmp")
    tmp.write_text(payload, encoding="utf-8")
    subprocess.run(
        ["python3", "-m", "json.tool", str(tmp)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    tmp.replace(path)


def apply_local_inbound_sync(
    *,
    config_path: str,
    new_cfg: dict,
    inbound_tag: str,
    unit: str,
) -> SyncAction:
    path = Path(config_path)
    existing_cfg: dict | None = None
    if path.exists():
        existing_cfg = json.loads(path.read_text(encoding="utf-8"))
    action, added_uuids = decide_sync_action(existing_cfg, new_cfg, inbound_tag)
    if action == "skip":
        _sync_log(f"nl sync: action=skip tag={inbound_tag}")
        return "skip"
    _write_config_atomic(path, new_cfg)
    if action == "hot_add":
        new_clients = [
            c for c in _clients_for_inbound(new_cfg, inbound_tag) if c.get("id") in added_uuids
        ]
        ok, detail = _hot_add_users_local(new_cfg, inbound_tag, new_clients)
        if ok:
            _sync_log(
                f"nl sync: action=hot_add tag={inbound_tag} added={len(new_clients)} detail={detail[:120]}"
            )
            return "hot_add"
        _sync_log(
            f"nl sync: restart reason=adu_failed tag={inbound_tag} added={len(new_clients)} detail={detail}"
        )
    else:
        _sync_log(f"nl sync: action=restart tag={inbound_tag}")
    _restart_unit_local(unit)
    return "restart"


def sync_xray_clients_remote(
    *,
    ru_host: str,
    ru_user: str,
    ru_password: str,
    remote_config_path: str,
    client_uuids: Iterable[str],
    bridge_private_key: str,
    bridge_short_id: str,
    bridge_dest: str,
    bridge_sni: str,
    bridge_path: str,
    nl_exit_ip: str,
    relay_uuid: str,
    relay_public_key: str,
    relay_short_id: str,
    relay_sni: str,
    relay_path: str,
    relay_port: int = 8443,
    client_port: int = 443,
    client_emails: Mapping[str, str] | None = None,
) -> None:
    """Push RU Xray config over SSH. Never touches NL 3x-ui / trading bot."""
    if not bridge_private_key or not relay_public_key or not relay_uuid or not bridge_path:
        raise RuntimeError("incomplete dual-hop Reality settings")

    cfg = build_ru_config(
        client_uuids,
        bridge_private_key=bridge_private_key,
        bridge_short_id=bridge_short_id,
        bridge_dest=bridge_dest,
        bridge_sni=bridge_sni,
        bridge_path=bridge_path,
        nl_exit_ip=nl_exit_ip,
        relay_uuid=relay_uuid,
        relay_public_key=relay_public_key,
        relay_short_id=relay_short_id,
        relay_sni=relay_sni,
        relay_path=relay_path,
        relay_port=relay_port,
        client_port=client_port,
        client_emails=client_emails,
    )
    cfg = _ensure_api_block(cfg)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        ru_host,
        username=ru_user,
        password=ru_password,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    try:
        client.exec_command(
            "mkdir -p /var/log/xeno && touch /var/log/xeno/ru-access.log /var/log/xeno/ru-error.log "
            "&& chmod 755 /var/log/xeno && chmod 644 /var/log/xeno/ru-*.log",
            timeout=30,
        )
        existing_cfg: dict | None = None
        try:
            sftp = client.open_sftp()
            with sftp.file(remote_config_path, "r") as f:
                existing_cfg = json.loads(f.read().decode("utf-8"))
            sftp.close()
        except OSError:
            pass

        action, added_uuids = decide_sync_action(existing_cfg, cfg, RU_CLIENT_INBOUND)
        if action == "skip":
            _sync_log("ru sync: action=skip")
            return

        tmp = remote_config_path + ".xeno.tmp"
        payload = _canonical_json(cfg)
        sftp = client.open_sftp()
        with sftp.file(tmp, "w") as f:
            f.write(payload)
        sftp.close()

        if action == "restart":
            cmd = (
                f"mv -f {tmp} {remote_config_path} && python3 -m json.tool {remote_config_path} >/dev/null "
                f"&& systemctl restart xray && sleep 1 && systemctl is-active xray"
            )
            _i, o, e = client.exec_command(cmd, timeout=90)
            out = o.read().decode("utf-8", "replace")
            err = e.read().decode("utf-8", "replace")
            code = o.channel.recv_exit_status()
            if code != 0 or "active" not in out:
                raise RuntimeError(f"RU xray sync failed ({code}): {out.strip()} {err.strip()}")
            _sync_log("ru sync: action=restart")
            return

        # hot_add: persist config, then add users via API (no process restart)
        _i, o, e = client.exec_command(
            f"mv -f {tmp} {remote_config_path} && python3 -m json.tool {remote_config_path} >/dev/null",
            timeout=60,
        )
        err = e.read().decode("utf-8", "replace")
        code = o.channel.recv_exit_status()
        if code != 0:
            raise RuntimeError(f"RU xray config write failed ({code}): {err.strip()}")

        new_clients = [
            c for c in _clients_for_inbound(cfg, RU_CLIENT_INBOUND) if c.get("id") in added_uuids
        ]
        ok, detail = _hot_add_users_remote(client, cfg, RU_CLIENT_INBOUND, new_clients)
        if ok:
            _sync_log(f"ru sync: action=hot_add added={len(new_clients)} detail={detail[:120]}")
            return
        _sync_log(f"ru sync: restart reason=adu_failed added={len(new_clients)} detail={detail}")
        _restart_unit_remote(client, "xray")
    finally:
        client.close()


def _unique_uuids(client_uuids: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for u in client_uuids:
        if u and u not in seen:
            seen.append(u)
    return seen


def sync_nl_direct_clients_local(
    client_uuids: Iterable[str],
    *,
    config_path: str = NL_RELAY_CONFIG,
    client_emails: Mapping[str, str] | None = None,
) -> None:
    """Patch xeno-direct-in clients on local NL coexist config. Leaves hop inbound intact."""
    path = Path(config_path)
    if not path.exists():
        raise RuntimeError(f"NL relay config missing: {config_path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    emails = client_emails or {}
    clients = [
        {"id": u, "email": emails.get(u) or client_email(u)}
        for u in _unique_uuids(client_uuids)
    ]
    if not clients:
        clients = [{"id": "00000000-0000-0000-0000-000000000000", "email": "placeholder"}]
    found = False
    for inbound in cfg.get("inbounds", []):
        if inbound.get("tag") == "xeno-direct-in":
            inbound.setdefault("settings", {})["clients"] = clients
            ss = inbound.setdefault("streamSettings", {})
            xh = ss.setdefault("xhttpSettings", {})
            xh["mode"] = "stream-one"
            if not xh.get("path"):
                xh["path"] = "/"
            found = True
            break
    if not found:
        raise RuntimeError("xeno-direct-in inbound missing — run scripts/deploy_backups.py first")
    cfg = _ensure_api_block(cfg)
    apply_local_inbound_sync(
        config_path=config_path,
        new_cfg=cfg,
        inbound_tag=NL_DIRECT_INBOUND,
        unit="xeno-relay",
    )


def sync_hy2_users_local(
    client_uuids: Iterable[str],
    *,
    config_path: str = HY2_CONFIG,
) -> None:
    """Rewrite Hysteria2 userpass map from active UUIDs."""
    path = Path(config_path)
    if not path.exists():
        raise RuntimeError(f"HY2 config missing: {config_path}")
    text = path.read_text(encoding="utf-8")
    userpass = {f"xeno-{u[:8]}": u for u in _unique_uuids(client_uuids)}
    if not userpass:
        userpass = {"xeno-placeholder": "00000000-0000-0000-0000-000000000000"}
    block = "  userpass:\n" + "".join(f"    {k}: {v}\n" for k, v in userpass.items())
    if re.search(r"(?m)^  userpass:\n(?:    .+\n)*", text):
        text = re.sub(r"(?m)^  userpass:\n(?:    .+\n)*", block, text, count=1)
    else:
        raise RuntimeError("HY2 config has no userpass block")
    new_text = text if text.endswith("\n") else text + "\n"
    if path.read_text(encoding="utf-8") == new_text:
        return
    path.write_text(new_text, encoding="utf-8")
    _restart_unit_local("xeno-hy2")
