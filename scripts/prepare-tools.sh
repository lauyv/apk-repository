#!/usr/bin/env bash
set -euo pipefail

: "${TOOLS_DIR:?Set TOOLS_DIR}"
mkdir -p "$TOOLS_DIR"
TOOLS_DIR=$(cd "$TOOLS_DIR" && pwd)
WORK=$(mktemp -d "${RUNNER_TEMP:-/tmp}/apk-repository-tools.XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
uv run --locked python -c 'import json; c=json.load(open("repository.json"))["apk_tools"]; print(c["version"]); print(c["commit"])' > "$WORK/pin"
mapfile -t pin < "$WORK/pin"
mkdir "$WORK/source"
curl --fail --location --silent --show-error --retry 3 \
  "https://codeload.github.com/alpinelinux/apk-tools/tar.gz/${pin[1]}" -o "$WORK/source.tar.gz"
tar -xzf "$WORK/source.tar.gz" -C "$WORK/source" --strip-components=1
VERSION="${pin[0]}" meson setup "$WORK/source/build" "$WORK/source" \
  --buildtype=release -Ddefault_library=static \
  -Ddocs=disabled -Dhelp=disabled -Dlua=disabled -Dpython=disabled \
  -Dtests=disabled -Dzstd=enabled -Durl_backend=wget
meson compile -C "$WORK/source/build" -j 2
install -m 0755 "$WORK/source/build/src/apk" "$TOOLS_DIR/apk"
