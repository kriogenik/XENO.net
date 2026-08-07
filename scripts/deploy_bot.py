#!/usr/bin/env python3
"""Deploy XENO.net Telegram bot + sub HTTP under product install root.

Provisions clients onto entry Xray via SSH (cascade). Never touches sacred panel inbounds
or sibling services on the same host.
"""
from __future__ import annotations

import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
BOT = ROOT / "bot"
INSTALL_ROOT = "/etc/runaway/xeno.net"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v.strip().strip("'").strip('"')
    return env


def ssh_connect(host: str, password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="root", password=password, timeout=30, allow_agent=False, look_for_keys=False)
    return c


def run(c: paramiko.SSHClient, cmd: str, check: bool = True) -> str:
    print(" $", cmd[:180].replace("\n", " "))
    _, o, e = c.exec_command(cmd, timeout=600)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        sys.stdout.buffer.write((out.rstrip()[:4000] + "\n").encode("utf-8", "replace"))
    if check and code != 0:
        raise RuntimeError(f"failed ({code}): {cmd}\n{err[:2000]}")
    return out


def sftp_write(c: paramiko.SSHClient, remote: str, data: str) -> None:
    sftp = c.open_sftp()
    with sftp.file(remote, "w") as f:
        f.write(data if data.endswith("\n") else data + "\n")
    sftp.close()


def sftp_put_dir(c: paramiko.SSHClient, local: Path, remote: str) -> None:
    run(c, f"mkdir -p {remote}")
    sftp = c.open_sftp()
    for path in local.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = path.relative_to(local).as_posix()
        rpath = f"{remote}/{rel}"
        parent = "/".join(rpath.split("/")[:-1])
        run(c, f"mkdir -p {parent}", check=False)
        sftp.put(str(path), rpath)
        print("  put", rel)
    sftp.close()


def main() -> int:
    bot_env = (SECRETS / "bot.env").read_text(encoding="utf-8")
    bridge_env = (SECRETS / "bridge.env").read_text(encoding="utf-8")
    nl = load_env(SECRETS / "nl-access.env")
    ru_acc = load_env(SECRETS / "ru-access.env")
    ru_ssh = (
        f"RU_SSH_HOST={ru_acc.get('RU_SSH_HOST') or ru_acc.get('RU_BRIDGE_IP') or 'CHANGE_ME_RU'}\n"
        f"RU_SSH_USER={ru_acc.get('RU_SSH_USER', 'root')}\n"
        f"RU_SSH_PASS={ru_acc['RU_SSH_PASS']}\n"
    )
    nl_host = nl.get("NL_EXIT_IP") or nl.get("NL_SSH_HOST") or "CHANGE_ME_NL"
    nl_pass = nl["NL_SSH_PASS"]

    app = f"{INSTALL_ROOT}/bot"
    conf = f"{INSTALL_ROOT}/config"
    data = f"{INSTALL_ROOT}/data"
    www = f"{INSTALL_ROOT}/www/sub"
    venv = f"{INSTALL_ROOT}/.venv"

    c = ssh_connect(nl_host, nl_pass)
    run(c, "systemctl is-active x-ui xeno-bot xeno-relay || true", check=False)
    run(c, f"mkdir -p {app} {conf} {data} {www} /var/log/xeno")

    sftp_write(c, f"{conf}/bot.env", bot_env)
    sftp_write(c, f"{conf}/bridge.env", bridge_env)
    sftp_write(c, f"{conf}/ru-ssh.env", ru_ssh)
    run(c, "mkdir -p /etc/xeno")
    run(
        c,
        f"ln -sfn {conf}/bot.env /etc/xeno/bot.env && "
        f"ln -sfn {conf}/bridge.env /etc/xeno/bridge.env && "
        f"ln -sfn {conf}/ru-ssh.env /etc/xeno/ru-ssh.env && "
        f"chmod 600 {conf}/bot.env {conf}/bridge.env {conf}/ru-ssh.env",
        check=False,
    )

    sftp_put_dir(c, BOT, app)

    # Prefer absolute venv python in oneshots (avoid ../.venv path quirks)
    py = f"{venv}/bin/python"

    run(
        c,
        f"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-pip python3-venv
python3 -m venv {venv}
{venv}/bin/pip install -U pip
{venv}/bin/pip install -r {app}/requirements.txt
""",
    )

    bot_unit = f"""[Unit]
Description=XENO.net Telegram bot (RU cascade via SSH; sacred 3x-ui untouched)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={app}
EnvironmentFile={conf}/bridge.env
EnvironmentFile={conf}/bot.env
EnvironmentFile={conf}/ru-ssh.env
ExecStart={venv}/bin/python {app}/main.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
    sub_unit = f"""[Unit]
Description=XENO.net Happ subscription HTTPS NL :2080
After=network.target

[Service]
Type=simple
WorkingDirectory={app}
EnvironmentFile={conf}/bridge.env
EnvironmentFile={conf}/bot.env
ExecStart={venv}/bin/python {app}/sub_server.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
    sftp_write(c, "/etc/systemd/system/xenonet-bot.service", bot_unit)
    sftp_write(c, "/etc/systemd/system/xenonet-sub.service", sub_unit)

    diag_collect_unit = f"""[Unit]
Description=XENO.net connection diag collector (ingest logs → SQLite)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={app}
EnvironmentFile={conf}/bridge.env
EnvironmentFile={conf}/bot.env
EnvironmentFile={conf}/ru-ssh.env
ExecStart={venv}/bin/python -m diag --collect-only --alerts
Nice=10
"""
    diag_collect_timer = """[Unit]
Description=XENO.net diag collect every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=1min
Persistent=true
Unit=xenonet-diag-collect.service

[Install]
WantedBy=timers.target
"""
    diag_digest_unit = f"""[Unit]
Description=XENO.net connection digests (daily/weekly/monthly files)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={app}
EnvironmentFile={conf}/bridge.env
EnvironmentFile={conf}/bot.env
EnvironmentFile={conf}/ru-ssh.env
ExecStart={venv}/bin/python -m diag --digest-only --emit all
Nice=10
"""
    diag_digest_timer = """[Unit]
Description=XENO.net digests at 03:10 UTC daily

[Timer]
OnCalendar=*-*-* 03:10:00
Persistent=true
Unit=xenonet-diag-digest.service

[Install]
WantedBy=timers.target
"""
    diag_smoke_unit = f"""[Unit]
Description=XENO.net hourly E2E smoke + alerts
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={app}
EnvironmentFile={conf}/bridge.env
EnvironmentFile={conf}/bot.env
EnvironmentFile={conf}/ru-ssh.env
ExecStart={venv}/bin/python -m diag --smoke-only --alerts
Nice=10
"""
    diag_smoke_timer = """[Unit]
Description=XENO.net smoke hourly

[Timer]
OnBootSec=3min
OnUnitActiveSec=1h
AccuracySec=2min
Persistent=true
Unit=xenonet-diag-smoke.service

[Install]
WantedBy=timers.target
"""
    sftp_write(c, "/etc/systemd/system/xenonet-diag-collect.service", diag_collect_unit)
    sftp_write(c, "/etc/systemd/system/xenonet-diag-collect.timer", diag_collect_timer)
    sftp_write(c, "/etc/systemd/system/xenonet-diag-digest.service", diag_digest_unit)
    sftp_write(c, "/etc/systemd/system/xenonet-diag-digest.timer", diag_digest_timer)
    sftp_write(c, "/etc/systemd/system/xenonet-diag-smoke.service", diag_smoke_unit)
    sftp_write(c, "/etc/systemd/system/xenonet-diag-smoke.timer", diag_smoke_timer)

    # SelfSteal health: hung process can still be "active" while HTTPS :9443 wedges Reality hop.
    # Logs reason to /var/log/xeno/events.jsonl before restart.
    steal_watch_unit = f"""[Unit]
Description=XENO.net SelfSteal HTTPS healthcheck (restart if :9443 wedged)
After=network-online.target xeno-steal-nl.service

[Service]
Type=oneshot
WorkingDirectory={app}
ExecStart={py} -m diag.steal_watch
Nice=10
"""
    steal_watch_timer = """[Unit]
Description=XENO.net SelfSteal health every 2 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
AccuracySec=30s
Persistent=true
Unit=xenonet-steal-watch.service

[Install]
WantedBy=timers.target
"""
    sftp_write(c, "/etc/systemd/system/xenonet-steal-watch.service", steal_watch_unit)
    sftp_write(c, "/etc/systemd/system/xenonet-steal-watch.timer", steal_watch_timer)

    # Hop Reality canary: TCP :8443 alone is not enough — need VLESS+Reality handshake.
    hop_watch_unit = f"""[Unit]
Description=XENO.net hop Reality canary (:8443 → SelfSteal)
After=network-online.target xeno-relay.service xeno-steal-nl.service

[Service]
Type=oneshot
WorkingDirectory={app}
EnvironmentFile={conf}/bridge.env
EnvironmentFile={conf}/bot.env
ExecStart={py} -m diag.hop_watch
Nice=10
"""
    hop_watch_timer = """[Unit]
Description=XENO.net hop Reality canary every 3 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=3min
AccuracySec=30s
Persistent=true
Unit=xenonet-hop-watch.service

[Install]
WantedBy=timers.target
"""
    sftp_write(c, "/etc/systemd/system/xenonet-hop-watch.service", hop_watch_unit)
    sftp_write(c, "/etc/systemd/system/xenonet-hop-watch.timer", hop_watch_timer)

    # RU→NL path canary (proves entry nl-exit; local hop canary is not enough).
    ru_hop_watch_unit = f"""[Unit]
Description=XENO.net RU→NL hop path canary
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={app}
EnvironmentFile={conf}/bridge.env
EnvironmentFile={conf}/bot.env
EnvironmentFile={conf}/ru-ssh.env
ExecStart={py} -m diag.ru_hop_watch
Nice=10
"""
    ru_hop_watch_timer = """[Unit]
Description=XENO.net RU→NL hop path canary every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true
Unit=xenonet-ru-hop-watch.service

[Install]
WantedBy=timers.target
"""
    sftp_write(c, "/etc/systemd/system/xenonet-ru-hop-watch.service", ru_hop_watch_unit)
    sftp_write(c, "/etc/systemd/system/xenonet-ru-hop-watch.timer", ru_hop_watch_timer)

    # Harden live steal unit if present (Restart=always).
    run(
        c,
        r"""
set -euo pipefail
unit=/etc/systemd/system/xeno-steal-nl.service
if [[ -f "$unit" ]]; then
  grep -q '^Restart=always' "$unit" || sed -i 's/^Restart=.*/Restart=always/' "$unit"
  grep -q '^RestartSec=' "$unit" || sed -i '/^Restart=/a RestartSec=3' "$unit"
  grep -q '^StartLimitIntervalSec=0' "$unit" || sed -i '/^\[Unit\]/a StartLimitIntervalSec=0' "$unit"
fi
""",
        check=False,
    )

    expiry_unit = f"""[Unit]
Description=XENO.net soft expiry reminders (3d / 1d)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={app}
EnvironmentFile={conf}/bridge.env
EnvironmentFile={conf}/bot.env
ExecStart={venv}/bin/python {app}/expiry_nudge.py
Nice=10
"""
    expiry_timer = """[Unit]
Description=XENO.net expiry nags daily 10:15 UTC

[Timer]
OnCalendar=*-*-* 10:15:00
Persistent=true
Unit=xenonet-expiry-nudge.service

[Install]
WantedBy=timers.target
"""
    sftp_write(c, "/etc/systemd/system/xenonet-expiry-nudge.service", expiry_unit)
    sftp_write(c, "/etc/systemd/system/xenonet-expiry-nudge.timer", expiry_timer)

    logrotate_src = ROOT / "configs" / "logrotate-xeno.conf"
    if logrotate_src.exists():
        sftp_write(c, "/etc/logrotate.d/xeno", logrotate_src.read_text(encoding="utf-8"))

    run(c, "mkdir -p /var/log/xeno/digests/daily /var/log/xeno/digests/weekly /var/log/xeno/digests/monthly /var/log/xeno/digests/smoke")
    run(c, "ufw allow 2080/tcp comment 'xeno-sub-nl' || true; ufw reload || true", check=False)
    run(
        c,
        "systemctl daemon-reload && "
        "systemctl enable xenonet-bot xenonet-sub "
        "xenonet-diag-collect.timer xenonet-diag-digest.timer xenonet-diag-smoke.timer "
        "xenonet-expiry-nudge.timer xenonet-steal-watch.timer xenonet-hop-watch.timer "
        "xenonet-ru-hop-watch.timer && "
        "systemctl restart xenonet-sub && "
        "systemctl restart xenonet-bot && "
        "systemctl restart xenonet-diag-collect.timer && "
        "systemctl restart xenonet-diag-digest.timer && "
        "systemctl restart xenonet-diag-smoke.timer && "
        "systemctl restart xenonet-expiry-nudge.timer && "
        "systemctl restart xeno-steal-nl || true; "
        "systemctl restart xenonet-steal-watch.timer || true; "
        "systemctl restart xenonet-hop-watch.timer || true; "
        "systemctl restart xenonet-ru-hop-watch.timer || true",
    )
    run(
        c,
        "systemctl is-active xenonet-bot xenonet-sub xeno-bot x-ui xeno-relay "
        "xenonet-diag-collect.timer xenonet-diag-digest.timer xenonet-diag-smoke.timer "
        "xenonet-expiry-nudge.timer xenonet-steal-watch.timer xenonet-hop-watch.timer "
        "xenonet-ru-hop-watch.timer || true",
        check=False,
    )
    # First hop canary + RU path canary + smoke + digest via systemd units (they load EnvironmentFiles).
    # Bare `python -m diag.hop_watch` without bridge.env → false missing_relay_env / hop_reality.
    run(
        c,
        "systemctl start xenonet-hop-watch.service; "
        "systemctl start xenonet-ru-hop-watch.service; "
        f"cd {app} && set -a && . {conf}/bridge.env && . {conf}/bot.env && . {conf}/ru-ssh.env && set +a && "
        f"{py} -m diag --smoke-only --alerts && "
        f"{py} -m diag --emit day --alerts",
        check=False,
    )
    run(c, "journalctl -u xenonet-bot -n 25 --no-pager", check=False)
    run(
        c,
        f"{venv}/bin/python - <<'PY'\n"
        "import json,urllib.request\n"
        f"tok=open('{conf}/bot.env').read().split('BOT_TOKEN=')[1].splitlines()[0].strip()\n"
        "print(json.load(urllib.request.urlopen('https://api.telegram.org/bot'+tok+'/getMe', timeout=30)))\n"
        "PY",
        check=False,
    )
    # NL → RU SSH check
    run(
        c,
        f"{venv}/bin/python - <<'PY'\n"
        "import paramiko\n"
        "from pathlib import Path\n"
        "def load(p):\n"
        "  d={}\n"
        "  for line in Path(p).read_text().splitlines():\n"
        "    if '=' in line and not line.startswith('#'):\n"
        "      k,v=line.split('=',1); d[k]=v.strip().strip(chr(39))\n"
        "  return d\n"
        f"e=load('{conf}/ru-ssh.env')\n"
        "c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())\n"
        "c.connect(e['RU_SSH_HOST'], username=e.get('RU_SSH_USER','root'), password=e['RU_SSH_PASS'], timeout=20, allow_agent=False, look_for_keys=False)\n"
        "_,o,_=c.exec_command('hostname; systemctl is-active xray')\n"
        "print(o.read().decode()); c.close()\n"
        "PY",
    )
    # Durable deploy ledger on box (no secrets).
    run(
        c,
        f"cd {app} && {py} -c \"from ops_events import KIND_DEPLOY, emit; emit(KIND_DEPLOY, host='nl', ok=True, root='{INSTALL_ROOT}')\"",
        check=False,
    )
    c.close()
    print(f"Deploy OK -> {INSTALL_ROOT} (product units only; sacred/sibling services untouched)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
