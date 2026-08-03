#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/common.sh"

PUBLISH_REMOTE=1
if [[ "${1:-}" == "--local-only" ]]; then
  PUBLISH_REMOTE=0
  require_inventory_or_dummy
else
  require_inventory
fi

load_secrets
ensure_secrets_dir

[[ -n "${CLIENT_UUID:-}" && -n "${REALITY_PUBLIC_KEY:-}" && -n "${REALITY_SHORT_ID:-}" ]] \
  || die "Run ./scripts/gen-secrets.sh first (needs SSH) or ./scripts/dry-run.sh"

info "Building Happ multi-node subscription"
python3 "$ROOT_DIR/scripts/lib/build_subscription.py" --root "$ROOT_DIR"

if [[ "$PUBLISH_REMOTE" -eq 0 ]]; then
  info "Local-only mode: skipped publish to nl-exit"
  info "URL: $(tr -d '\n' < "$SECRETS_DIR/subscription.url")"
  exit 0
fi

load_secrets
SUB_TOKEN="${SUB_TOKEN:?}"
SUB_PORT="${SUB_PORT:-2096}"

info "Publishing subscription to nl-exit :${SUB_PORT}"
remote_bash nl-exit <<EOF
set -euo pipefail
mkdir -p /var/www/xeno-sub/${SUB_TOKEN}
EOF
scp_to nl-exit "$SECRETS_DIR/subscription.base64" "/var/www/xeno-sub/${SUB_TOKEN}/index.html"

remote_bash nl-exit <<EOF
set -euo pipefail
cat >/etc/systemd/system/xeno-sub.service <<'UNIT'
[Unit]
Description=xeno.net subscription HTTP
After=network.target

[Service]
Type=simple
WorkingDirectory=/var/www/xeno-sub
ExecStart=/usr/bin/python3 -m http.server ${SUB_PORT} --bind 0.0.0.0
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable xeno-sub
systemctl restart xeno-sub
ufw allow ${SUB_PORT}/tcp comment 'xeno-sub' || true
ufw reload || true
EOF

# Canonical URL
echo "http://${NL_EXIT_IP}:${SUB_PORT}/${SUB_TOKEN}/" > "$SECRETS_DIR/subscription.url"
info "Done. Import into Happ: $(tr -d '\n' < "$SECRETS_DIR/subscription.url")"
info "Raw links: secrets/subscription.txt"
