#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
arch=${1:-$(uname -m)}
manifest="$repo_root/config/android/images-$arch.env"
[ -r "$manifest" ] || { printf 'no pinned Android images for %s\n' "$arch" >&2; exit 1; }
# shellcheck disable=SC1090
. "$manifest"
destination=${LUMA_ANDROID_IMAGE_CACHE:-"$repo_root/build/cache/android/$arch"}
install -d -m 0755 "$destination"
fetch() {
  name=$1 url=$2 digest=$3 size=$4 target="$destination/$1"
  part="$target.part"
  verify() {
    candidate=$1
    [ -f "$candidate" ] && \
      [ "$(wc -c <"$candidate" | tr -d '[:space:]')" = "$size" ] && \
      printf '%s  %s\n' "$digest" "$candidate" | sha256sum -c - >/dev/null 2>&1
  }
  if ! verify "$target"; then
    if ! verify "$part"; then
      curl --fail --location --retry 4 --output "$part" "$url"
      verify "$part"
    fi
    mv "$part" "$target"
  fi
}
fetch "$LUMA_ANDROID_SYSTEM_FILENAME" "$LUMA_ANDROID_SYSTEM_URL" "$LUMA_ANDROID_SYSTEM_SHA256" "$LUMA_ANDROID_SYSTEM_SIZE"
fetch "$LUMA_ANDROID_VENDOR_FILENAME" "$LUMA_ANDROID_VENDOR_URL" "$LUMA_ANDROID_VENDOR_SHA256" "$LUMA_ANDROID_VENDOR_SIZE"
printf 'Verified Android images: %s\n' "$destination"
