#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Download-only recovery preparation. This script never communicates with a
# phone and never invokes adb or fastboot.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-physical/inputs.env"

build_dir=${LUMA_FP6_PHYSICAL_BUILD_DIR:-$repo_root/build/mobile/fp6-physical}
downloads_dir="$build_dir/downloads"
factory_image="$downloads_dir/$FP6_STOCK_FACTORY_IMAGE"
manifest="$build_dir/recovery-manifest.txt"

command -v curl >/dev/null 2>&1 || {
  printf 'error: curl is required\n' >&2
  exit 1
}

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

mkdir -p "$downloads_dir"

if [ ! -f "$factory_image" ]; then
  partial="$factory_image.partial"
  curl --fail --location --continue-at - --proto '=https' --tlsv1.2 \
    --output "$partial" "$FP6_STOCK_FACTORY_URL"

  actual_bytes=$(wc -c <"$partial" | tr -d ' ')
  if [ "$actual_bytes" != "$FP6_STOCK_FACTORY_BYTES" ]; then
    printf 'error: stock image length mismatch; resumable partial retained\n' >&2
    printf 'expected: %s\nactual:   %s\n' "$FP6_STOCK_FACTORY_BYTES" "$actual_bytes" >&2
    exit 1
  fi

  actual=$(sha256_file "$partial")
  if [ "$actual" != "$FP6_STOCK_FACTORY_SHA256" ]; then
    printf 'error: stock image checksum mismatch; partial retained\n' >&2
    printf 'expected: %s\nactual:   %s\n' "$FP6_STOCK_FACTORY_SHA256" "$actual" >&2
    exit 1
  fi
  mv "$partial" "$factory_image"
fi

actual=$(sha256_file "$factory_image")
if [ "$actual" != "$FP6_STOCK_FACTORY_SHA256" ]; then
  printf 'error: existing stock image checksum mismatch\n' >&2
  printf 'expected: %s\nactual:   %s\n' "$FP6_STOCK_FACTORY_SHA256" "$actual" >&2
  exit 1
fi

{
  printf 'LUMA_FP6_RECOVERY_MANIFEST_VERSION=1\n'
  printf 'SOURCE=official-fairphone-factory-package\n'
  printf 'ANDROID_VERSION=%s\n' "$FP6_STOCK_ANDROID_VERSION"
  printf 'FAIRPHONE_PAGE_VERSION=%s\n' "$FP6_STOCK_PAGE_VERSION"
  printf 'FAIRPHONE_PACKAGE_VERSION=%s\n' "$FP6_STOCK_PACKAGE_VERSION"
  printf 'SECURITY_PATCH=%s\n' "$FP6_STOCK_SECURITY_PATCH"
  printf 'IMAGE=%s\n' "$FP6_STOCK_FACTORY_IMAGE"
  printf 'BYTES=%s\n' "$FP6_STOCK_FACTORY_BYTES"
  printf 'SHA256=%s\n' "$actual"
  printf 'CHECKSUM_VERIFIED=true\n'
  printf 'FLASH_AUTHORIZED=false\n'
  printf 'VERSION_LABEL_REVIEW=archive-directory-confirms-package-version\n'
} >"$manifest"

printf 'FP6 stock recovery package verified:\n'
printf '  image:    %s\n' "$factory_image"
printf '  manifest: %s\n' "$manifest"
printf 'No phone operation was performed.\n'
