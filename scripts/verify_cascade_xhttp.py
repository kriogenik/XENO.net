#!/usr/bin/env python3
"""E2E verify XHTTP+Reality cascade (post rebuild-plan). No bot required."""
from __future__ import annotations

import json
import sys
import time
import uuid as uuidlib
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]


def _inventory_path() -> Path:
    local = ROOT / "inventory" / "hosts.local.env"
    return local if local.is_file() else ROOT / "inventory" / "hosts.env"



def load(p: Path) -> dict[str, str]:
    d: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip().strip("'").strip('"')
    return d


def ssh(host: str, password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="root", password=password, timeout=30, allow_agent=False, look_for_keys=False)
    return c


def run(c: paramiko.SSHClient, cmd: str, t: int = 120) -> tuple[int, str, str]:
    _, o, e = c.exec_command(cmd, timeout=t)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    return o.channel.recv_exit_status(), out, err


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    nl_acc = load(ROOT / "secrets" / "nl-access.env")
    ru_acc = load(ROOT / "secrets" / "ru-access.env")
    inv = load(_inventory_path())
    reality = load(ROOT / "secrets" / "reality.env")
    uuids = load(ROOT / "secrets" / "uuids.env")

    nl_ip = inv["NL_EXIT_IP"]
    ru_ip = inv["RU_BRIDGE_IP"]
    cn = ssh(nl_ip, nl_acc["NL_SSH_PASS"])
    cr = ssh(ru_ip, ru_acc["RU_SSH_PASS"])

    results: list[tuple[str, str, str]] = []

    # Sacred
    for unit in ("x-ui", "xeno-bot"):
        _, out, _ = run(cn, f"systemctl is-active {unit}")
        ok = out.strip() == "active"
        results.append((f"NL {unit}", "PASS" if ok else "FAIL", out.strip()))

    for unit, host, c in (("xeno-relay", "NL", cn), ("xray", "RU", cr)):
        _, out, _ = run(c, f"systemctl is-active {unit}")
        ok = out.strip() == "active"
        results.append((f"{host} {unit}", "PASS" if ok else "FAIL", out.strip()))

    _, ufw, _ = run(cn, "ufw status | grep 8443 || true")
    ok = ru_ip in ufw and "8443" in ufw
    results.append(("NL UFW 8443 from RU", "PASS" if ok else "FAIL", ufw.strip()[:200]))

    code, out, err = run(cr, f"nc -zv -w 5 {nl_ip} 8443 2>&1 || true")
    ok = "succeeded" in (out + err).lower() or "open" in (out + err).lower()
    results.append(("RU TCP to NL:8443", "PASS" if ok else "FAIL", (out + err).strip()[:200]))

    # Pair check: hop shortId / path / uuid
    _, ru_raw, _ = run(cr, "cat /usr/local/etc/xray/config.json")
    _, nl_raw, _ = run(cn, "cat /usr/local/etc/xray/xeno-relay.json")
    ru_cfg = json.loads(ru_raw)
    nl_cfg = json.loads(nl_raw)
    ru_hop = next(o for o in ru_cfg["outbounds"] if o.get("tag") == "nl-exit")
    nl_in = nl_cfg["inbounds"][0]
    hop_user = ru_hop["settings"]["vnext"][0]["users"][0]
    nl_user = nl_in["settings"]["clients"][0]
    hop_rs = ru_hop["streamSettings"]["realitySettings"]
    nl_rs = nl_in["streamSettings"]["realitySettings"]
    hop_path = ru_hop["streamSettings"]["xhttpSettings"]["path"]
    nl_path = nl_in["streamSettings"]["xhttpSettings"]["path"]

    pair_ok = (
        hop_user["id"] == nl_user["id"] == uuids["RELAY_UUID"]
        and hop_rs["shortId"] in nl_rs["shortIds"]
        and hop_rs["serverName"] in nl_rs["serverNames"]
        and hop_rs["publicKey"] == reality["RELAY_REALITY_PUBLIC_KEY"]
        and hop_path == nl_path == reality["RELAY_PATH"]
        and ru_hop["streamSettings"]["network"] == "xhttp"
        and nl_in["streamSettings"]["network"] == "xhttp"
        and not hop_user.get("flow")
        and not nl_user.get("flow")
    )
    results.append(("Hop XHTTP Reality pair", "PASS" if pair_ok else "FAIL", f"path={hop_path} sid={hop_rs.get('shortId')}"))

    client_uuid = uuids["CLIENT_UUID"]
    marker = f"e2e-{uuidlib.uuid4().hex[:8]}"
    client_cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": 18080,
                "protocol": "socks",
                "settings": {"udp": True},
            }
        ],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "127.0.0.1",
                            "port": int(inv.get("CLIENT_PORT", "443")),
                            "users": [{"id": client_uuid, "encryption": "none", "email": marker}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "reality",
                    "xhttpSettings": {"path": reality["BRIDGE_PATH"], "mode": "auto"},
                    "realitySettings": {
                        "serverName": reality["BRIDGE_REALITY_SNI"],
                        "fingerprint": "randomized",
                        "publicKey": reality["BRIDGE_REALITY_PUBLIC_KEY"],
                        "shortId": reality["BRIDGE_REALITY_SHORT_ID"],
                    },
                },
            }
        ],
    }
    sftp = cr.open_sftp()
    with sftp.file("/tmp/xeno-e2e-client.json", "w") as f:
        f.write(json.dumps(client_cfg))
    sftp.close()

    run(cr, "pkill -f 'xray.*xeno-e2e-client' || true; sleep 1")
    run(cr, "nohup /usr/local/bin/xray run -c /tmp/xeno-e2e-client.json >/tmp/xeno-e2e-client.log 2>&1 & sleep 2")
    _, out, err = run(
        cr,
        "curl -4 -sS -m 25 --socks5-hostname 127.0.0.1:18080 http://ipv4.icanhazip.com; echo; "
        "curl -4 -sS -m 25 --socks5-hostname 127.0.0.1:18080 http://ifconfig.me/ip; echo",
    )
    ips = [x.strip() for x in (out or "").splitlines() if x.strip()]
    e2e_ok = nl_ip in ips
    results.append(("E2E exit IP = NL", "PASS" if e2e_ok else "FAIL", f"ips={ips} err={err[:120]}"))
    _, elog, _ = run(cr, "tail -30 /tmp/xeno-e2e-client.log")
    if not e2e_ok:
        print("e2e client log:\n", elog)

    run(cr, "pkill -f 'xray.*xeno-e2e-client' || true")
    cn.close()
    cr.close()

    print("\n========== RESULTS ==========")
    for name, status, detail in results:
        print(f"{status:6} | {name}: {detail[:200]}")
    print(f"\nE2E_OK={e2e_ok}")
    sub = (ROOT / "secrets" / "subscription.txt").read_text(encoding="utf-8").strip() if (ROOT / "secrets" / "subscription.txt").exists() else ""
    if sub:
        print("VLESS:", sub.splitlines()[0][:120], "...")
    return 0 if e2e_ok and all(s != "FAIL" for _, s, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
