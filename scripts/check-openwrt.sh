#!/usr/bin/env bash
# Runs only on the Linux CI runner. No production signing key is available here.
set -euo pipefail

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
curl --fail --location --silent --show-error --retry 3 \
  "https://downloads.openwrt.org/releases/25.12.0/targets/$target/$image_file" \
  -o "$WORK/rootfs.tar.gz"
printf '%s  %s\n' "$image_sha256" "$WORK/rootfs.tar.gz" | sha256sum -c -
docker import "$WORK/rootfs.tar.gz" apk-repository-openwrt:check

uv run --locked python -m http.server 8765 --bind 127.0.0.1 --directory "$SITE" > "$WORK/http.log" 2>&1 &
server_pid=$!
for ((attempt = 1; attempt <= 20; attempt++)); do
  if curl --fail --silent http://127.0.0.1:8765/manifest.json -o /dev/null; then break; fi
  sleep 1
done
curl --fail --silent http://127.0.0.1:8765/manifest.json -o /dev/null

for channel in stable prerelease; do
  uv run --locked python - "$SITE/manifest.json" "$channel" "$WORK" "$ARCH" > "$WORK/versions" <<'PY'
import json, sys
from pathlib import Path
manifest = json.load(open(sys.argv[1]))
records = [p for p in manifest["packages"] if p["channel"] == sys.argv[2] and p["arch"] == sys.argv[4]]
assert records, "No packages for installation check"
Path(sys.argv[3], "install-specs").write_text("".join(p["name"] + "@lauyv=" + p["version"] + "\n" for p in records))
Path(sys.argv[3], "package-names").write_text("".join(p["name"] + "\n" for p in records))
core = next((p for p in records if p["name"] == "sing-box"), None)
print(core["release_tag"].removeprefix("v") if core else "")
record = next(p for p in manifest["bootstrap"] if p["channel"] == sys.argv[2] and p["arch"] == sys.argv[4])
print(record["file"])
PY
  mapfile -t versions < "$WORK/versions"
  docker run --rm --network host -v "$SITE:/feed:ro" -v "$WORK:/checks:ro" \
    -e "ARCH=$ARCH" -e "CHANNEL=$channel" -e "CORE_UPSTREAM=${versions[0]}" -e "BOOTSTRAP=${versions[1]}" \
    apk-repository-openwrt:check /bin/sh -eu -c '
      test "$(apk --print-arch)" = "$ARCH"
      mkdir -p /etc/apk/keys /etc/apk/repositories.d /var/lock
      cp /feed/apk/apk-repository.pem /etc/apk/keys/apk-repository.pem
      apk add --no-scripts "/feed/$BOOTSTRAP"
      test -s /etc/apk/repositories.d/apk-repository.list
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
      apk del --no-scripts "$@" "apk-repository-$CHANNEL"
      test -s /etc/apk/repositories.d/distfeeds.list
    '
done

# Use the two retained releases for real upgrades, without publishing historical APKs.
uv run --locked python - "$SITE/manifest.json" "$ARCH" "$TOOLS_DIR/apk" > "$WORK/upgrades" <<'PY'
import json
import subprocess
import sys

with open(sys.argv[1]) as stream:
    manifest = json.load(stream)
packages = {}
for record in manifest["packages"]:
    if record["arch"] == sys.argv[2]:
        packages.setdefault(record["name"], {})[record["channel"]] = record
for name, channels in packages.items():
    if set(channels) != {"stable", "prerelease"}:
        print(f"SKIP upgrade {name}: only one channel", file=sys.stderr)
        continue
    low, high = channels["stable"], channels["prerelease"]
    comparison = subprocess.run([sys.argv[3], "version", "-t", low["version"], high["version"]],
                                check=True, capture_output=True, text=True).stdout.strip()
    if comparison == "=":
        print(f"SKIP upgrade {name}: channels contain the same version", file=sys.stderr)
        continue
    if comparison == ">":
        low, high = high, low
    elif comparison != "<":
        raise ValueError("Unexpected APK version comparison: " + comparison)
    print(name, low["channel"], low["version"], high["channel"], high["version"])
PY

while read -r package old_channel old_version new_channel new_version; do
  docker run --rm --network host -v "$SITE:/feed:ro" \
    -e "ARCH=$ARCH" -e "PACKAGE=$package" \
    -e "OLD_CHANNEL=$old_channel" -e "OLD_VERSION=$old_version" \
    -e "NEW_CHANNEL=$new_channel" -e "NEW_VERSION=$new_version" \
    apk-repository-openwrt:check /bin/sh -eu -c '
      test "$(apk --print-arch)" = "$ARCH"
      mkdir -p /etc/apk/keys /etc/apk/repositories.d /var/lock
      cp /feed/apk/apk-repository.pem /etc/apk/keys/apk-repository.pem
      printf "@lauyv http://127.0.0.1:8765/apk/%s/%s/packages.adb\n" "$OLD_CHANNEL" "$ARCH" > /etc/apk/repositories.d/customfeeds.list
      apk update
      apk add --no-scripts "$PACKAGE@lauyv=$OLD_VERSION"
      apk info -e "$PACKAGE=$OLD_VERSION"
      printf "@lauyv http://127.0.0.1:8765/apk/%s/%s/packages.adb\n" "$NEW_CHANNEL" "$ARCH" > /etc/apk/repositories.d/customfeeds.list
      apk update
      apk add --no-scripts -u "$PACKAGE@lauyv=$NEW_VERSION"
      apk info -e "$PACKAGE=$NEW_VERSION"
      if apk info -e "$PACKAGE=$OLD_VERSION"; then
        echo "Old version remains installed" >&2
        exit 1
      fi
      echo "Upgraded $PACKAGE: $OLD_VERSION -> $NEW_VERSION ($ARCH)"
    '
done < "$WORK/upgrades"
