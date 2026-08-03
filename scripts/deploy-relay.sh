#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/common.sh"
source "$ROOT_DIR/scripts/lib/install-xray.sh"

HOST="${1:-}"
[[ "$HOST" == "ru-bridge" || "$HOST" == "yc-whitelist" ]] \
  || die "usage: $0 <ru-bridge|yc-whitelist>"

require_inventory
"$ROOT_DIR/scripts/gen-secrets.sh"
load_secrets
export_render_env

info "Deploying relay $HOST ($(host_ip "$HOST"))"

tmp="$(mktemp)"
render_template "$CONFIGS_DIR/xray/relay.json.template" "$tmp"
install_xray_remote "$HOST"
scp_to "$HOST" "$tmp" /usr/local/etc/xray/config.json
scp_to "$HOST" "$CONFIGS_DIR/systemd/xray.service" /etc/systemd/system/xray.service
rm -f "$tmp"

remote_bash "$HOST" <<EOF
set -euo pipefail
systemctl daemon-reload
systemctl enable xray
systemctl restart xray
systemctl --no-pager -l status xray | head -n 20
ufw allow ${CLIENT_PORT}/tcp comment 'xeno-client'
ufw reload || true
# quick outbound check to NL relay port
timeout 5 bash -c 'cat < /dev/null > /dev/tcp/${NL_EXIT_IP}/${RELAY_PORT}' \
  && echo "tcp ${NL_EXIT_IP}:${RELAY_PORT} reachable" \
  || echo "WARN: cannot dial NL relay port yet"
EOF

info "Relay $HOST deployed"
