#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Convert a verified, architecture-specific official Waydroid archive pair
# into one coherent raw image directory for the rollback-capable deployer.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
arch=${1:-$(uname -m)}
manifest="$repo_root/config/android/images-$arch.env"
[ -r "$manifest" ] || {
  printf 'error: no pinned Android images for %s\n' "$arch" >&2
  exit 1
}
# shellcheck disable=SC1090
. "$manifest"

cache=${LUMA_ANDROID_IMAGE_CACHE:-$repo_root/build/cache/android/$arch}
output=${LUMA_ANDROID_PREPARED_IMAGES:-$repo_root/build/android/pinned/$arch/images}

for tool in mktemp sha256sum unzip wc; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: missing image preparation tool: %s\n' "$tool" >&2
    exit 1
  }
done

verify_archive() {
  path=$1 digest=$2 size=$3
  [ -f "$path" ] && \
    [ "$(wc -c <"$path" | tr -d '[:space:]')" = "$size" ] && \
    printf '%s  %s\n' "$digest" "$path" | sha256sum -c - >/dev/null
}

system_archive="$cache/$LUMA_ANDROID_SYSTEM_FILENAME"
vendor_archive="$cache/$LUMA_ANDROID_VENDOR_FILENAME"
verify_archive "$system_archive" "$LUMA_ANDROID_SYSTEM_SHA256" \
  "$LUMA_ANDROID_SYSTEM_SIZE"
verify_archive "$vendor_archive" "$LUMA_ANDROID_VENDOR_SHA256" \
  "$LUMA_ANDROID_VENDOR_SIZE"

if [ -r "$output/SHA256SUMS" ] && (
  cd "$output"
  sha256sum --check --strict SHA256SUMS >/dev/null
); then
  printf 'Prepared Android images already verified: %s\n' "$output"
  exit 0
fi
[ ! -e "$output" ] || {
  printf 'error: refusing unverified existing output: %s\n' "$output" >&2
  exit 1
}

install -d -m 0755 "$(dirname -- "$output")"
stage=$(mktemp -d "${output}.stage.XXXXXX")
cleanup() {
  rm -rf -- "$stage"
}
trap cleanup EXIT

unzip -p "$system_archive" system.img >"$stage/system.img"
unzip -p "$vendor_archive" vendor.img >"$stage/vendor.img"
[ -s "$stage/system.img" ] && [ -s "$stage/vendor.img" ]
(
  cd "$stage"
  sha256sum system.img vendor.img >SHA256SUMS
  sha256sum --check --strict SHA256SUMS
)
chmod 0644 "$stage/system.img" "$stage/vendor.img" "$stage/SHA256SUMS"
mv "$stage" "$output"
trap - EXIT

printf 'Prepared verified Android images: %s\n' "$output"
