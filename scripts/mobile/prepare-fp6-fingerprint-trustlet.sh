#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Verify and stage the exact manufacturer-delivered FP6 FocalTech trusted
# application for an explicitly authorized lab test. This script is offline:
# it does not contact, inspect, or modify a phone and never loads the TA.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

input_dir=${1:?usage: prepare-fp6-fingerprint-trustlet.sh INPUT_DIR OUTPUT_DIR}
output_dir=${2:?usage: prepare-fp6-fingerprint-trustlet.sh INPUT_DIR OUTPUT_DIR}

die() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

hash_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  else
    shasum -a 256 | awk '{print $1}'
  fi
}

file_size() {
  if stat -f '%z' "$1" >/dev/null 2>&1; then
    stat -f '%z' "$1"
  else
    stat -c '%s' "$1"
  fi
}

[ -d "$input_dir" ] || die "input directory not found: $input_dir"
[ ! -e "$output_dir" ] || die "refusing to overwrite: $output_dir"

names=(
  focal64.b00 focal64.b01 focal64.b02 focal64.b03 focal64.b04
  focal64.b05 focal64.b06 focal64.b07 focal64.b08 focal64.mdt
)
sizes=(
  "$FP6_FINGERPRINT_TRUSTLET_B00_SIZE"
  "$FP6_FINGERPRINT_TRUSTLET_B01_SIZE"
  "$FP6_FINGERPRINT_TRUSTLET_B02_SIZE"
  "$FP6_FINGERPRINT_TRUSTLET_B03_SIZE"
  "$FP6_FINGERPRINT_TRUSTLET_B04_SIZE"
  "$FP6_FINGERPRINT_TRUSTLET_B05_SIZE"
  "$FP6_FINGERPRINT_TRUSTLET_B06_SIZE"
  "$FP6_FINGERPRINT_TRUSTLET_B07_SIZE"
  "$FP6_FINGERPRINT_TRUSTLET_B08_SIZE"
  "$FP6_FINGERPRINT_TRUSTLET_MDT_SIZE"
)
hashes=(
  "$FP6_FINGERPRINT_TRUSTLET_B00_SHA256"
  "$FP6_FINGERPRINT_TRUSTLET_B01_SHA256"
  "$FP6_FINGERPRINT_TRUSTLET_B02_SHA256"
  "$FP6_FINGERPRINT_TRUSTLET_B03_SHA256"
  "$FP6_FINGERPRINT_TRUSTLET_B04_SHA256"
  "$FP6_FINGERPRINT_TRUSTLET_B05_SHA256"
  "$FP6_FINGERPRINT_TRUSTLET_B06_SHA256"
  "$FP6_FINGERPRINT_TRUSTLET_B07_SHA256"
  "$FP6_FINGERPRINT_TRUSTLET_B08_SHA256"
  "$FP6_FINGERPRINT_TRUSTLET_MDT_SHA256"
)

[ "${#names[@]}" -eq "$FP6_FINGERPRINT_TRUSTLET_SEGMENT_COUNT" ] ||
  die 'configured segment count differs'

for index in "${!names[@]}"; do
  source_file=$input_dir/${names[$index]}
  [ -f "$source_file" ] && [ ! -L "$source_file" ] ||
    die "missing regular input: ${names[$index]}"
  [ "$(file_size "$source_file")" = "${sizes[$index]}" ] ||
    die "size mismatch: ${names[$index]}"
  [ "$(hash_file "$source_file")" = "${hashes[$index]}" ] ||
    die "SHA-256 mismatch: ${names[$index]}"
done

bundle_sha=$(
  for index in "${!names[@]}"; do
    printf '%s  %s\n' "${hashes[$index]}" "${names[$index]}"
  done | hash_stdin
)
[ "$bundle_sha" = "$FP6_FINGERPRINT_TRUSTLET_BUNDLE_SHA256" ] ||
  die 'configured bundle digest differs'

mkdir -p "$(dirname -- "$output_dir")"
work_dir=$(mktemp -d "$(dirname -- "$output_dir")/.fp6-focal64.XXXXXX")
cleanup() {
  find "$work_dir" -depth -delete 2>/dev/null || true
}
trap cleanup EXIT INT TERM

firmware_dir=$work_dir/root/usr/lib/firmware
mkdir -p "$firmware_dir"
for index in "${!names[@]}"; do
  install -m 0644 "$input_dir/${names[$index]}" "$firmware_dir/${names[$index]}"
done

(
  cd "$work_dir/root"
  for index in "${!names[@]}"; do
    printf '%s  usr/lib/firmware/%s\n' "${hashes[$index]}" "${names[$index]}"
  done
) >"$work_dir/SHA256SUMS"

{
  printf 'SCOPE=fp6-fingerprint-focal64-trustlet-offline-staging\n'
  printf 'SOURCE=Fairphone-FP6-QREL-16.95.0-NON-HLOS\n'
  printf 'FACTORY_URL=%s\n' "$FP6_FINGERPRINT_FACTORY_URL"
  printf 'FACTORY_PUBLISHED_SHA256=%s\n' "$FP6_FINGERPRINT_FACTORY_PUBLISHED_SHA256"
  printf 'FACTORY_FULL_ARCHIVE_VERIFIED=false\n'
  printf 'NON_HLOS_SHA256=%s\n' "$FP6_FINGERPRINT_NON_HLOS_SHA256"
  printf 'NON_HLOS_CRC32=%s\n' "$FP6_FINGERPRINT_NON_HLOS_CRC32"
  printf 'TRUSTLET_NAME=%s\n' "$FP6_FINGERPRINT_TRUSTLET_NAME"
  printf 'SEGMENT_COUNT=%s\n' "$FP6_FINGERPRINT_TRUSTLET_SEGMENT_COUNT"
  printf 'BUNDLE_SHA256=%s\n' "$bundle_sha"
  printf 'REDISTRIBUTABLE=false\n'
  printf 'SECURE_WORLD_ACCEPTED=false\n'
  printf 'BIOMETRIC_OPERATION_PERFORMED=false\n'
} >"$work_dir/manifest.env"

mv "$work_dir" "$output_dir"
trap - EXIT INT TERM
printf 'FP6 FocalTech trusted application staged offline: %s\n' "$output_dir"
