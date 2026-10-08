#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Fetch and verify the exact official recovery artifacts selected for the
# inventoried Pixel 9. This script never communicates with a phone and refuses
# to proceed unless the operator separately confirms Google's download terms.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

output_root=${LUMA_PIXEL9_RECOVERY_DIR:-$repo_root/build/cache/recovery/pixel9/$PIXEL9_OBSERVED_BUILD_ID}
terms_accepted=${LUMA_PIXEL9_GOOGLE_DOWNLOAD_TERMS_ACCEPTED:-0}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$terms_accepted" = 1 ] || die 'Google download terms have not been explicitly accepted; review the official Pixel factory and full-OTA pages, then set LUMA_PIXEL9_GOOGLE_DOWNLOAD_TERMS_ACCEPTED=1 for this fetch only'
[ "$PIXEL9_FACTORY_IMAGE_SELECTED" = true ] || die 'no factory image is selected'
[ "$PIXEL9_FULL_OTA_SELECTED" = true ] || die 'no full OTA is selected'

for tool in curl df wc mv mkdir; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool is missing: $tool"
done

verify_sha256() {
  expected=$1
  path=$2
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$path" | sha256sum -c -
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$path" | shasum -a 256 -c -
  else
    die 'sha256sum or shasum is required'
  fi
}

mkdir -p "$output_root"
available_kib=$(df -Pk "$output_root" | awk 'NR == 2 {print $4}')
payload_bytes=0
[ -f "$output_root/$PIXEL9_FACTORY_IMAGE_FILENAME" ] || \
  payload_bytes=$((payload_bytes + PIXEL9_FACTORY_IMAGE_BYTES))
[ -f "$output_root/$PIXEL9_FULL_OTA_FILENAME" ] || \
  payload_bytes=$((payload_bytes + PIXEL9_FULL_OTA_BYTES))
required_kib=$(((payload_bytes + 1073741824 + 1023) / 1024))
[ "$available_kib" -ge "$required_kib" ] || \
  die "recovery set plus safety margin requires at least $((required_kib / 1048576)) GiB free; found $((available_kib / 1048576)) GiB"

fetch_one() {
  label=$1
  url=$2
  filename=$3
  expected_bytes=$4
  expected_sha256=$5
  destination="$output_root/$filename"
  partial="$destination.part"

  if [ -f "$destination" ]; then
    verify_sha256 "$expected_sha256" "$destination" || \
      die "$label exists but does not match the pinned SHA-256"
    printf '%s already present and verified: %s\n' "$label" "$destination"
    return
  fi

  curl -L --fail --show-error --continue-at - --output "$partial" "$url"
  actual_bytes=$(wc -c <"$partial" | tr -d ' ')
  [ "$actual_bytes" -eq "$expected_bytes" ] || \
    die "$label size mismatch: expected $expected_bytes bytes, received $actual_bytes"
  verify_sha256 "$expected_sha256" "$partial" || \
    die "$label does not match the pinned SHA-256"
  mv "$partial" "$destination"
  printf '%s fetched and verified: %s\n' "$label" "$destination"
}

fetch_one 'factory image' "$PIXEL9_FACTORY_IMAGE_URL" \
  "$PIXEL9_FACTORY_IMAGE_FILENAME" "$PIXEL9_FACTORY_IMAGE_BYTES" \
  "$PIXEL9_FACTORY_IMAGE_SHA256"
fetch_one 'full OTA' "$PIXEL9_FULL_OTA_URL" \
  "$PIXEL9_FULL_OTA_FILENAME" "$PIXEL9_FULL_OTA_BYTES" \
  "$PIXEL9_FULL_OTA_SHA256"

manifest="$output_root/luma-recovery-manifest.env"
manifest_partial="$manifest.part"
{
  printf 'LUMA_PIXEL9_RECOVERY_MANIFEST_VERSION=1\n'
  printf 'DEVICE=tokay\n'
  printf 'OBSERVED_BUILD_ID=%s\n' "$PIXEL9_OBSERVED_BUILD_ID"
  printf 'OBSERVED_SECURITY_PATCH=%s\n' "$PIXEL9_OBSERVED_SECURITY_PATCH"
  printf 'OBSERVED_BOOTLOADER=%s\n' "$PIXEL9_OBSERVED_BOOTLOADER"
  printf 'RECOVERY_REQUIRED_BASEBAND=%s\n' "$PIXEL9_RECOVERY_REQUIRED_BASEBAND"
  printf 'OBSERVED_SLOT_SUFFIX=%s\n' "$PIXEL9_OBSERVED_SLOT_SUFFIX"
  printf 'OBSERVED_VIRTUAL_AB=%s\n' "$PIXEL9_OBSERVED_VIRTUAL_AB"
  printf 'OBSERVED_SLOT_A_SUCCESSFUL=%s\n' "$PIXEL9_OBSERVED_SLOT_A_SUCCESSFUL"
  printf 'OBSERVED_SLOT_A_UNBOOTABLE=%s\n' "$PIXEL9_OBSERVED_SLOT_A_UNBOOTABLE"
  printf 'OBSERVED_SLOT_B_SUCCESSFUL=%s\n' "$PIXEL9_OBSERVED_SLOT_B_SUCCESSFUL"
  printf 'OBSERVED_SLOT_B_UNBOOTABLE=%s\n' "$PIXEL9_OBSERVED_SLOT_B_UNBOOTABLE"
  printf 'FACTORY_IMAGE_FILENAME=%s\n' "$PIXEL9_FACTORY_IMAGE_FILENAME"
  printf 'FACTORY_IMAGE_BYTES=%s\n' "$PIXEL9_FACTORY_IMAGE_BYTES"
  printf 'FACTORY_IMAGE_SHA256=%s\n' "$PIXEL9_FACTORY_IMAGE_SHA256"
  printf 'FACTORY_IMAGE_VERIFIED=true\n'
  printf 'FULL_OTA_FILENAME=%s\n' "$PIXEL9_FULL_OTA_FILENAME"
  printf 'FULL_OTA_BYTES=%s\n' "$PIXEL9_FULL_OTA_BYTES"
  printf 'FULL_OTA_SHA256=%s\n' "$PIXEL9_FULL_OTA_SHA256"
  printf 'FULL_OTA_VERIFIED=true\n'
  printf 'TERMS_ACCEPTED_BY_OPERATOR=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'INACTIVE_SLOT_REPAIR_AUTHORIZED=false\n'
  printf 'INACTIVE_SLOT_REPAIR_COMPLETED=%s\n' "$PIXEL9_INACTIVE_SLOT_REPAIR_COMPLETED"
  printf 'INACTIVE_SLOT_REPAIR_AUTHORIZATION_CONSUMED=%s\n' "$PIXEL9_INACTIVE_SLOT_REPAIR_AUTHORIZATION_CONSUMED"
  printf 'UNLOCK_AUTHORIZED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
  printf 'RELOCK_AUTHORIZED=false\n'
} >"$manifest_partial"
mv "$manifest_partial" "$manifest"

printf 'Pixel 9 recovery set is locally complete and verified: %s\n' "$output_root"
