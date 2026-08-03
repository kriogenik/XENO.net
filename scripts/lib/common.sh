#!/usr/bin/env bash
# shellcheck disable=SC1091
set -euo pipefail

# When sourced from scripts/*.sh, caller already set ROOT_DIR to repo root.
if [[ -z "${ROOT_DIR:-}" ]]; then
  ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
INVENTORY_ENV="${ROOT_DIR}/inventory/hosts.env"
if [[ -f "${ROOT_DIR}/inventory/hosts.local.env" ]]; then
  INVENTORY_ENV="${ROOT_DIR}/inventory/hosts.local.env"
fi
SECRETS_DIR="${ROOT_DIR}/secrets"
CONFIGS_DIR="${ROOT_DIR}/configs"

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }
warn() { echo "WARN: $*" >&2; }

require_inventory() {
  [[ -f "$INVENTORY_ENV" ]] || die "missing $INVENTORY_ENV"
  # shellcheck disable=SC1090
  source "$INVENTORY_ENV"
  SSH_USER="${SSH_USER:-root}"
  SSH_KEY="${SSH_KEY/#\~/$HOME}"
  RELAY_PORT="${RELAY_PORT:-8443}"
  CLIENT_PORT="${CLIENT_PORT:-443}"
  REALITY_SNI="${REALITY_SNI:-www.cloudflare.com}"
  REALITY_DEST="${REALITY_DEST:-www.cloudflare.com:443}"
  DOMAIN="${DOMAIN:-}"
  TIMEZONE="${TIMEZONE:-UTC}"

  for var in NL_EXIT_IP RU_BRIDGE_IP YC_WHITELIST_IP; do
    val="${!var}"
    if [[ -z "$val" || "$val" == CHANGE_ME_* ]]; then
      die "$var not set in inventory (hosts.local.env или hosts.env) — см. docs/ops/servers.md"
    fi
  done
}

# Allow dry-run / local render without real IPs
require_inventory_or_dummy() {
  [[ -f "$INVENTORY_ENV" ]] || die "missing $INVENTORY_ENV"
  # shellcheck disable=SC1090
  source "$INVENTORY_ENV"
  SSH_USER="${SSH_USER:-root}"
  SSH_KEY="${SSH_KEY/#\~/$HOME}"
  RELAY_PORT="${RELAY_PORT:-8443}"
  CLIENT_PORT="${CLIENT_PORT:-443}"
  REALITY_SNI="${REALITY_SNI:-www.cloudflare.com}"
  REALITY_DEST="${REALITY_DEST:-www.cloudflare.com:443}"
  DOMAIN="${DOMAIN:-}"
  NL_EXIT_IP="${NL_EXIT_IP:-203.0.113.10}"
  RU_BRIDGE_IP="${RU_BRIDGE_IP:-203.0.113.20}"
  YC_WHITELIST_IP="${YC_WHITELIST_IP:-203.0.113.30}"
  if [[ "$NL_EXIT_IP" == CHANGE_ME_* ]]; then NL_EXIT_IP=203.0.113.10; fi
  if [[ "$RU_BRIDGE_IP" == CHANGE_ME_* ]]; then RU_BRIDGE_IP=203.0.113.20; fi
  if [[ "$YC_WHITELIST_IP" == CHANGE_ME_* ]]; then YC_WHITELIST_IP=203.0.113.30; fi
}

host_ip() {
  case "$1" in
    nl-exit) echo "$NL_EXIT_IP" ;;
    ru-bridge) echo "$RU_BRIDGE_IP" ;;
    yc-whitelist) echo "$YC_WHITELIST_IP" ;;
    *) die "unknown host: $1" ;;
  esac
}

ssh_cmd() {
  local -a opts=(-o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ConnectTimeout=15)
  if [[ -n "${SSH_KEY:-}" && -f "${SSH_KEY}" ]]; then
    opts+=(-i "$SSH_KEY")
  fi
  ssh "${opts[@]}" "$@"
}

scp_cmd() {
  local -a opts=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
  if [[ -n "${SSH_KEY:-}" && -f "${SSH_KEY}" ]]; then
    opts+=(-i "$SSH_KEY")
  fi
  scp "${opts[@]}" "$@"
}

remote() {
  local host="$1"; shift
  local ip
  ip="$(host_ip "$host")"
  ssh_cmd "${SSH_USER}@${ip}" "$@"
}

remote_bash() {
  local host="$1"
  remote "$host" bash -s
}

scp_to() {
  local host="$1" src="$2" dst="$3"
  local ip
  ip="$(host_ip "$host")"
  scp_cmd "$src" "${SSH_USER}@${ip}:${dst}"
}

ensure_secrets_dir() {
  mkdir -p "$SECRETS_DIR" "$SECRETS_DIR/backups"
}

load_secrets() {
  ensure_secrets_dir
  [[ -f "$SECRETS_DIR/reality.env" ]] && # shellcheck disable=SC1091
    source "$SECRETS_DIR/reality.env"
  [[ -f "$SECRETS_DIR/uuids.env" ]] && # shellcheck disable=SC1091
    source "$SECRETS_DIR/uuids.env"
  [[ -f "$SECRETS_DIR/panel.env" ]] && # shellcheck disable=SC1091
    source "$SECRETS_DIR/panel.env"
}

render_template() {
  local src="$1" dst="$2"
  python3 - "$src" "$dst" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
missing = []
import re
for key in re.findall(r"\{\{(\w+)\}\}", text):
    if key not in os.environ or os.environ[key] == "":
        missing.append(key)
if missing:
    sys.stderr.write("missing template vars: " + ", ".join(sorted(set(missing))) + "\n")
    sys.exit(1)
for k, v in os.environ.items():
    text = text.replace("{{" + k + "}}", v)
open(dst, "w", encoding="utf-8").write(text)
PY
}

export_render_env() {
  export NL_EXIT_IP RU_BRIDGE_IP YC_WHITELIST_IP
  export RELAY_PORT CLIENT_PORT REALITY_SNI REALITY_DEST
  export REALITY_PRIVATE_KEY REALITY_PUBLIC_KEY REALITY_SHORT_ID
  export CLIENT_UUID RELAY_UUID
}
