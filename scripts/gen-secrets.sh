#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/common.sh"
require_inventory
ensure_secrets_dir
load_secrets

info "Ensuring Reality keys and UUIDs"

gen_uuid() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen | tr '[:upper:]' '[:lower:]'
  else
    python3 -c 'import uuid; print(uuid.uuid4())'
  fi
}

if [[ -z "${CLIENT_UUID:-}" || -z "${RELAY_UUID:-}" ]]; then
  CLIENT_UUID="${CLIENT_UUID:-$(gen_uuid)}"
  RELAY_UUID="${RELAY_UUID:-$(gen_uuid)}"
  cat > "$SECRETS_DIR/uuids.env" <<EOF
CLIENT_UUID=$CLIENT_UUID
RELAY_UUID=$RELAY_UUID
EOF
  info "Wrote secrets/uuids.env"
fi

if [[ -z "${REALITY_PRIVATE_KEY:-}" || -z "${REALITY_PUBLIC_KEY:-}" || -z "${REALITY_SHORT_ID:-}" ]]; then
  info "Generating Reality keys on nl-exit (temporary xray binary)"
  KEYS="$(remote_bash nl-exit <<'EOF'
set -euo pipefail
tmpdir=$(mktemp -d)
cd "$tmpdir"
arch=$(uname -m)
case "$arch" in
  x86_64) a=64 ;;
  aarch64|arm64) a=arm64-v8a ;;
  *) echo "unsupported arch $arch" >&2; exit 1 ;;
esac
# Fetch latest Xray release asset
ver=$(curl -fsSL https://api.github.com/repos/XTLS/Xray-core/releases/latest | jq -r .tag_name)
curl -fsSL -o xray.zip "https://github.com/XTLS/Xray-core/releases/download/${ver}/Xray-linux-${a}.zip"
unzip -q xray.zip xray
./xray x25519
openssl rand -hex 4
rm -rf "$tmpdir"
EOF
)"
  # Output format from xray x25519 varies; parse PrivateKey / Password / PublicKey lines
  REALITY_PRIVATE_KEY="$(echo "$KEYS" | awk -F': ' '/Private/{print $2; exit}' | tr -d '\r')"
  REALITY_PUBLIC_KEY="$(echo "$KEYS" | awk -F': ' '/Public/{print $2; exit}' | tr -d '\r')"
  REALITY_SHORT_ID="$(echo "$KEYS" | tail -n1 | tr -d '\r')"
  [[ -n "$REALITY_PRIVATE_KEY" && -n "$REALITY_PUBLIC_KEY" && -n "$REALITY_SHORT_ID" ]] \
    || die "failed to parse Reality keys from remote output: $KEYS"
  cat > "$SECRETS_DIR/reality.env" <<EOF
REALITY_PRIVATE_KEY=$REALITY_PRIVATE_KEY
REALITY_PUBLIC_KEY=$REALITY_PUBLIC_KEY
REALITY_SHORT_ID=$REALITY_SHORT_ID
EOF
  info "Wrote secrets/reality.env"
fi

if [[ -z "${PANEL_PORT:-}" ]]; then
  PANEL_PORT=$((20000 + RANDOM % 20000))
  PANEL_PATH="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
  PANEL_USER="xeno"
  PANEL_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  cat > "$SECRETS_DIR/panel.env" <<EOF
PANEL_PORT=$PANEL_PORT
PANEL_PATH=$PANEL_PATH
PANEL_USER=$PANEL_USER
PANEL_PASS=$PANEL_PASS
EOF
  info "Wrote secrets/panel.env"
fi

info "Secrets ready"
