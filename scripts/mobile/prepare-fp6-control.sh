#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Download and verify upstream control artifacts only. No Android platform
# tool is invoked and no phone is accessed.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-control/inputs.env"

build_dir=${LUMA_FP6_PHYSICAL_BUILD_DIR:-$repo_root/build/mobile/fp6-physical}
downloads_dir="$build_dir/downloads/postmarketos-$FP6_CONTROL_BUILD"
manifest="$build_dir/control-manifest.txt"

for tool in curl xz; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required control preparation tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    printf 'error: sha256sum or shasum is required\n' >&2
    exit 1
  fi
}

fetch_and_verify() {
  filename=$1
  expected=$2
  destination="$downloads_dir/$filename"
  if [ ! -f "$destination" ]; then
    partial="$destination.partial"
    curl --fail --location --continue-at - --proto '=https' --tlsv1.2 \
      --output "$partial" "$FP6_CONTROL_BASE_URL/$filename"
    actual=$(sha256_file "$partial")
    if [ "$actual" != "$expected" ]; then
      printf 'error: control artifact checksum mismatch; partial retained: %s\n' "$filename" >&2
      printf 'expected: %s\nactual:   %s\n' "$expected" "$actual" >&2
      exit 1
    fi
    mv "$partial" "$destination"
  fi

  actual=$(sha256_file "$destination")
  if [ "$actual" != "$expected" ]; then
    printf 'error: existing control artifact checksum mismatch: %s\n' "$filename" >&2
    printf 'expected: %s\nactual:   %s\n' "$expected" "$actual" >&2
    exit 1
  fi
  xz --test "$destination"
}

mkdir -p "$downloads_dir"
fetch_and_verify "$FP6_CONTROL_BOOT_IMAGE" "$FP6_CONTROL_BOOT_SHA256"
fetch_and_verify "$FP6_CONTROL_ROOTFS_IMAGE" "$FP6_CONTROL_ROOTFS_SHA256"

{
  printf 'LUMA_FP6_CONTROL_MANIFEST_VERSION=1\n'
  printf 'SOURCE=official-postmarketos-images\n'
  printf 'CHANNEL=%s\n' "$FP6_CONTROL_CHANNEL"
  printf 'UI=%s\n' "$FP6_CONTROL_UI"
  printf 'BUILD=%s\n' "$FP6_CONTROL_BUILD"
  printf 'IMAGE_VERSION=%s\n' "$FP6_CONTROL_IMAGE_VERSION"
  printf 'BOOT_IMAGE=%s\n' "$FP6_CONTROL_BOOT_IMAGE"
  printf 'BOOT_SHA256=%s\n' "$FP6_CONTROL_BOOT_SHA256"
  printf 'ROOTFS_IMAGE=%s\n' "$FP6_CONTROL_ROOTFS_IMAGE"
  printf 'ROOTFS_SHA256=%s\n' "$FP6_CONTROL_ROOTFS_SHA256"
  printf 'WIKI_REVISION=%s\n' "$FP6_CONTROL_WIKI_REVISION"
  printf 'CHECKSUMS_VERIFIED=true\n'
  printf 'COMPRESSED_STREAMS_VERIFIED=true\n'
  printf 'INSTALL_MUTATES_PHONE=%s\n' "$FP6_CONTROL_INSTALL_MUTATES_PHONE"
  printf 'INSTALL_AUTHORIZED=%s\n' "$FP6_CONTROL_INSTALL_AUTHORIZED"
} >"$manifest"

printf 'FP6 postmarketOS control artifacts verified:\n'
printf '  boot:     %s/%s\n' "$downloads_dir" "$FP6_CONTROL_BOOT_IMAGE"
printf '  rootfs:   %s/%s\n' "$downloads_dir" "$FP6_CONTROL_ROOTFS_IMAGE"
printf '  manifest: %s\n' "$manifest"
printf 'No phone operation was performed; installation remains unauthorized.\n'
