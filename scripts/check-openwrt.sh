#!/usr/bin/env bash
# Runs only on the Linux CI runner. No production signing key is available here.
set -euo pipefail

SITE=$(cd "${1:?Pass the signed site directory}" && pwd)
WORK=$(mktemp -d "${RUNNER_TEMP:-/tmp}/apk-repository-openwrt.XXXXXX")
server_pid=''
cleanup() {
  if [[ -n "$server_pid" ]]; then kill "$server_pid" 2>/dev/null || true; fi
  rm -rf -- "$WORK"
}
trap cleanup EXIT

# Fixed baseline from the official 25.12.0 x86/64 download manifest.
curl --fail --location --silent --show-error --retry 3 \
  https://downloads.openwrt.org/releases/25.12.0/targets/x86/64/openwrt-25.12.0-x86-64-rootfs.tar.gz \
  -o "$WORK/rootfs.tar.gz"
printf '%s  %s\n' 11c2f26b9d48c02cbdb3b63d499e1f6413ed62bcdbbea78937b12279b95acb1e "$WORK/rootfs.tar.gz" | sha256sum -c -
docker import "$WORK/rootfs.tar.gz" apk-repository-openwrt:check

uv run --locked python -m http.server 8765 --bind 127.0.0.1 --directory "$SITE" > "$WORK/http.log" 2>&1 &
server_pid=$!
for ((attempt = 1; attempt <= 20; attempt++)); do
  if curl --fail --silent http://127.0.0.1:8765/manifest.json -o /dev/null; then break; fi
  sleep 1
done
curl --fail --silent http://127.0.0.1:8765/manifest.json -o /dev/null

for channel in stable prerelease; do
  uv run --locked python - "$SITE/manifest.json" "$channel" "$WORK" > "$WORK/versions" <<'PY'
import json, sys
from pathlib import Path
manifest = json.load(open(sys.argv[1]))
records = [p for p in manifest["packages"] if p["channel"] == sys.argv[2] and p["arch"] == "x86_64"]
Path(sys.argv[3], "install-specs").write_text("".join(p["name"] + "@apk_repository=" + p["version"] + "\n" for p in records))
Path(sys.argv[3], "package-names").write_text("".join(p["name"] + "\n" for p in records))
core = next((p for p in records if p["name"] == "sing-box"), None)
print(core["release_tag"].removeprefix("v") if core else "")
record = next(p for p in manifest["bootstrap"] if p["channel"] == sys.argv[2] and p["arch"] == "x86_64")
print(record["file"])
PY
  mapfile -t versions < "$WORK/versions"
  docker run --rm --network host -v "$SITE:/feed:ro" -v "$WORK:/checks:ro" \
    -e "CHANNEL=$channel" -e "CORE_UPSTREAM=${versions[0]}" -e "BOOTSTRAP=${versions[1]}" \
    apk-repository-openwrt:check /bin/sh -eu -c '
      mkdir -p /etc/apk/keys /etc/apk/repositories.d /var/lock
      cp /feed/apk/apk-repository.pem /etc/apk/keys/apk-repository.pem
      apk add --no-scripts "/feed/$BOOTSTRAP"
      test -s /etc/apk/repositories.d/apk-repository.list
      printf "@apk_repository http://127.0.0.1:8765/apk/%s/x86_64/packages.adb\n" "$CHANNEL" > /etc/apk/repositories.d/apk-repository.list
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
      apk add --no-scripts -u "$@"
      set --
      while IFS= read -r name; do set -- "$@" "$name"; done < /checks/package-names
      apk del --no-scripts "$@" "apk-repository-$CHANNEL"
      test -s /etc/apk/repositories.d/distfeeds.list
    '
done
