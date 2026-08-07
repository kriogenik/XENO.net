"""Shared Reality hop probe — VLESS+XHTTP to local :8443 → loopback canary HTTP.

Relay blocks ``geoip:private`` by default; we allow only ``127.0.0.1:19443``
(canary port) so the probe never dials third-party sites or user destinations.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

STATE_PATH = Path("/var/log/xeno/hop_canary.json")
RELAY_CONFIG = Path(os.environ.get("NL_RELAY_CONFIG", "/usr/local/etc/xray/xeno-relay.json"))
XRAY_BIN = os.environ.get("XRAY_BIN", "/usr/local/bin/xray")
HOP_ADDR = "127.0.0.1"
# Narrow exception to private-block on relay — only this port is allowed for canary.
CANARY_PORT = 19443
CANARY_URL = f"http://127.0.0.1:{CANARY_PORT}/"
CANARY_BODY = b"xeno-hop-canary"


@dataclass
class HopProbeResult:
    ok: bool
    detail: str
    elapsed_ms: int = 0


class _CanaryHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(CANARY_BODY)))
        self.end_headers()
        self.wfile.write(CANARY_BODY)

    def log_message(self, *_args: Any) -> None:
        return


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _env_relay() -> dict[str, str] | None:
    uuid = (os.environ.get("RELAY_UUID") or "").strip()
    pbk = (os.environ.get("RELAY_REALITY_PUBLIC_KEY") or "").strip()
    sid = (os.environ.get("RELAY_REALITY_SHORT_ID") or "").strip()
    sni = (os.environ.get("RELAY_REALITY_SNI") or os.environ.get("NL_DOMAIN") or "").strip()
    path = (os.environ.get("RELAY_PATH") or "").strip()
    port = int(os.environ.get("RELAY_PORT") or "8443")
    fp = (os.environ.get("REALITY_CLIENT_FP") or "chrome").strip() or "chrome"
    if not all((uuid, pbk, sid, sni, path)):
        return None
    return {
        "uuid": uuid,
        "pbk": pbk,
        "sid": sid,
        "sni": sni,
        "path": path,
        "port": str(port),
        "fp": fp,
    }


def build_client_config(*, socks_port: int, relay: dict[str, str]) -> dict[str, Any]:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": socks_port,
                "protocol": "socks",
                "settings": {"udp": False},
            }
        ],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": HOP_ADDR,
                            "port": int(relay["port"]),
                            "users": [{"id": relay["uuid"], "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "reality",
                    "xhttpSettings": {"path": relay["path"], "mode": "stream-one"},
                    "realitySettings": {
                        "serverName": relay["sni"],
                        "fingerprint": relay["fp"],
                        "publicKey": relay["pbk"],
                        "shortId": relay["sid"],
                    },
                },
            }
        ],
    }


def canary_routing_rule() -> dict[str, Any]:
    return {
        "type": "field",
        "ip": ["127.0.0.1"],
        "port": str(CANARY_PORT),
        "outboundTag": "direct",
    }


def _rule_is_canary(rule: dict[str, Any]) -> bool:
    ips = rule.get("ip") or []
    port = str(rule.get("port") or "")
    return (
        rule.get("type") == "field"
        and "127.0.0.1" in ips
        and port == str(CANARY_PORT)
        and rule.get("outboundTag") == "direct"
    )


def ensure_canary_routing(
    *,
    config_path: Path = RELAY_CONFIG,
    unit: str = "xeno-relay",
) -> str:
    """Insert 127.0.0.1:19443 → direct before geoip:private block. Restart relay if changed.

    Returns: ``ok`` | ``already`` | ``missing_config`` | ``error:...``
    """
    if not config_path.is_file():
        return "missing_config"
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"error:read:{type(exc).__name__}"

    routing = cfg.setdefault("routing", {})
    rules: list[dict[str, Any]] = list(routing.get("rules") or [])
    if any(_rule_is_canary(r) for r in rules if isinstance(r, dict)):
        return "already"

    insert_at = 0
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            continue
        ips = r.get("ip") or []
        if "geoip:private" in ips and r.get("outboundTag") == "block":
            insert_at = i
            break
        insert_at = i + 1

    rules.insert(insert_at, canary_routing_rule())
    routing["rules"] = rules
    try:
        config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return f"error:write:{type(exc).__name__}"

    try:
        subprocess.run(["systemctl", "restart", unit], check=False, timeout=60)
        time.sleep(1.5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error:restart:{type(exc).__name__}"
    return "ok"


def probe_hop_reality(*, timeout: float = 20.0, ensure_route: bool = True) -> HopProbeResult:
    """Run short-lived local xray client against 127.0.0.1:RELAY_PORT → canary HTTP."""
    relay = _env_relay()
    if not relay:
        return HopProbeResult(ok=False, detail="missing_relay_env")
    if not Path(XRAY_BIN).is_file():
        return HopProbeResult(ok=False, detail="xray_bin_missing")

    lock_fp = None
    try:
        lock_path = Path("/run/xeno-hop-canary.lock")
        lock_fp = lock_path.open("w")
        import fcntl

        # Block until peer probe finishes — avoids curl_rc=56 races (smoke vs hop_watch).
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
    except OSError:
        # Non-Linux — best effort without exclusive lock
        lock_fp = None

    try:
        return _probe_locked(relay=relay, timeout=timeout, ensure_route=ensure_route)
    finally:
        if lock_fp is not None:
            try:
                import fcntl

                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                lock_fp.close()
            except OSError:
                pass


def _probe_locked(
    *,
    relay: dict[str, str],
    timeout: float,
    ensure_route: bool,
) -> HopProbeResult:
    if ensure_route:
        route = ensure_canary_routing()
        if route.startswith("error:") or route == "missing_config":
            return HopProbeResult(ok=False, detail=f"canary_route:{route}")

    socks_port = _free_port()
    cfg = build_client_config(socks_port=socks_port, relay=relay)
    t0 = time.monotonic()
    proc: subprocess.Popen[bytes] | None = None
    cfg_path: Path | None = None
    httpd: HTTPServer | None = None
    try:
        httpd = HTTPServer(("127.0.0.1", CANARY_PORT), _CanaryHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        fd, name = tempfile.mkstemp(prefix="xeno-hop-canary-", suffix=".json")
        os.close(fd)
        cfg_path = Path(name)
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        proc = subprocess.Popen(
            [XRAY_BIN, "run", "-c", str(cfg_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return HopProbeResult(ok=False, detail="xray_exited_early", elapsed_ms=_ms(t0))
            try:
                with socket.create_connection(("127.0.0.1", socks_port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            return HopProbeResult(ok=False, detail="socks_not_ready", elapsed_ms=_ms(t0))

        # Let XHTTP+Reality session settle — immediate curl often gets rc=56 under load.
        time.sleep(0.6)

        curl_t = max(5, int(timeout) - 5)
        body = b""
        rc = -1
        for attempt in range(3):
            r = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--max-time",
                    str(curl_t),
                    "-x",
                    f"socks5h://127.0.0.1:{socks_port}",
                    CANARY_URL,
                ],
                capture_output=True,
                timeout=curl_t + 3,
            )
            body = (r.stdout or b"").strip()
            rc = int(r.returncode)
            if rc == 0 and body == CANARY_BODY:
                return HopProbeResult(ok=True, detail="canary_ok", elapsed_ms=_ms(t0))
            # Transient relay blip / peer teardown
            if rc in (56, 52, 28) and attempt < 2:
                time.sleep(1.0)
                continue
            break
        return HopProbeResult(
            ok=False,
            detail=f"curl_rc={rc}_body={body[:40]!r}",
            elapsed_ms=_ms(t0),
        )
    except OSError as exc:
        if getattr(exc, "errno", None) == 98:
            return HopProbeResult(ok=False, detail="canary_port_busy", elapsed_ms=_ms(t0))
        return HopProbeResult(ok=False, detail=f"exc:{type(exc).__name__}", elapsed_ms=_ms(t0))
    except subprocess.TimeoutExpired:
        return HopProbeResult(ok=False, detail="exc:TimeoutExpired", elapsed_ms=_ms(t0))
    finally:
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        if proc is not None and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        if cfg_path is not None:
            try:
                cfg_path.unlink(missing_ok=True)
            except OSError:
                pass


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def read_state(path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# curl 56/52/28 = transient relay/client teardown under load — not a dead hop.
_TRANSIENT_DETAILS = ("canary_busy", "canary_port_busy")
_TRANSIENT_CURL_PREFIXES = ("curl_rc=56_", "curl_rc=52_", "curl_rc=28_")
# If hop answered OK recently, blips must not open Telegram spam.
_TRANSIENT_GRACE_SEC = 20 * 60


def _is_transient_fail(detail: str, *, last_ok_at: int | None, now: int) -> bool:
    if detail in _TRANSIENT_DETAILS:
        return True
    if not any(detail.startswith(p) for p in _TRANSIENT_CURL_PREFIXES):
        return False
    if not last_ok_at:
        return False
    return (now - int(last_ok_at)) <= _TRANSIENT_GRACE_SEC


def write_state(result: HopProbeResult, *, path: Path = STATE_PATH) -> dict[str, Any]:
    prev = read_state(path)
    now = int(time.time())
    last_ok = int(prev.get("last_ok_at") or 0) or None
    soft = (not result.ok) and _is_transient_fail(
        result.detail, last_ok_at=last_ok, now=now
    )
    if soft:
        # Keep previous health for alerting; still record the blip detail.
        state = {
            "ok": True if last_ok else bool(prev.get("ok")),
            "consecutive_fail": 0 if last_ok else int(prev.get("consecutive_fail") or 0),
            "last_ok_at": last_ok,
            "last_check_at": now,
            "detail": result.detail,
            "elapsed_ms": result.elapsed_ms,
            "soft_skip": True,
        }
    elif result.ok:
        state = {
            "ok": True,
            "consecutive_fail": 0,
            "last_ok_at": now,
            "last_check_at": now,
            "detail": result.detail,
            "elapsed_ms": result.elapsed_ms,
        }
    else:
        state = {
            "ok": False,
            "consecutive_fail": int(prev.get("consecutive_fail") or 0) + 1,
            "last_ok_at": last_ok,
            "last_check_at": now,
            "detail": result.detail,
            "elapsed_ms": result.elapsed_ms,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    return state


def canary_alerting(
    state: dict[str, Any] | None = None,
    *,
    min_fails: int = 5,
    max_age_sec: int = 20 * 60,
    now: int | None = None,
) -> bool:
    """True when hop Reality canary should open an alert.

    Default ≥5 consecutive *hard* fails (~15 min at 3-min timer). Transient
    curl_rc=56/52 blips are soft-skipped in write_state when hop was recently OK.
    """
    st = state if state is not None else read_state()
    if not st:
        return False
    if bool(st.get("ok")) or bool(st.get("soft_skip")):
        return False
    if int(st.get("consecutive_fail") or 0) < min_fails:
        return False
    ts = int(st.get("last_check_at") or 0)
    n = now if now is not None else int(time.time())
    if not ts or (n - ts) > max_age_sec:
        return False
    return True
