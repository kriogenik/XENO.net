#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/common.sh"
source "$ROOT_DIR/scripts/lib/install-xray.sh"

require_inventory
"$ROOT_DIR/scripts/gen-secrets.sh"
load_secrets
export_render_env

info "Deploying nl-exit: Xray data plane + 3x-ui panel"

# --- Xray standalone (authoritative proxy config) ---
tmp="$(mktemp)"
render_template "$CONFIGS_DIR/xray/nl-exit-standalone.json.template" "$tmp"
install_xray_remote nl-exit
scp_to nl-exit "$tmp" /usr/local/etc/xray/config.json
scp_to nl-exit "$CONFIGS_DIR/systemd/xray.service" /etc/systemd/system/xray.service
rm -f "$tmp"

remote_bash nl-exit <<EOF
set -euo pipefail
# Avoid port clash: 3x-ui will NOT bind 443/8443; only our xray does.
systemctl daemon-reload
systemctl enable xray
systemctl restart xray
systemctl --no-pager -l status xray | head -n 20

# UFW: client + relay (restricted)
ufw allow ${CLIENT_PORT}/tcp comment 'xeno-client'
ufw allow from ${RU_BRIDGE_IP} to any port ${RELAY_PORT} proto tcp comment 'xeno-relay-ru'
ufw allow from ${YC_WHITELIST_IP} to any port ${RELAY_PORT} proto tcp comment 'xeno-relay-yc'
ufw reload || true
EOF

# --- 3x-ui for admin UI (high port, secret path); does not own 443 ---
info "Installing 3x-ui panel on nl-exit (management only)"
remote_bash nl-exit <<EOF
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! command -v x-ui >/dev/null 2>&1; then
  # Non-interactive-ish install; may still prompt — use yes
  printf 'n\n' | bash <(curl -fsSL https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh) || \
    bash <(curl -fsSL https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh) <<INSTALL_ANSWERS
n
INSTALL_ANSWERS
fi

# Configure panel listen settings if CLI exists
if command -v x-ui >/dev/null 2>&1; then
  x-ui setting -username "${PANEL_USER}" -password "${PANEL_PASS}" || true
  x-ui setting -port "${PANEL_PORT}" || true
  x-ui setting -webBasePath "/${PANEL_PATH}/" || true
  # Stop embedded xray conflict if 3x-ui tries to bind same ports:
  # Keep panel web UI; disable its xray by stopping and preferring our systemd xray.
  systemctl restart x-ui || true
  sleep 2
  # Ensure our xray is the one on 443/8443
  systemctl restart xray
fi

ufw allow ${PANEL_PORT}/tcp comment 'xeno-3xui'
ufw reload || true
echo "PANEL=http://$(curl -4 -fsSL ifconfig.co 2>/dev/null || hostname -I | awk '{print \$1}'):${PANEL_PORT}/${PANEL_PATH}/"
EOF

cat > "$SECRETS_DIR/panel.url" <<EOF
http://${NL_EXIT_IP}:${PANEL_PORT}/${PANEL_PATH}/
user: ${PANEL_USER}
pass: ${PANEL_PASS}
note: open via SSH tunnel preferred — panel manages users optionally; data plane is systemd xray
EOF

info "nl-exit deployed"
info "Panel creds: secrets/panel.env and secrets/panel.url"
info "Data plane: systemd xray on :${CLIENT_PORT} (direct) and :${RELAY_PORT} (relay)"
