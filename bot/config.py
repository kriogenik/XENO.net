from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DonateWallet:
    """Public donation address (coin label + address)."""

    coin: str
    address: str


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))


def load_env_files() -> None:
    for p in (
        Path("/etc/runaway/xeno.net/config/bot.env"),
        Path("/etc/runaway/xeno.net/config/bridge.env"),
        Path("/etc/runaway/xeno.net/config/ru-ssh.env"),
        Path("/etc/xeno/bot.env"),
        Path("/etc/xeno/bridge.env"),
        Path("/etc/xeno/ru-ssh.env"),
        Path(__file__).resolve().parents[1] / "secrets" / "bot.env",
        Path(__file__).resolve().parents[1] / "secrets" / "bridge.env",
        Path(__file__).resolve().parents[1] / "secrets" / "reality.env",
        Path(__file__).resolve().parents[1] / "secrets" / "uuids.env",
        Path(__file__).resolve().parents[1] / "secrets" / "ru-access.env",
        Path(__file__).resolve().parents[1] / "inventory" / "hosts.env",
    ):
        _load_env_file(p)


def _load_donate_wallets() -> tuple[DonateWallet, ...]:
    """Optional public wallets from env — empty tuple hides nothing critical."""
    specs = (
        ("DONATE_USDT_TRC20", "USDT · TRC20"),
        ("DONATE_TON", "TON"),
        ("DONATE_BTC", "BTC"),
    )
    wallets: list[DonateWallet] = []
    for key, label in specs:
        addr = os.environ.get(key, "").strip()
        if addr:
            wallets.append(DonateWallet(coin=label, address=addr))
    return tuple(wallets)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: Path
    sub_root: Path
    sub_port: int
    sub_public_ip: str
    # Public base for Happ import links, e.g. https://nl.example.com:2080 (no trailing slash).
    # Always https — Happ (iOS/Android/desktop) must not get cleartext http:// sub URLs.
    sub_public_base: str
    sub_tls_cert: str
    sub_tls_key: str
    ru_public_host: str
    ru_public_ip: str
    client_port: int
    reality_sni: str
    reality_pbk: str
    reality_sid: str
    reality_dest: str
    reality_private_key: str
    bridge_path: str
    xhttp_mode: str
    nl_exit_ip: str
    relay_uuid: str
    relay_port: int
    relay_public_key: str
    relay_short_id: str
    relay_sni: str
    relay_path: str
    demo_days: int
    admin_ids: frozenset[int]
    bootstrap_client_uuid: str
    ru_ssh_host: str
    ru_ssh_user: str
    ru_ssh_pass: str
    ru_xray_config: str
    # Optional: NL panel metrics only (never used to provision sacred/foreign inbounds)
    xui_base_url: str
    xui_api_token: str
    xui_inbound_id: int
    # NL public backups (Direct XHTTP + Hysteria2); optional until deploy_backups
    backups_enabled: bool
    nl_public_host: str
    nl_direct_port: int
    direct_path: str
    direct_pbk: str
    direct_sid: str
    direct_sni: str
    hy2_port: int
    hy2_enabled: bool
    sub_format: str  # "links" (Happ URI+autoconnect) | "balancer" (full JSON)
    nl_relay_config: str
    hy2_config: str
    reality_client_fp: str
    canary_client_uuid: str
    # Support DM bridge («Диалог»)
    support_enabled: bool
    support_rate_limit: int
    support_rate_window_sec: int
    support_reopen_cooldown_sec: int
    support_idle_close_sec: int
    donate_wallets: tuple[DonateWallet, ...]


def load_settings(*, require_token: bool = True) -> Settings:
    load_env_files()
    token = os.environ.get("BOT_TOKEN", "").strip()
    if require_token and not token:
        raise RuntimeError("BOT_TOKEN is not set")

    admins_raw = os.environ.get("ADMIN_IDS", "")
    admin_ids = frozenset(int(x) for x in admins_raw.split(",") if x.strip().isdigit())
    if not admin_ids and require_token:
        # Без ADMIN_IDS godmode/метрики недоступны — это нормально для публичного шаблона.
        pass

    ru_ip = os.environ.get("RU_PUBLIC_IP") or os.environ.get("RU_BRIDGE_IP") or ""
    ru_host = os.environ.get("RU_DOMAIN") or ru_ip
    nl_ip = os.environ.get("NL_EXIT_IP") or os.environ.get("NL_PUBLIC_IP") or ""
    sub_ip = os.environ.get("SUB_PUBLIC_IP") or nl_ip
    sub_port = int(os.environ.get("SUB_PORT", "2080"))
    nl_public_host = (
        os.environ.get("NL_PUBLIC_HOST")
        or os.environ.get("NL_DOMAIN")
        or nl_ip
    )
    # Always HTTPS for subscription links (any client OS). Prefer SUB_PUBLIC_BASE;
    # else NL domain; last resort IP (cert may not match — set SUB_PUBLIC_BASE).
    sub_public_base = (os.environ.get("SUB_PUBLIC_BASE") or "").strip().rstrip("/")
    sub_tls_cert = (os.environ.get("SUB_TLS_CERT") or "").strip()
    sub_tls_key = (os.environ.get("SUB_TLS_KEY") or "").strip()
    if sub_public_base.startswith("http://"):
        sub_public_base = "https://" + sub_public_base[len("http://") :]
    if not sub_public_base:
        host = nl_public_host or sub_ip or nl_ip
        if host:
            sub_public_base = f"https://{host}:{sub_port}"
    if sub_public_base and not sub_public_base.startswith("https://"):
        sub_public_base = "https://" + sub_public_base.lstrip("/")

    bridge_priv = os.environ.get("BRIDGE_REALITY_PRIVATE_KEY") or os.environ.get("REALITY_PRIVATE_KEY", "")
    bridge_pbk = os.environ.get("BRIDGE_REALITY_PUBLIC_KEY") or os.environ.get("REALITY_PUBLIC_KEY", "")
    bridge_sid = os.environ.get("BRIDGE_REALITY_SHORT_ID") or os.environ.get("REALITY_SHORT_ID", "")
    bridge_sni = (
        os.environ.get("BRIDGE_REALITY_SNI")
        or os.environ.get("REALITY_SNI", "www.cloudflare.com")
    )
    bridge_dest = (
        os.environ.get("BRIDGE_REALITY_DEST")
        or os.environ.get("REALITY_DEST", f"{bridge_sni}:443")
    )
    bridge_path = os.environ.get("BRIDGE_PATH") or os.environ.get("XHTTP_PATH", "/xeno")
    mode = os.environ.get("XHTTP_MODE", "auto")

    relay_uuid = os.environ.get("RELAY_UUID", "")
    relay_pbk = os.environ.get("RELAY_REALITY_PUBLIC_KEY", "")
    relay_sid = os.environ.get("RELAY_REALITY_SHORT_ID", "")
    relay_sni = os.environ.get("RELAY_REALITY_SNI") or bridge_sni
    relay_path = os.environ.get("RELAY_PATH", "")

    ru_pass = os.environ.get("RU_SSH_PASS", "").strip().strip("'")

    if require_token:
        if not ru_ip or not nl_ip:
            raise RuntimeError("RU_BRIDGE_IP and NL_EXIT_IP required")
        if not bridge_pbk or not bridge_sid or not bridge_priv or not relay_uuid:
            raise RuntimeError("Bridge Reality/relay UUID incomplete")
        if not relay_pbk or not relay_sid or not relay_path or not bridge_path:
            raise RuntimeError("Dual-hop RELAY_* / BRIDGE_PATH required")
        if not ru_pass:
            raise RuntimeError("RU_SSH_PASS required for NL→RU Xray sync")

    return Settings(
        bot_token=token,
        db_path=Path(os.environ.get("DB_PATH", "/etc/runaway/xeno.net/data/bot.db")),
        sub_root=Path(os.environ.get("SUB_ROOT", "/etc/runaway/xeno.net/www/sub")),
        sub_port=sub_port,
        sub_public_ip=sub_ip or nl_ip,
        sub_public_base=sub_public_base,
        sub_tls_cert=sub_tls_cert,
        sub_tls_key=sub_tls_key,
        ru_public_host=ru_host,
        ru_public_ip=ru_ip,
        client_port=int(os.environ.get("CLIENT_PORT", "443")),
        reality_sni=bridge_sni,
        reality_pbk=bridge_pbk,
        reality_sid=bridge_sid,
        reality_dest=bridge_dest,
        reality_private_key=bridge_priv,
        bridge_path=bridge_path,
        xhttp_mode=mode,
        nl_exit_ip=nl_ip,
        relay_uuid=relay_uuid,
        relay_port=int(os.environ.get("RELAY_PORT", "8443")),
        relay_public_key=relay_pbk,
        relay_short_id=relay_sid,
        relay_sni=relay_sni,
        relay_path=relay_path,
        demo_days=int(os.environ.get("DEMO_DAYS", "30")),
        admin_ids=admin_ids,
        bootstrap_client_uuid=os.environ.get("BOOTSTRAP_CLIENT_UUID")
        or os.environ.get("CLIENT_UUID", "").strip(),
        ru_ssh_host=os.environ.get("RU_SSH_HOST") or os.environ.get("RU_BRIDGE_IP") or ru_ip,
        ru_ssh_user=os.environ.get("RU_SSH_USER", "root"),
        ru_ssh_pass=ru_pass,
        ru_xray_config=os.environ.get("RU_XRAY_CONFIG", "/usr/local/etc/xray/config.json"),
        xui_base_url=os.environ.get("XUI_BASE_URL", "").rstrip("/"),
        xui_api_token=os.environ.get("XUI_API_TOKEN", "").strip(),
        xui_inbound_id=int(os.environ.get("XUI_INBOUND_ID", "0") or "0"),
        backups_enabled=os.environ.get("BACKUPS_ENABLED", "0").strip() in ("1", "true", "yes"),
        nl_public_host=nl_public_host or nl_ip,
        nl_direct_port=int(os.environ.get("NL_DIRECT_PORT", "2053")),
        direct_path=os.environ.get("DIRECT_PATH", ""),
        direct_pbk=os.environ.get("DIRECT_REALITY_PUBLIC_KEY", ""),
        direct_sid=os.environ.get("DIRECT_REALITY_SHORT_ID", ""),
        direct_sni=os.environ.get("DIRECT_REALITY_SNI")
        or os.environ.get("NL_DOMAIN")
        or nl_ip,
        hy2_port=int(os.environ.get("HY2_PORT", "8444")),
        hy2_enabled=os.environ.get("HY2_ENABLED", "1").strip().lower() in ("1", "true", "yes"),
        # Default links: Happ mobile reliably imports URI list; JSON balancer is opt-in.
        sub_format=(os.environ.get("SUB_FORMAT", "links").strip().lower() or "links"),
        nl_relay_config=os.environ.get("NL_RELAY_CONFIG", "/usr/local/etc/xray/xeno-relay.json"),
        hy2_config=os.environ.get("HY2_CONFIG", "/etc/hysteria/config.yaml"),
        reality_client_fp=os.environ.get("REALITY_CLIENT_FP", "chrome"),
        canary_client_uuid=(
            os.environ.get("CANARY_CLIENT_UUID", "").strip()
            or os.environ.get("BOOTSTRAP_CLIENT_UUID", "").strip()
            or os.environ.get("CLIENT_UUID", "").strip()
        ),
        support_enabled=os.environ.get("SUPPORT_ENABLED", "1").strip().lower()
        in ("1", "true", "yes"),
        support_rate_limit=int(os.environ.get("SUPPORT_RATE_LIMIT", "5") or "5"),
        support_rate_window_sec=int(
            os.environ.get("SUPPORT_RATE_WINDOW_SEC", str(10 * 60)) or str(10 * 60)
        ),
        support_reopen_cooldown_sec=int(
            os.environ.get("SUPPORT_REOPEN_COOLDOWN_SEC", str(60 * 60)) or str(60 * 60)
        ),
        support_idle_close_sec=int(
            os.environ.get("SUPPORT_IDLE_CLOSE_SEC", str(72 * 60 * 60))
            or str(72 * 60 * 60)
        ),
        donate_wallets=_load_donate_wallets(),
    )
