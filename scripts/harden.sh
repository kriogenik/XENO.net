#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/common.sh"
require_inventory

HOST="${1:-}"
[[ -n "$HOST" ]] || die "usage: $0 <nl-exit|ru-bridge|yc-whitelist>"

info "Hardening $HOST ($(host_ip "$HOST"))"
remote_bash "$HOST" <<EOF
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
timedatectl set-timezone ${TIMEZONE:-UTC} || true
apt-get update -y
apt-get install -y curl wget ca-certificates ufw fail2ban jq unzip openssl uuid-runtime
apt-get upgrade -y

# SSH hardening (keep pubkey auth; don't lock yourself out)
if [[ -d /root/.ssh ]] && grep -qE 'ssh-(ed25519|rsa)|ecdsa' /root/.ssh/authorized_keys 2>/dev/null; then
  sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
  systemctl reload ssh || systemctl reload sshd || true
fi

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

systemctl enable --now fail2ban
echo "harden done"
EOF

info "Hardening complete: $HOST"
