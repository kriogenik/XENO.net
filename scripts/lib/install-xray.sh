#!/usr/bin/env bash
# Install Xray binary + systemd on a remote host; stdin is unused.
# Usage via deploy scripts after scp config.
set -euo pipefail

install_xray_remote() {
  local host="$1"
  remote_bash "$host" <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
arch=$(uname -m)
case "$arch" in
  x86_64) a=64 ;;
  aarch64|arm64) a=arm64-v8a ;;
  *) echo "unsupported arch $arch" >&2; exit 1 ;;
esac
ver=$(curl -fsSL https://api.github.com/repos/XTLS/Xray-core/releases/latest | jq -r .tag_name)
tmpdir=$(mktemp -d)
cd "$tmpdir"
curl -fsSL -o xray.zip "https://github.com/XTLS/Xray-core/releases/download/${ver}/Xray-linux-${a}.zip"
unzip -qo xray.zip
install -m 755 xray /usr/local/bin/xray
mkdir -p /usr/local/etc/xray /usr/local/share/xray
# geoip/geosite
curl -fsSL -o /usr/local/share/xray/geoip.dat \
  https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat
curl -fsSL -o /usr/local/share/xray/geosite.dat \
  https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat
rm -rf "$tmpdir"
/usr/local/bin/xray version
EOF
}
