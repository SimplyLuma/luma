#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build and test the inert FP6 QSEECom transport bridge. This script never
# contacts a phone, loads a trusted application, or handles biometric data.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

usage() {
  printf 'Usage: %s OUTPUT_DIR [ARCHIVED_HAL_EVIDENCE]\n' "$0" >&2
  exit 2
}

[ "$#" -ge 1 ] && [ "$#" -le 2 ] || usage
output_dir=$1
hal_evidence=${2:-}
source_dir=$repo_root/src/fp6-fingerprint-qsee-bridge

[ ! -e "$output_dir" ] || {
  printf 'Refusing to overwrite existing output: %s\n' "$output_dir" >&2
  exit 1
}
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || {
  printf 'Run this build inside the pinned Linux AArch64 builder.\n' >&2
  exit 1
}

mkdir -p "$(dirname -- "$output_dir")"

hash_file() {
  sha256sum "$1" | awk '{print $1}'
}

work_dir=$(mktemp -d "$(dirname -- "$output_dir")/.fp6-qsee-bridge.XXXXXX")
cleanup() {
  if [ -d "$work_dir" ]; then
    find "$work_dir" -depth -delete
  fi
}
trap cleanup EXIT INT TERM

cp -a "$source_dir/." "$work_dir/source/"
make -C "$work_dir/source" clean
make -C "$work_dir/source" check
make -C "$work_dir/source" all

mapfile -t exports < <(
  nm -D --defined-only --format=posix "$work_dir/source/libQSEEComAPI.so" |
    awk '$2 ~ /^[Tt]$/ {sub(/@@.*/, "", $1); print $1}' | LC_ALL=C sort -u
)
expected_exports=(QSEECom_send_cmd QSEECom_shutdown_app QSEECom_start_app)
[ "${exports[*]}" = "${expected_exports[*]}" ] || {
  printf 'Unexpected bridge exports: %s\n' "${exports[*]}" >&2
  exit 1
}

if [ -n "$hal_evidence" ]; then
  [ -f "$hal_evidence" ] || {
    printf 'Missing archived HAL evidence: %s\n' "$hal_evidence" >&2
    exit 1
  }
  hal_sha=$(hash_file "$hal_evidence")
  case "$hal_sha" in
    "$FP6_FINGERPRINT_STOCK_HAL_SHA256"|"$FP6_FINGERPRINT_PUBLISHED_HAL_SHA256") ;;
    *)
      printf 'Archived/published HAL evidence SHA-256 differs.\n' >&2
      exit 1
      ;;
  esac
  mapfile -t imports < <(
    objdump -T "$hal_evidence" |
      awk '$3 == "*UND*" && $NF ~ /^QSEECom_/ {print $NF}' |
      LC_ALL=C sort -u
  )
  [ "${imports[*]}" = "${expected_exports[*]}" ] || {
    printf 'Archived HAL QSEECom imports differ: %s\n' "${imports[*]}" >&2
    exit 1
  }
fi

mkdir -p "$work_dir/output/linux-aarch64" "$work_dir/output/tests"
install -m 0755 "$work_dir/source/libQSEEComAPI.so" \
  "$work_dir/output/linux-aarch64/libQSEEComAPI.so"
install -m 0755 "$work_dir/source/test-qseecom-bridge" \
  "$work_dir/output/tests/test-qseecom-bridge"

android_ready=false
if [ -n "${ANDROID_CC:-}" ]; then
  [ "$(basename -- "$ANDROID_CC")" = \
    "aarch64-linux-android${FP6_FINGERPRINT_ANDROID_API}-clang" ] || {
    printf 'ANDROID_CC must target the pinned AArch64 Android API %s.\n' \
      "$FP6_FINGERPRINT_ANDROID_API" >&2
    exit 1
  }
  ndk_root=$(CDPATH= cd -- "$(dirname -- "$ANDROID_CC")/../../../../.." && pwd)
  grep -Fqx "Pkg.Revision = $FP6_FINGERPRINT_ANDROID_NDK_VERSION" \
    "$ndk_root/source.properties" || {
    printf 'Android NDK revision differs.\n' >&2
    exit 1
  }
  make -C "$work_dir/source" android ANDROID_CC="$ANDROID_CC"
  readelf -h "$work_dir/source/libQSEEComAPI.android.so" |
    grep -Fq 'Machine:                           AArch64'
  mapfile -t android_needed < <(
    readelf -d "$work_dir/source/libQSEEComAPI.android.so" |
      sed -n 's/.*Shared library: \[\(.*\)\]/\1/p' | LC_ALL=C sort -u
  )
  [ "${android_needed[*]}" = 'libc.so libdl.so' ] || {
    printf 'Unexpected Android bridge dependencies: %s\n' \
      "${android_needed[*]}" >&2
    exit 1
  }
  mkdir -p "$work_dir/output/android-aarch64"
  install -m 0755 "$work_dir/source/libQSEEComAPI.android.so" \
    "$work_dir/output/android-aarch64/libQSEEComAPI.so"
  android_ready=true
fi

# A second build in a differently named directory must be byte-identical. This
# catches random temporary paths, timestamps, and non-deterministic build IDs.
cp -a "$source_dir/." "$work_dir/source-recheck/"
make -C "$work_dir/source-recheck" clean
make -C "$work_dir/source-recheck" check
make -C "$work_dir/source-recheck" all
cmp "$work_dir/source/libQSEEComAPI.so" \
  "$work_dir/source-recheck/libQSEEComAPI.so"
cmp "$work_dir/source/test-qseecom-bridge" \
  "$work_dir/source-recheck/test-qseecom-bridge"
if [ "$android_ready" = true ]; then
  make -C "$work_dir/source-recheck" android ANDROID_CC="$ANDROID_CC"
  cmp "$work_dir/source/libQSEEComAPI.android.so" \
    "$work_dir/source-recheck/libQSEEComAPI.android.so"
fi

source_digest=$(
  cd "$source_dir"
  while IFS= read -r file; do
    printf '%s  %s\n' "$(hash_file "$file")" "$file"
  done < <(find . -maxdepth 1 -type f \
    ! -name 'libQSEEComAPI*.so' ! -name 'test-qseecom-bridge' | LC_ALL=C sort) |
    sha256sum | awk '{print $1}'
)

{
  printf 'SCOPE=fp6-fingerprint-qsee-transport-only\n'
  printf 'SOURCE_DIGEST_SHA256=%s\n' "$source_digest"
  printf 'LINUX_AARCH64_SHA256=%s\n' \
    "$(hash_file "$work_dir/output/linux-aarch64/libQSEEComAPI.so")"
  printf 'TRANSPORT_TEST_SHA256=%s\n' \
    "$(hash_file "$work_dir/output/tests/test-qseecom-bridge")"
  printf 'AOSP_QSEECOM_API_COMMIT=%s\n' \
    "$FP6_FINGERPRINT_QSEECOM_API_COMMIT"
  printf 'AOSP_QSEECOM_API_HEADER_SHA256=%s\n' \
    "$FP6_FINGERPRINT_QSEECOM_API_HEADER_SHA256"
  printf 'ARCHIVED_HAL_ABI_VERIFIED=%s\n' "$([ -n "$hal_evidence" ] && echo true || echo false)"
  if [ -n "$hal_evidence" ]; then
    printf 'HAL_EVIDENCE_SHA256=%s\n' "$hal_sha"
  fi
  printf 'ANDROID_BIONIC_ARTIFACT_READY=%s\n' "$android_ready"
  if [ "$android_ready" = true ]; then
    printf 'ANDROID_NDK_VERSION=%s\n' "$FP6_FINGERPRINT_ANDROID_NDK_VERSION"
    printf 'ANDROID_API=%s\n' "$FP6_FINGERPRINT_ANDROID_API"
    printf 'ANDROID_AARCH64_SHA256=%s\n' \
      "$(hash_file "$work_dir/output/android-aarch64/libQSEEComAPI.so")"
  fi
  printf 'TRUSTLET_INCLUDED=false\n'
  printf 'FINGERPRINT_PROTOCOL_INCLUDED=false\n'
  printf 'BIOMETRIC_OPERATION_PERFORMED=false\n'
  printf 'BYTE_REPRODUCIBLE=true\n'
} >"$work_dir/output/manifest.env"

mv "$work_dir/output" "$output_dir"
trap - EXIT INT TERM
find "$work_dir" -depth -delete
printf 'FP6 inert QSEE bridge bundle: %s\n' "$output_dir"
