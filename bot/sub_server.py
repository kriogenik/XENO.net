from __future__ import annotations

import base64
import ssl
import time
from pathlib import Path

from aiohttp import web

from config import load_settings
from ops_events import (
    KIND_SUB_404,
    emit_rate_limited,
    hash_ip,
    truncate_ip,
    truncate_token,
)
from provision import sub_url

_URI_PREFIXES = (
    "vless://",
    "vmess://",
    "trojan://",
    "ss://",
    "ssr://",
    "hysteria2://",
    "hy2://",
    "tuic://",
    "wireguard://",
)


def _b64_header(value: str) -> str:
    """Happ announce/profile values: base64:… prefix (UTF-8)."""
    return "base64:" + base64.b64encode(value.encode("utf-8")).decode("ascii")


def _looks_like_uri_list(text: str) -> bool:
    s = text.lstrip()
    if not s:
        return False
    if s.startswith("#"):
        # Happ body meta lines, then URIs
        for line in s.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            return any(line.startswith(p) for p in _URI_PREFIXES)
        return False
    return any(s.startswith(p) for p in _URI_PREFIXES)


def _normalize_body(raw: str) -> tuple[str, bool]:
    """Return (body, is_json). Decode legacy base64 URI blobs for iOS Happ."""
    stripped = raw.strip()
    if not stripped:
        return raw, False
    if stripped.startswith("[") or stripped.startswith("{"):
        return raw if raw.endswith("\n") else raw + "\n", True
    if _looks_like_uri_list(stripped):
        return raw if raw.endswith("\n") else raw + "\n", False
    # Legacy: single-line base64 of vless://… list (Android often accepted; iOS Happ → unknown type)
    try:
        decoded = base64.b64decode(stripped, validate=False).decode("utf-8")
    except Exception:
        return raw if raw.endswith("\n") else raw + "\n", False
    if _looks_like_uri_list(decoded) or decoded.lstrip().startswith(("[", "{")):
        is_json = decoded.lstrip().startswith(("[", "{"))
        body = decoded if decoded.endswith("\n") else decoded + "\n"
        return body, is_json
    return raw if raw.endswith("\n") else raw + "\n", False


def build_sub_response_headers(*, web_page: str, is_json: bool) -> dict[str, str]:
    """Happ subscription HTTP headers (shared with tests).

    No subscription-autoconnect / lowestdelay: that silently picked NL Direct when
    ping was lower and users thought RU was «dead». Client must pick 🇷🇺 RU manually.
    No Content-Disposition: attachment — iOS Safari → «в разрешении отказано».
    """
    content_type = "application/json; charset=utf-8" if is_json else "text/plain; charset=utf-8"
    announce = (
        "XENO · обновите подписку и вручную выберите 🇷🇺 RU. "
        "Если не коннектится: удалите профиль XENO → добавьте ссылку заново. "
        "iOS: Dev Settings → No Limit Mode. Windows: Happ от администратора / Proxy."
        if not is_json
        else "XENO smart JSON. Если не коннектится — попросите links-формат."
    )
    return {
        "Content-Type": content_type,
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "Profile-Update-Interval": "1",
        "Profile-Title": "XENO.net",
        "Profile-Web-Page-Url": web_page,
        "Announce": _b64_header(announce),
    }


async def handle_sub(request: web.Request) -> web.Response:
    token = request.match_info["token"]
    settings = request.app["settings"]
    path = settings.sub_root / token / "sub.txt"
    if not path.exists():
        alt = settings.sub_root / token / "index.txt"
        path = alt if alt.exists() else path
    if not path.exists():
        peer = request.remote or ""
        emit_rate_limited(
            KIND_SUB_404,
            key=hash_ip(peer) or peer or "unknown",
            min_interval_sec=60.0,
            token_prefix=truncate_token(token),
            ip_trunc=truncate_ip(peer),
            ip_hash=hash_ip(peer),
            req_path=str(request.path)[:80],
        )
        return web.Response(status=404, text="not found")
    raw = path.read_text(encoding="utf-8")
    body, is_json = _normalize_body(raw)
    bust = int(path.stat().st_mtime) if path.exists() else int(time.time())
    web_page = f"{sub_url(settings, token)}?v={bust}"
    return web.Response(
        text=body,
        headers=build_sub_response_headers(web_page=web_page, is_json=is_json),
    )


def _ssl_context(cert: str, key: str) -> ssl.SSLContext | None:
    if not cert or not key:
        return None
    cert_path, key_path = Path(cert), Path(key)
    if not cert_path.is_file() or not key_path.is_file():
        raise RuntimeError(f"SUB_TLS_CERT/KEY missing: {cert_path} / {key_path}")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return ctx


def main() -> None:
    settings = load_settings(require_token=False)
    settings.sub_root.mkdir(parents=True, exist_ok=True)
    app = web.Application()
    app["settings"] = settings
    app.router.add_get("/sub/{token}/", handle_sub)
    app.router.add_get("/{token}/", handle_sub)
    ssl_ctx = _ssl_context(settings.sub_tls_cert, settings.sub_tls_key)
    web.run_app(app, host="0.0.0.0", port=settings.sub_port, ssl_context=ssl_ctx)


if __name__ == "__main__":
    main()
