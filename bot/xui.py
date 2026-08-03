"""3x-ui panel API client (Bearer token). Manages the dedicated XENO inbound only."""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


def _int_set(env_name: str, default: str) -> frozenset[int]:
    raw = os.environ.get(env_name, default)
    return frozenset(int(x) for x in raw.split(",") if x.strip().isdigit())


# Чужие inbound’ы панели — никогда не трогаем. ID задаются в bot.env (не в публичном git).
# Пустой XUI_SACRED_INBOUND_PORTS = проверка только по ID.
SACRED_INBOUND_IDS: frozenset[int] = _int_set("XUI_SACRED_INBOUND_IDS", "2,3")
SACRED_INBOUND_PORTS: frozenset[int] = _int_set("XUI_SACRED_INBOUND_PORTS", "")


class XuiError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServerStatus:
    cpu: float
    mem_used: int
    mem_total: int
    disk_used: int
    disk_total: int
    uptime: int
    loads: tuple[float, float, float]
    xray_state: str
    xray_version: str
    panel_version: str
    net_up: int
    net_down: int
    tcp_count: int
    udp_count: int
    public_ip: str


@dataclass(frozen=True)
class ClientTraffic:
    email: str
    up: int
    down: int
    enable: bool
    expiry_time: int
    last_online: int | None = None


class XuiClient:
    def __init__(self, base_url: str, api_token: str, *, verify_tls: bool = False) -> None:
        self.base = base_url.rstrip("/")
        self.token = api_token.strip()
        self.ctx = None if verify_tls else ssl._create_unverified_context()

    def _req(self, method: str, path: str, data: dict | None = None) -> Any:
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=45) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            raise XuiError(f"HTTP {e.code} {path}: {raw[:400]}") from e
        except Exception as e:
            raise XuiError(f"{method} {path}: {e}") from e
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise XuiError(f"bad JSON from {path}: {raw[:200]}") from e
        if isinstance(parsed, dict) and parsed.get("success") is False:
            raise XuiError(parsed.get("msg") or str(parsed))
        return parsed

    def server_status(self) -> ServerStatus:
        obj = (self._req("GET", "/panel/api/server/status") or {}).get("obj") or {}
        mem = obj.get("mem") or {}
        disk = obj.get("disk") or {}
        xray = obj.get("xray") or {}
        net = obj.get("netIO") or {}
        loads = obj.get("loads") or [0, 0, 0]
        pub = obj.get("publicIP") or {}
        return ServerStatus(
            cpu=float(obj.get("cpu") or 0),
            mem_used=int(mem.get("current") or 0),
            mem_total=int(mem.get("total") or 1),
            disk_used=int(disk.get("current") or 0),
            disk_total=int(disk.get("total") or 1),
            uptime=int(obj.get("uptime") or 0),
            loads=(float(loads[0]), float(loads[1]), float(loads[2])),
            xray_state=str(xray.get("state") or "unknown"),
            xray_version=str(xray.get("version") or ""),
            panel_version=str(obj.get("panelVersion") or ""),
            net_up=int(net.get("up") or 0),
            net_down=int(net.get("down") or 0),
            tcp_count=int(obj.get("tcpCount") or 0),
            udp_count=int(obj.get("udpCount") or 0),
            public_ip=str(pub.get("ipv4") or ""),
        )

    def get_inbound(self, inbound_id: int) -> dict:
        if inbound_id in SACRED_INBOUND_IDS:
            raise XuiError(f"inbound {inbound_id} is sacred — refused")
        obj = (self._req("GET", f"/panel/api/inbounds/get/{inbound_id}") or {}).get("obj")
        if not obj:
            raise XuiError(f"inbound {inbound_id} not found")
        port = int(obj.get("port") or 0)
        if port in SACRED_INBOUND_PORTS:
            raise XuiError(f"inbound {inbound_id} port {port} is sacred — refused")
        return obj

    def list_inbounds(self) -> list[dict]:
        return (self._req("GET", "/panel/api/inbounds/list") or {}).get("obj") or []

    def inbound_client_emails(self, inbound_id: int) -> set[str]:
        return set(self.inbound_clients(inbound_id))

    def inbound_clients(self, inbound_id: int) -> dict[str, dict[str, Any]]:
        """Return email -> client dict for the given inbound."""
        ib = self.get_inbound(inbound_id)
        settings = ib.get("settings")
        if isinstance(settings, str):
            settings = json.loads(settings)
        out: dict[str, dict[str, Any]] = {}
        for c in (settings or {}).get("clients") or []:
            email = str(c.get("email") or "")
            if email:
                out[email] = dict(c)
        return out

    def add_client(
        self,
        *,
        inbound_id: int,
        email: str,
        client_uuid: str,
        sub_id: str,
        expiry_ms: int = 0,
        tg_id: int | None = None,
        total_gb: int = 0,
        enable: bool = True,
        comment: str = "",
    ) -> None:
        """Add client ONLY to the given inbound (never broadcast to all). Idempotent."""
        if inbound_id in SACRED_INBOUND_IDS:
            raise XuiError(f"refusing add_client on sacred inbound {inbound_id}")
        client: dict[str, Any] = {
            "id": client_uuid,
            "email": email,
            "enable": enable,
            "expiryTime": expiry_ms,
            "totalGB": total_gb,
            "limitIp": 0,
            "subId": sub_id,
            "flow": "",
            "reset": 0,
            "comment": comment or "",
        }
        if tg_id is not None:
            client["tgId"] = tg_id
        try:
            self._req(
                "POST",
                "/panel/api/clients/add",
                {"inboundIds": [inbound_id], "client": client},
            )
        except XuiError as exc:
            msg = str(exc).lower()
            if "already" in msg or "exist" in msg or "duplicate" in msg:
                return
            raise

    def update_client(
        self,
        *,
        inbound_id: int,
        email: str,
        client: dict[str, Any],
        comment: str | None = None,
    ) -> None:
        """Update an existing client on the given inbound (preserves UUID/credentials)."""
        if inbound_id in SACRED_INBOUND_IDS:
            raise XuiError(f"refusing update_client on sacred inbound {inbound_id}")
        updated = dict(client)
        if comment is not None:
            updated["comment"] = comment
        if not updated.get("id"):
            raise XuiError(f"update_client missing id for {email}")
        updated["email"] = email
        path = "/panel/api/clients/update/" + urllib.parse.quote(email, safe="")
        try:
            self._req("POST", path, updated)
        except XuiError as exc:
            msg = str(exc).lower()
            if "record not found" in msg:
                # Legacy panel: fall back to inbound-scoped updateClient.
                settings = json.dumps({"clients": [updated]})
                self._req_form(
                    "POST",
                    f"/panel/api/inbounds/updateClient/{updated['id']}",
                    {"id": inbound_id, "settings": settings},
                )
                return
            raise

    def _req_form(self, method: str, path: str, data: dict[str, Any]) -> Any:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=45) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            raise XuiError(f"HTTP {e.code} {path}: {raw[:400]}") from e
        except Exception as e:
            raise XuiError(f"{method} {path}: {e}") from e
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise XuiError(f"bad JSON from {path}: {raw[:200]}") from e
        if isinstance(parsed, dict) and parsed.get("success") is False:
            raise XuiError(parsed.get("msg") or str(parsed))
        return parsed

    def delete_client(self, email: str) -> None:
        self._req("POST", f"/panel/api/clients/del/{email}")

    def client_stats_for_inbound(self, inbound_id: int) -> list[ClientTraffic]:
        ib = self.get_inbound(inbound_id)
        out: list[ClientTraffic] = []
        for st in ib.get("clientStats") or []:
            out.append(
                ClientTraffic(
                    email=str(st.get("email") or ""),
                    up=int(st.get("up") or 0),
                    down=int(st.get("down") or 0),
                    enable=bool(st.get("enable")),
                    expiry_time=int(st.get("expiryTime") or 0),
                    last_online=int(st["lastOnline"]) if st.get("lastOnline") else None,
                )
            )
        return out

    def inbound_totals(self, inbound_id: int) -> tuple[int, int, int]:
        """Returns (up, down, client_count)."""
        ib = self.get_inbound(inbound_id)
        settings = ib.get("settings")
        if isinstance(settings, str):
            settings = json.loads(settings)
        n = len((settings or {}).get("clients") or [])
        return int(ib.get("up") or 0), int(ib.get("down") or 0), n
