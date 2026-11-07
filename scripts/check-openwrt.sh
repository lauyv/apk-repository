#!/usr/bin/env bash
# Runs only on the Linux CI runner. No production signing key is available here.
set -Eeuo pipefail
trap 'status=$?; echo "OpenWrt check failed at line $LINENO (exit $status)" >&2; exit "$status"' ERR

SITE=$(cd "${1:?Pass the signed site directory}" && pwd)
ARCH=${2:?Pass the OpenWrt package architecture}
case "$ARCH" in
  x86_64)
    target=x86/64
    image_file=openwrt-25.12.0-x86-64-rootfs.tar.gz
    image_sha256=11c2f26b9d48c02cbdb3b63d499e1f6413ed62bcdbbea78937b12279b95acb1e
    ;;
  aarch64_generic)
    target=armsr/armv8
    image_file=openwrt-25.12.0-armsr-armv8-rootfs.tar.gz
    image_sha256=d5e42b396d7f64697c65a884912107b49e05d2b2f2c00a251f94c44f8deef507
    ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
WORK=$(mktemp -d "${RUNNER_TEMP:-/tmp}/apk-repository-openwrt.XXXXXX")
server_pid=''
cleanup() {
  if [[ -n "$server_pid" ]]; then kill "$server_pid" 2>/dev/null || true; fi
  rm -rf -- "$WORK"
}
trap cleanup EXIT

# Fixed baselines from the official OpenWrt 25.12.0 download manifests.
cache_dir="build/cache/openwrt/$ARCH"
mkdir -p "$cache_dir"
rootfs="$cache_dir/$image_sha256.tar.gz"
if [[ ! -f "$rootfs" ]]; then
  curl --fail --location --silent --show-error --retry 3 \
    "https://downloads.openwrt.org/releases/25.12.0/targets/$target/$image_file" \
    -o "$WORK/rootfs.tar.gz"
  printf '%s  %s\n' "$image_sha256" "$WORK/rootfs.tar.gz" | sha256sum -c -
  mv "$WORK/rootfs.tar.gz" "$rootfs"
fi
# Check restored cache contents too, before Docker imports the filesystem.
printf '%s  %s\n' "$image_sha256" "$rootfs" | sha256sum -c -
docker import "$rootfs" apk-repository-openwrt:check

python3 -m http.server 8765 --bind 127.0.0.1 --directory "$SITE" > "$WORK/http.log" 2>&1 &
server_pid=$!
for ((attempt = 1; attempt <= 20; attempt++)); do
  if curl --fail --silent http://127.0.0.1:8765/manifest.json -o /dev/null; then break; fi
  sleep 1
done
if ! curl --fail --silent --show-error http://127.0.0.1:8765/manifest.json -o /dev/null; then
  cat "$WORK/http.log" >&2
  exit 1
fi

for channel in stable latest; do
echo "Checking installation: $ARCH / $channel"
python3 - "$SITE/manifest.json" "$WORK" "$ARCH" "$channel" > "$WORK/versions" <<'PY'
import json, sys
from pathlib import Path
manifest = json.load(open(sys.argv[1]))
records = [p for p in manifest["packages"] if p["arch"] == sys.argv[3] and p["channel"] == sys.argv[4]]
assert records, "No packages for installation check"
Path(sys.argv[2], "install-specs").write_text("".join(p["name"] + "@lauyv=" + p["version"] + "\n" for p in records))
Path(sys.argv[2], "package-names").write_text("".join(p["name"] + "\n" for p in records))
core = next((p for p in records if p["name"] == "sing-box"), None)
print(core["release_tag"].removeprefix("v") if core else "")

PY
core_upstream=$(cat "$WORK/versions")
docker run --rm --network host -v "$SITE:/feed:ro" -v "$WORK:/checks:ro" \
  -e "CHANNEL=$channel" -e "ARCH=$ARCH" -e "CORE_UPSTREAM=$core_upstream" \
  apk-repository-openwrt:check /bin/sh -eu -c '
    actual_arch=$(cat /etc/apk/arch)
    if [ "$actual_arch" != "$ARCH" ]; then
      echo "OpenWrt package architecture mismatch: expected=$ARCH actual=$actual_arch" >&2
      exit 1
    fi
    mkdir -p /etc/apk/keys /etc/apk/repositories.d /var/lock
    cp /feed/apk/apk-repository.pem /etc/apk/keys/apk-repository.pem
    printf "@lauyv http://127.0.0.1:8765/apk/%s/%s/packages.adb\n" "$CHANNEL" "$ARCH" > /etc/apk/repositories.d/apk-repository.list
    apk update
    set --
    while IFS= read -r spec; do set -- "$@" "$spec"; done < /checks/install-specs
    apk add --no-scripts "$@"
    if [ -n "$CORE_UPSTREAM" ]; then
      sing-box version | head -n 1 | grep -F -x "sing-box version $CORE_UPSTREAM"
      test -x /etc/init.d/sing-box
      test -f /etc/config/sing-box
    fi
    if grep -q -x luci-app-sing-box /checks/package-names; then
      test -x /usr/libexec/rpcd/luci.sing-box
    fi
    set --
    while IFS= read -r name; do set -- "$@" "$name"; done < /checks/package-names
    apk del --no-scripts "$@"
    test -s /etc/apk/repositories.d/distfeeds.list
  '
done
