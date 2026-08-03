#!/usr/bin/env python3
"""Tear down ONLY XENO.net VPN stack. Never touch 3x-ui (x-ui) or trading xeno-bot."""
from __future__ import annotations

import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deploy_two_node import load_env, run, ssh_connect  # noqa: E402


def banner(msg: str) -> None:
    print("\n" + "=" * 60)
    print(msg)
    print("=" * 60)


def inventory_nl(c: paramiko.SSHClient) -> None:
    banner("NL INVENTORY (before)")
    run(
        c,
        r"""
set -e
echo '=== units ==='
systemctl list-units --type=service --all --no-pager | grep -E 'xeno|x-ui|xray' || true
echo '=== unit files ==='
ls -la /etc/systemd/system/ | grep -E 'xeno|xray' || true
echo '=== runaway ==='
ls -la /etc/runaway/ 2>/dev/null || true
echo '=== xray cfg ==='
ls -la /usr/local/etc/xray/ 2>/dev/null || true
echo '=== /etc/xeno ==='
ls -la /etc/xeno/ 2>/dev/null || true
echo '=== /var/log/xeno ==='
ls -la /var/log/xeno 2>/dev/null || true
echo '=== ufw ==='
ufw status numbered 2>/dev/null || true
echo '=== active checks ==='
for u in x-ui xeno-bot xeno-relay xenonet-bot xenonet-sub xray; do
  printf '%s: ' "$u"; systemctl is-active "$u" 2>/dev/null || echo missing
done
""",
        check=False,
    )


def teardown_nl(c: paramiko.SSHClient, ru_ip: str) -> None:
    banner("NL TEARDOWN")
    # Stop/disable only our units — NEVER x-ui or xeno-bot
    run(
        c,
        r"""
set -e
for u in xeno-relay xenonet-bot xenonet-sub; do
  if systemctl list-unit-files | grep -q "^${u}.service"; then
    systemctl stop "$u" 2>/dev/null || true
    systemctl disable "$u" 2>/dev/null || true
    rm -f "/etc/systemd/system/${u}.service"
    echo "removed unit $u"
  else
    echo "no unit $u"
  fi
done
# drop any leftover xeno-relay drop-ins / overrides
rm -rf /etc/systemd/system/xeno-relay.service.d 2>/dev/null || true
rm -rf /etc/systemd/system/xenonet-bot.service.d 2>/dev/null || true
rm -rf /etc/systemd/system/xenonet-sub.service.d 2>/dev/null || true
systemctl daemon-reload
""",
        check=False,
    )

    # VPN bot tree only — never touch sibling product trees on the same host
    run(
        c,
        r"""
set -e
if [ -d /etc/runaway/xeno.net ]; then
  rm -rf /etc/runaway/xeno.net
  echo 'removed /etc/runaway/xeno.net'
else
  echo 'no /etc/runaway/xeno.net'
fi
# ensure trading tree still exists
ls -la /etc/runaway/ 2>/dev/null | head -20 || true
""",
        check=False,
    )

    # Our relay config / related
    run(
        c,
        r"""
set -e
rm -f /usr/local/etc/xray/xeno-relay.json
rm -f /usr/local/etc/xray/xeno-relay.json.bak 2>/dev/null || true
# /etc/xeno used by our bot deploy
if [ -d /etc/xeno ]; then
  rm -rf /etc/xeno
  echo 'removed /etc/xeno'
fi
if [ -d /var/log/xeno ]; then
  rm -rf /var/log/xeno
  echo 'removed /var/log/xeno'
fi
# standalone xray binary: only remove if NOT used by x-ui and no other configs remain that need it
# Keep binary — 3x-ui / other tools may share it. Do not uninstall xray package.
ls -la /usr/local/etc/xray/ 2>/dev/null || true
""",
        check=False,
    )

    # UFW: remove our rules (8443 from RU, 2080 xeno-sub). Leave 3x-ui ports.
    run(
        c,
        f"""
set +e
echo '=== ufw before cleanup ==='
ufw status numbered
python3 - <<'PY'
import subprocess, re
RU = "{ru_ip}"
out = subprocess.check_output(["ufw", "status", "numbered"], text=True, errors="replace")
to_del = []
for line in out.splitlines():
    m = re.match(r"\\[\\s*(\\d+)\\]\\s+(.*)", line)
    if not m:
        continue
    num, rest = int(m.group(1)), m.group(2).lower()
    if "8443" in rest and (RU.lower() in rest or "xeno" in rest or "relay" in rest):
        to_del.append(num)
    elif "2080" in rest and ("xeno" in rest or "sub" in rest):
        to_del.append(num)
    elif "xeno-relay" in rest or "xeno-sub" in rest or "xenonet" in rest:
        to_del.append(num)
for n in sorted(set(to_del), reverse=True):
    p = subprocess.run(["ufw", "--force", "delete", str(n)], capture_output=True, text=True)
    print("deleted ufw #{{}} rc={{}} {{}} {{}}".format(n, p.returncode, p.stdout.strip(), p.stderr.strip()))
if not to_del:
    print("no matching ufw rules found for 8443/2080 xeno")
PY
echo '=== ufw after cleanup ==='
ufw status numbered
""",
        check=False,
    )


def confirm_nl(c: paramiko.SSHClient) -> None:
    banner("NL CONFIRM (after)")
    out = run(
        c,
        r"""
echo '=== sacred must be active ==='
for u in x-ui xeno-bot; do
  s=$(systemctl is-active "$u" 2>/dev/null || echo missing)
  echo "$u=$s"
done
echo '=== our units must be gone/inactive ==='
for u in xeno-relay xenonet-bot xenonet-sub; do
  s=$(systemctl is-active "$u" 2>/dev/null || echo missing)
  echo "$u=$s"
done
echo '=== runaway ==='
ls -la /etc/runaway/ 2>/dev/null || true
echo '=== listeners (sample) ==='
ss -tlnp | grep -E ':(22|443|8443|2080|2096)\s' || true
""",
        check=False,
    )
    return out


def inventory_ru(c: paramiko.SSHClient) -> None:
    banner("RU INVENTORY (before)")
    run(
        c,
        r"""
echo '=== units ==='
systemctl list-units --type=service --all --no-pager | grep -E 'xeno|xray|nginx' || true
echo '=== unit files ==='
ls -la /etc/systemd/system/ | grep -E 'xeno|xray' || true
echo '=== xray cfg ==='
ls -la /usr/local/etc/xray/ 2>/dev/null || true
echo '=== www ==='
ls -la /var/www/ 2>/dev/null || true
echo '=== runaway ==='
ls -la /etc/runaway/ 2>/dev/null || true
echo '=== /etc/xeno ==='
ls -la /etc/xeno/ 2>/dev/null || true
echo '=== /var/log/xeno ==='
ls -la /var/log/xeno 2>/dev/null || true
echo '=== ufw ==='
ufw status numbered 2>/dev/null || true
echo '=== active ==='
for u in xray nginx; do printf '%s: ' "$u"; systemctl is-active "$u" 2>/dev/null || echo missing; done
""",
        check=False,
    )


def teardown_ru(c: paramiko.SSHClient) -> None:
    banner("RU TEARDOWN")
    run(
        c,
        r"""
set +e
# Stop our xray bridge
if systemctl list-unit-files | grep -q '^xray.service'; then
  systemctl stop xray 2>/dev/null || true
  systemctl disable xray 2>/dev/null || true
  echo 'stopped/disabled xray'
fi
# Remove custom unit if we dropped one (official installer also uses /etc/systemd/system/xray.service)
# Keep official unit file if present from Xray-install, but wipe our config so it won't listen.
rm -f /usr/local/etc/xray/config.json
rm -f /usr/local/etc/xray/config.json.bak 2>/dev/null || true
# Write inert config so accidental start does nothing useful
mkdir -p /usr/local/etc/xray
cat > /usr/local/etc/xray/config.json <<'EOF'
{
  "log": {"loglevel": "warning"},
  "inbounds": [],
  "outbounds": [{"protocol": "freedom", "tag": "direct"}]
}
EOF
echo 'wiped xray config to empty inbounds'

# Our sub/www leftovers
rm -rf /var/www/xeno-sub 2>/dev/null && echo 'removed /var/www/xeno-sub' || true
rm -rf /etc/runaway/xeno.net 2>/dev/null || true
rm -rf /etc/xeno 2>/dev/null || true
rm -rf /var/log/xeno 2>/dev/null || true

# Any leftover bot units we may have placed on RU (unlikely)
for u in xenonet-bot xenonet-sub xeno-relay; do
  if [ -f "/etc/systemd/system/${u}.service" ]; then
    systemctl stop "$u" 2>/dev/null || true
    systemctl disable "$u" 2>/dev/null || true
    rm -f "/etc/systemd/system/${u}.service"
    echo "removed $u"
  fi
done
systemctl daemon-reload
""",
        check=False,
    )

    # UFW: remove 443 xeno-client, 2080/2096 sub — leave SSH
    run(
        c,
        r"""
set +e
echo '=== ufw before ==='
ufw status numbered
python3 - <<'PY'
import subprocess, re
out = subprocess.check_output(["ufw", "status", "numbered"], text=True, errors="replace")
to_del = []
for line in out.splitlines():
    m = re.match(r"\[\s*(\d+)\]\s+(.*)", line)
    if not m:
        continue
    num, rest = int(m.group(1)), m.group(2).lower()
    if "22/tcp" in rest or "openssh" in rest:
        continue
    if any(p in rest for p in ("443", "2080", "2096")):
        if "xeno" in rest or "client" in rest or "sub" in rest or "vless" in rest or "xray" in rest:
            to_del.append(num)
        elif re.search(r"\b443(/tcp)?\b", rest) or re.search(r"\b2080\b", rest) or re.search(r"\b2096\b", rest):
            to_del.append(num)
for n in sorted(set(to_del), reverse=True):
    p = subprocess.run(["ufw", "--force", "delete", str(n)], capture_output=True, text=True)
    print("deleted ufw #{} rc={} {} {}".format(n, p.returncode, p.stdout.strip(), p.stderr.strip()))
if not to_del:
    print("no matching ufw rules for 443/2080/2096")
PY
echo '=== ufw after ==='
ufw status numbered
ufw allow OpenSSH 2>/dev/null || ufw allow 22/tcp 2>/dev/null || true
""",
        check=False,
    )


def confirm_ru(c: paramiko.SSHClient) -> None:
    banner("RU CONFIRM (after)")
    run(
        c,
        r"""
echo '=== xray status ==='
systemctl is-active xray 2>/dev/null || echo missing
systemctl is-enabled xray 2>/dev/null || echo not-enabled
echo '=== listeners ==='
ss -tlnp | grep -E ':(22|443|2080|2096)\s' || echo '(no 443/2080/2096)'
echo '=== leftover paths ==='
ls /var/www/xeno-sub 2>/dev/null && echo FAIL:xeno-sub || echo ok:no-xeno-sub
ls /etc/xeno 2>/dev/null && echo FAIL:etc-xeno || echo ok:no-etc-xeno
ls /var/log/xeno 2>/dev/null && echo FAIL:log-xeno || echo ok:no-log-xeno
echo '=== ufw ==='
ufw status numbered
""",
        check=False,
    )


def main() -> int:
    nl_acc = load_env(ROOT / "secrets" / "nl-access.env")
    ru_acc = load_env(ROOT / "secrets" / "ru-access.env")
    nl_ip = nl_acc["NL_EXIT_IP"]
    ru_ip = ru_acc["RU_BRIDGE_IP"]

    print(f"Connecting NL {nl_ip} ...")
    nl = ssh_connect(nl_ip, nl_acc["NL_SSH_PASS"], nl_acc.get("NL_SSH_USER", "root"))
    inventory_nl(nl)
    teardown_nl(nl, ru_ip)
    confirm_nl(nl)
    nl.close()

    print(f"\nConnecting RU {ru_ip} ...")
    ru = ssh_connect(ru_ip, ru_acc["RU_SSH_PASS"], ru_acc.get("RU_SSH_USER", "root"))
    inventory_ru(ru)
    teardown_ru(ru)
    confirm_ru(ru)
    ru.close()

    banner("TEARDOWN COMPLETE")
    print("Sacred on NL (must remain): x-ui, xeno-bot")
    print("Removed: xeno-relay, xenonet-bot, xenonet-sub, RU xray bridge, our UFW/paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
