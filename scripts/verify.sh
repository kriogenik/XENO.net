#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/common.sh"
require_inventory
load_secrets

info "Verifying cluster"

failed=0
for host in nl-exit ru-bridge yc-whitelist; do
  echo -n "  systemd xray on $host ... "
  if remote "$host" "systemctl is-active xray" 2>/dev/null | grep -q active; then
    echo "active"
  else
    echo "INACTIVE"
    failed=1
  fi
done

echo -n "  nl-exit listens ${CLIENT_PORT} ... "
if remote nl-exit "ss -lnt | grep -q ':${CLIENT_PORT} '" 2>/dev/null; then
  echo "yes"
else
  echo "NO"; failed=1
fi

echo -n "  nl-exit listens ${RELAY_PORT} ... "
if remote nl-exit "ss -lnt | grep -q ':${RELAY_PORT} '" 2>/dev/null; then
  echo "yes"
else
  echo "NO"; failed=1
fi

for host in ru-bridge yc-whitelist; do
  echo -n "  $host -> NL:${RELAY_PORT} ... "
  if remote "$host" "timeout 5 bash -c 'cat < /dev/null > /dev/tcp/${NL_EXIT_IP}/${RELAY_PORT}'" 2>/dev/null; then
    echo "ok"
  else
    echo "FAIL"; failed=1
  fi
done

if [[ -f "$SECRETS_DIR/subscription.url" ]]; then
  echo "  subscription.url: $(cat "$SECRETS_DIR/subscription.url")"
else
  warn "subscription.url missing — run ./scripts/build-subscription.sh"
  failed=1
fi

[[ "$failed" -eq 0 ]] || die "verification failed"
info "Verification passed (client E2E still needed in Happ — docs/happ.md)"
