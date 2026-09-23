#!/usr/bin/env bash
# Run once on a trusted machine; never generate a new production key in a scheduled job.
set -euo pipefail

DESTINATION=${1:?Usage: bash scripts/generate-key.sh /secure/new-directory}
[[ ! -e "$DESTINATION" ]] || { echo 'Destination must not already exist.' >&2; exit 1; }
umask 077
mkdir -p "$DESTINATION"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out "$DESTINATION/apk-repository.key"
openssl pkey -in "$DESTINATION/apk-repository.key" -pubout -out "$DESTINATION/apk-repository.pem"
chmod 0644 "$DESTINATION/apk-repository.pem"
openssl dgst -sha256 "$DESTINATION/apk-repository.pem"
echo 'Store the private key securely and set APK_SIGNING_KEY in the repository-signing GitHub environment.'
