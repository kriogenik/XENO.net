#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/common.sh"
require_inventory

info "Checking SSH to all nodes"
failed=0
for host in nl-exit ru-bridge yc-whitelist; do
  ip="$(host_ip "$host")"
  echo -n "  $host ($ip) ... "
  if remote "$host" "echo ok" >/dev/null 2>&1; then
    echo "OK"
  else
    echo "FAIL"
    failed=1
  fi
done

[[ "$failed" -eq 0 ]] || die "SSH failed for one or more hosts"
info "All hosts reachable"
