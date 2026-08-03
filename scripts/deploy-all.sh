#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/common.sh"

info "xeno.net full deploy"
require_inventory

"$ROOT_DIR/scripts/bootstrap-local.sh"
"$ROOT_DIR/scripts/check-ssh.sh"

for host in nl-exit ru-bridge yc-whitelist; do
  "$ROOT_DIR/scripts/harden.sh" "$host"
done

"$ROOT_DIR/scripts/deploy-nl.sh"
"$ROOT_DIR/scripts/deploy-relay.sh" ru-bridge
"$ROOT_DIR/scripts/deploy-relay.sh" yc-whitelist
"$ROOT_DIR/scripts/build-subscription.sh"
"$ROOT_DIR/scripts/verify.sh"

info "All done."
echo
echo "Next:"
echo "  1. Open secrets/subscription.url"
echo "  2. Add it in Happ (docs/happ.md)"
echo "  3. Test RU Bridge, then YC Whitelist, then NL Direct"
