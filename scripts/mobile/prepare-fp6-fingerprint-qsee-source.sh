#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Prepare an auditable, offline Milos kernel tree with the generic QSEECom
# transport. This script does not build, boot, contact a phone, or enable the
# fingerprint sensor node.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

usage() {
  printf 'Usage: %s INPUT_DIR OUTPUT_DIR\n' "$0" >&2
  printf 'INPUT_DIR must contain the pinned Milos kernel archive.\n' >&2
  exit 2
}

[ "$#" -eq 2 ] || usage
input_dir=$1
output_dir=$2
archive=$input_dir/linux-v${FP6_FINGERPRINT_KERNEL_VERSION}-milos.tar.gz
patch_dir=$repo_root/patches/linux-qseecom
driver_patch_dir=$repo_root/patches/linux-milos-fingerprint

[ -f "$archive" ] || {
  printf 'Missing pinned kernel archive: %s\n' "$archive" >&2
  exit 1
}
[ ! -e "$output_dir" ] || {
  printf 'Refusing to overwrite existing output: %s\n' "$output_dir" >&2
  exit 1
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

sha256_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  else
    shasum -a 256 | awk '{print $1}'
  fi
}

actual_archive_sha=$(sha256_file "$archive")
[ "$actual_archive_sha" = "$FP6_FINGERPRINT_KERNEL_ARCHIVE_SHA256" ] || {
  printf 'Kernel archive SHA-256 mismatch: %s\n' "$actual_archive_sha" >&2
  exit 1
}

patch_count=$(find "$patch_dir" -maxdepth 1 -type f -name '*.patch' | wc -l | tr -d ' ')
[ "$patch_count" = "$FP6_FINGERPRINT_QSEECOM_PATCH_COUNT" ] || {
  printf 'QSEECom patch-count mismatch: %s\n' "$patch_count" >&2
  exit 1
}
patchset_sha=$(
  cd "$patch_dir"
  while IFS= read -r patch; do
    printf '%s  %s\n' "$(sha256_file "$patch")" "$patch"
  done < <(find . -maxdepth 1 -type f -name '*.patch' | LC_ALL=C sort) |
    sha256_stdin
)
[ "$patchset_sha" = "$FP6_FINGERPRINT_QSEECOM_PATCHSET_SHA256" ] || {
  printf 'QSEECom patchset SHA-256 mismatch: %s\n' "$patchset_sha" >&2
  exit 1
}

driver_patch_count=$(find "$driver_patch_dir" -maxdepth 1 -type f -name '*.patch' | wc -l | tr -d ' ')
[ "$driver_patch_count" = "$FP6_FINGERPRINT_DRIVER_PATCH_COUNT" ] || {
  printf 'Fingerprint driver patch-count mismatch: %s\n' "$driver_patch_count" >&2
  exit 1
}
driver_patchset_sha=$(
  cd "$driver_patch_dir"
  while IFS= read -r patch; do
    printf '%s  %s\n' "$(sha256_file "$patch")" "$patch"
  done < <(find . -maxdepth 1 -type f -name '*.patch' | LC_ALL=C sort) |
    sha256_stdin
)
[ "$driver_patchset_sha" = "$FP6_FINGERPRINT_DRIVER_PATCHSET_SHA256" ] || {
  printf 'Fingerprint driver patchset SHA-256 mismatch: %s\n' "$driver_patchset_sha" >&2
  exit 1
}

mkdir -p "$(dirname -- "$output_dir")"
work_dir=$(mktemp -d "$(dirname -- "$output_dir")/.fp6-fingerprint-qsee.XXXXXX")
cleanup() {
  if [ -d "$work_dir" ]; then
    find "$work_dir" -depth -delete
  fi
}
trap cleanup EXIT INT TERM

tar -xzf "$archive" -C "$work_dir" --strip-components=1

while IFS= read -r patch; do
  git -C "$work_dir" apply --no-index --check "$patch"
  git -C "$work_dir" apply --no-index "$patch"
done < <(find "$patch_dir" -maxdepth 1 -type f -name '*.patch' | LC_ALL=C sort)

while IFS= read -r patch; do
  git -C "$work_dir" apply --no-index --check "$patch"
  git -C "$work_dir" apply --no-index "$patch"
done < <(find "$driver_patch_dir" -maxdepth 1 -type f -name '*.patch' | LC_ALL=C sort)

# The QSEE transport is opt-in.  The sensor overlay remains disabled and no
# proprietary TA or fingerprint payload is copied into the prepared tree.
scripts_config=$work_dir/scripts/config
[ -x "$scripts_config" ] || chmod +x "$scripts_config"
"$scripts_config" --file "$work_dir/arch/arm64/configs/defconfig" \
  --enable TEE \
  --enable QCOM_QSEECOM \
  --module TEE_QSEECOM \
  --enable QCOM_MDT_LOADER \
  --enable INPUT_FINGERPRINT \
  --module FINGER_FOCAL

mkdir -p "$work_dir/luma-evidence"
cp "$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-fingerprint.dtso" \
  "$work_dir/luma-evidence/"
{
  printf 'FP6_FINGERPRINT_KERNEL_VERSION=%s\n' "$FP6_FINGERPRINT_KERNEL_VERSION"
  printf 'FP6_FINGERPRINT_KERNEL_ARCHIVE_SHA256=%s\n' "$actual_archive_sha"
  printf 'FP6_FINGERPRINT_QSEECOM_UPSTREAM_COMMIT=%s\n' "$FP6_FINGERPRINT_QSEECOM_KERNEL_COMMIT"
  printf 'FP6_FINGERPRINT_QSEECOM_PATCH_COUNT=%s\n' "$patch_count"
  printf 'FP6_FINGERPRINT_QSEECOM_PATCHSET_SHA256=%s\n' "$patchset_sha"
  printf 'FP6_FINGERPRINT_DRIVER_COMMIT=%s\n' "$FP6_FINGERPRINT_DRIVER_COMMIT"
  printf 'FP6_FINGERPRINT_DRIVER_PATCH_COUNT=%s\n' "$driver_patch_count"
  printf 'FP6_FINGERPRINT_DRIVER_PATCHSET_SHA256=%s\n' "$driver_patchset_sha"
  printf 'FP6_FINGERPRINT_SENSOR_NODE_ENABLED=false\n'
  printf 'FP6_FINGERPRINT_PROPRIETARY_PAYLOADS_INCLUDED=false\n'
} >"$work_dir/luma-evidence/source-manifest.env"

mv "$work_dir" "$output_dir"
trap - EXIT INT TERM
printf 'Prepared QSEECom source tree: %s\n' "$output_dir"
printf 'Fingerprint sensor node remains disabled; no phone was accessed.\n'
