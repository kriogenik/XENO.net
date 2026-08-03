#!/usr/bin/env python3
"""Open UFW :80 precisely (avoid matching 2080), issue LE cert, set SUB HTTPS env."""
from __future__ import annotations

import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
INSTALL = "/etc/runaway/xeno.net"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v.strip().strip("'").strip('"')
    return env


def resolve_domain() -> str:
    """NL public hostname from local secrets (never hardcode live domain in git)."""
    for path in (SECRETS / "bridge.env", SECRETS / "bot.env", ROOT / "inventory" / "hosts.local.env"):
        env = load_env(path)
        for key in ("NL_DOMAIN", "NL_PUBLIC_HOST", "DOMAIN"):
            val = (env.get(key) or "").strip()
            if val and "example.com" not in val and "CHANGE" not in val.upper():
                # Prefer explicit NL_* over bare DOMAIN when DOMAIN is apex
                if key == "DOMAIN" and not val.startswith("nl."):
                    continue
                return val
        # SUB_PUBLIC_BASE=https://host:2080 → host
        base = (env.get("SUB_PUBLIC_BASE") or "").strip()
        if base.startswith("https://"):
            host = base[len("https://") :].split("/")[0].split(":")[0]
            if host:
                return host
    raise RuntimeError(
        "Set NL_DOMAIN (or NL_PUBLIC_HOST) in secrets/bridge.env before enabling sub HTTPS"
    )


def run(c: paramiko.SSHClient, cmd: str, check: bool = True) -> str:
    print(" $", cmd[:220].replace("\n", " "))
    _, o, e = c.exec_command(cmd, timeout=600)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        sys.stdout.buffer.write((out.rstrip()[:4000] + "\n").encode("utf-8", "replace"))
    if check and code != 0:
        raise RuntimeError(f"failed ({code}): {cmd}\n{err[:2000]}")
    return out


def upsert_remote_env(c: paramiko.SSHClient, remote: str, keys: dict[str, str]) -> None:
    sftp = c.open_sftp()
    with sftp.file(remote, "r") as f:
        text = f.read().decode("utf-8", "replace")
    lines = text.splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if not line.strip() or line.strip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        k = line.split("=", 1)[0].strip()
        if k in keys:
            out.append(f"{k}={keys[k]}")
            seen.add(k)
        else:
            out.append(line)
    for k, v in keys.items():
        if k not in seen:
            out.append(f"{k}={v}")
    data = "\n".join(out).rstrip() + "\n"
    with sftp.file(remote, "w") as f:
        f.write(data)
    sftp.close()
    print("  upsert", remote)


def main() -> int:
    domain = resolve_domain()
    cert = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
    key = f"/etc/letsencrypt/live/{domain}/privkey.pem"
    base = f"https://{domain}:2080"

    nl = load_env(SECRETS / "nl-access.env")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        nl.get("NL_EXIT_IP") or nl.get("NL_SSH_HOST"),
        username=nl.get("NL_SSH_USER", "root"),
        password=nl["NL_SSH_PASS"],
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )

    # Exact :80 — do NOT grep '80/tcp' (matches 2080).
    run(
        c,
        "ufw status | grep -E '(^|[[:space:]])80/tcp[[:space:]]' >/dev/null "
        "|| ufw allow 80/tcp comment 'xeno-acme-http01'",
        check=False,
    )
    run(c, "ufw reload || true", check=False)
    run(c, "ufw status | grep -E '(^|[[:space:]])80/tcp' || true", check=False)

    have = run(c, f"test -f {cert} && test -f {key} && echo YES || echo NO", check=False).strip()
    if have != "YES":
        run(
            c,
            "certbot certonly --standalone --non-interactive --agree-tos "
            f"--register-unsafely-without-email -d {domain} "
            "--preferred-challenges http",
        )
    else:
        print("cert already present")

    keys = {
        "SUB_PUBLIC_BASE": base,
        "SUB_TLS_CERT": cert,
        "SUB_TLS_KEY": key,
    }
    upsert_remote_env(c, f"{INSTALL}/config/bot.env", keys)
    upsert_remote_env(c, f"{INSTALL}/config/bridge.env", keys)

    hook = "#!/bin/bash\nsystemctl try-restart xenonet-sub.service || true\n"
    run(c, "mkdir -p /etc/letsencrypt/renewal-hooks/deploy")
    sftp = c.open_sftp()
    with sftp.file("/etc/letsencrypt/renewal-hooks/deploy/xenonet-sub.sh", "w") as f:
        f.write(hook)
    sftp.close()
    run(c, "chmod 755 /etc/letsencrypt/renewal-hooks/deploy/xenonet-sub.sh")
    run(c, "systemctl enable --now certbot.timer 2>/dev/null || true", check=False)
    run(c, f"openssl x509 -in {cert} -noout -subject -dates")
    print("SUB_PUBLIC_BASE", base)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
