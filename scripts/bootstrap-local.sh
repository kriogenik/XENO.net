#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/common.sh"

info "Checking local dependencies"
missing=0
for cmd in ssh scp curl python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    warn "missing: $cmd"
    missing=1
  else
    echo "  ok: $cmd"
  fi
done

if [[ "$missing" -ne 0 ]]; then
  die "Install missing tools (on Windows use WSL2 Ubuntu)"
fi

if [[ ! -f "$INVENTORY_ENV" ]]; then
  die "missing inventory/hosts.env"
fi

echo
info "Inventory file present: $INVENTORY_ENV"
echo "Fill CHANGE_ME_* IPs after ordering servers — see docs/ops/servers.md"
echo "Then run: ./scripts/check-ssh.sh"
