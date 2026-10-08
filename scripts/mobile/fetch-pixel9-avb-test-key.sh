#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Fetch the public AOSP AVB test key from an immutable Gitiles revision. The
# key is intentionally public test material. This script never contacts a
# phone and does not install the key in any device trust store.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

output_dir=${1:?usage: fetch-pixel9-avb-test-key.sh OUTPUT_DIR}
key=$output_dir/testkey_rsa4096.pem
public_key=$output_dir/testkey_rsa4096.avbpubkey
avbtool=${PIXEL9_AVBTOOL:-}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

for tool in awk base64 chmod curl mkdir python3 sha1sum sha256sum tr wc; do
  command -v "$tool" >/dev/null 2>&1 || die "missing AVB-input tool: $tool"
done
[ -n "$avbtool" ] || die 'PIXEL9_AVBTOOL must name the pinned avbtool.py'
[ -f "$avbtool" ] || die "pinned avbtool is absent: $avbtool"
[ "$(sha256sum "$avbtool" | awk '{print $1}')" = "$PIXEL9_AVBTOOL_SHA256" ] ||
  die 'avbtool.py differs from the pin'
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

mkdir -p "$output_dir"
curl -fsSL "$PIXEL9_AVB_URL/+/$PIXEL9_AVB_COMMIT/$PIXEL9_AVB_TESTKEY_PATH?format=TEXT" |
  base64 -d >"$key"
chmod 0600 "$key"
[ "$(wc -c <"$key" | tr -d '[:space:]')" = "$PIXEL9_AVB_TESTKEY_BYTES" ] ||
  die 'AOSP AVB test-key byte size differs from the pin'
[ "$(sha256sum "$key" | awk '{print $1}')" = "$PIXEL9_AVB_TESTKEY_SHA256" ] ||
  die 'AOSP AVB test-key SHA-256 differs from the pin'

python3 "$avbtool" extract_public_key --key "$key" --output "$public_key"
chmod 0644 "$public_key"
[ "$(wc -c <"$public_key" | tr -d '[:space:]')" = "$PIXEL9_AVB_TEST_PUBLIC_KEY_BYTES" ] ||
  die 'AOSP AVB test public-key byte size differs from the pin'
[ "$(sha256sum "$public_key" | awk '{print $1}')" = "$PIXEL9_AVB_TEST_PUBLIC_KEY_SHA256" ] ||
  die 'AOSP AVB test public-key SHA-256 differs from the pin'
[ "$(sha1sum "$public_key" | awk '{print $1}')" = "$PIXEL9_AVB_TEST_PUBLIC_KEY_SHA1" ] ||
  die 'AOSP AVB test public-key SHA-1 differs from the pin'

{
  printf 'LUMA_PIXEL9_AVB_TEST_INPUT_VERSION=1\n'
  printf 'SCOPE=public-aosp-test-key-off-device-only\n'
  printf 'AVB_COMMIT=%s\n' "$PIXEL9_AVB_COMMIT"
  printf 'AVBTOOL_SHA256=%s\n' "$PIXEL9_AVBTOOL_SHA256"
  printf 'TEST_KEY_BYTES=%s\n' "$PIXEL9_AVB_TESTKEY_BYTES"
  printf 'TEST_KEY_SHA256=%s\n' "$PIXEL9_AVB_TESTKEY_SHA256"
  printf 'TEST_PUBLIC_KEY_BYTES=%s\n' "$PIXEL9_AVB_TEST_PUBLIC_KEY_BYTES"
  printf 'TEST_PUBLIC_KEY_SHA256=%s\n' "$PIXEL9_AVB_TEST_PUBLIC_KEY_SHA256"
  printf 'TEST_PUBLIC_KEY_SHA1=%s\n' "$PIXEL9_AVB_TEST_PUBLIC_KEY_SHA1"
  printf 'PUBLIC_TEST_KEY=true\n'
  printf 'PRODUCTION_TRUST_ROOT=false\n'
  printf 'CUSTOM_KEY_INSTALLED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'Pinned public AOSP AVB test input: %s\n' "$output_dir"
