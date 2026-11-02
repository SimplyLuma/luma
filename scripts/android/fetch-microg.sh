#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Place microG's pinned release APKs (config/android/microg.env) in DEST as
# GmsCore.apk, GsfProxy.apk and FakeStore.apk. Each comes from DEST itself,
# the cache directory, or microG's repository, and is used only when its size
# and SHA-256 match the pin.
#
#   fetch-microg.sh DEST [CACHE]
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo_root/config/android/microg.env"
dest=${1:?usage: fetch-microg.sh DEST [CACHE]}
cache=${2:-${LUMA_ANDROID_MICROG_CACHE:-$repo_root/build/android/microg}}
mkdir -p "$dest" "$cache"

matches() {
  [ -f "$1" ] && [ "$(stat -c %s "$1")" = "$2" ] &&
    [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$3" ]
}

for name in GMSCORE:GmsCore GSFPROXY:GsfProxy FAKESTORE:FakeStore; do
  key=${name%%:*} file=${name#*:}.apk
  url_var=LUMA_MICROG_${key}_URL sha_var=LUMA_MICROG_${key}_SHA256 size_var=LUMA_MICROG_${key}_SIZE
  url=${!url_var} sha=${!sha_var} size=${!size_var}
  matches "$dest/$file" "$size" "$sha" && continue
  if ! matches "$cache/$file" "$size" "$sha"; then
    curl -fsSL --retry 3 -o "$cache/$file.partial" "$url"
    mv "$cache/$file.partial" "$cache/$file"
    matches "$cache/$file" "$size" "$sha" || {
      printf 'error: %s does not match its pin\n' "$url" >&2
      rm -f "$cache/$file"
      exit 1
    }
  fi
  install -m 0644 "$cache/$file" "$dest/$file"
done
printf 'microG APKs in %s\n' "$dest"
