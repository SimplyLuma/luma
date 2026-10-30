#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later

# Build Stevia with Luma's local LatinIME completer on native Fedora/AArch64.
# The output is an offline binary bundle; this script never changes a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/stevia-source.env"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

for command in git install meson mktemp ninja patch sha256sum python3 glib-compile-schemas; do
  command -v "$command" >/dev/null 2>&1 || die "missing required command: $command"
done
[ "$(uname -s):$(uname -m)" = Linux:aarch64 ] || \
  die 'build the FP6 Stevia bundle on native Linux/aarch64'

build_root=${LUMA_FP6_PHYSICAL_BUILD_DIR:-$repo_root/build/mobile/fp6-physical}
keyboard_dir=${LUMA_KEYBOARD_BUNDLE_DIR:-$build_root/luma-keyboard-aarch64}
output_dir=${1:-$build_root/luma-stevia-aarch64}
[ -x "$keyboard_dir/luma-latinime-decoder" ] || die 'missing Luma decoder bundle'
[ -f "$keyboard_dir/main_en.dict" ] || die 'missing Luma English dictionary'

install -d -m 0755 "$output_dir"
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT

source_dir="$work_dir/stevia"
git init -q "$source_dir"
git -C "$source_dir" remote add origin "$STEVIA_UPSTREAM_URL"
git -C "$source_dir" fetch -q --depth=1 origin "$STEVIA_UPSTREAM_COMMIT"
git -C "$source_dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
[ "$(git -C "$source_dir" rev-parse HEAD)" = "$STEVIA_UPSTREAM_COMMIT" ] || \
  die 'Stevia source commit mismatch'
python3 "$repo_root/scripts/mobile/prepare-luma-stevia.py" "$source_dir" \
  >"$output_dir/source-manifest.json"

build_dir="$work_dir/build"
meson setup --prefix=/usr "$build_dir" "$source_dir" -Dtests=true -Dgtk_doc=false -Dman=false
meson compile -C "$build_dir" -j2
LUMA_LATINIME_DECODER="$keyboard_dir/luma-latinime-decoder" \
LUMA_LATINIME_DICTIONARY="$keyboard_dir/main_en.dict" \
  meson test -C "$build_dir" \
    test-completer test-completer-base test-completer-hunspell \
    test-completer-luma test-completers test-osk-widget test-load-layouts \
    test-size-manager test-emoji-db test-keycap-history test-keycap-widget test-keycap-emoji --print-errorlogs

binary="$output_dir/luma-phosh-osk-stevia"
install -m 0755 "$build_dir/src/phosh-osk-stevia" "$binary"
install -d -m 0755 "$output_dir/schemas" "$output_dir/fonts"
install -m 0644 "$build_dir/data/mobi.phosh.osk.gschema.xml" \
  "$build_dir/data/mobi.phosh.osk.enums.xml" "$output_dir/schemas/"
glib-compile-schemas --strict "$output_dir/schemas"
install -m 0644 "$repo_root/assets/fonts/figtree/Figtree.ttf" \
  "$repo_root/assets/fonts/figtree/OFL.txt" "$output_dir/fonts/"
binary_sha256=$(sha256sum "$binary" | awk '{print $1}')
binary_bytes=$(wc -c <"$binary" | tr -d ' ')

{
  printf 'LUMA_STEVIA_BUNDLE_VERSION=2\n'
  printf 'ARCHITECTURE=aarch64\n'
  printf 'STEVIA_UPSTREAM_COMMIT=%s\n' "$STEVIA_UPSTREAM_COMMIT"
  printf 'STEVIA_UPSTREAM_VERSION=%s\n' "$STEVIA_UPSTREAM_VERSION"
  printf 'BINARY_SHA256=%s\n' "$binary_sha256"
  printf 'BINARY_BYTES=%s\n' "$binary_bytes"
  printf 'SCHEMAS_SHA256=%s\n' "$(sha256sum "$output_dir/schemas/gschemas.compiled" | awk '{print $1}')"
  printf 'SCHEMA_XML_SHA256=%s\n' "$(sha256sum "$output_dir/schemas/mobi.phosh.osk.gschema.xml" | awk '{print $1}')"
  printf 'ENUM_XML_SHA256=%s\n' "$(sha256sum "$output_dir/schemas/mobi.phosh.osk.enums.xml" | awk '{print $1}')"
  printf 'FONT_SHA256=%s\n' "$(sha256sum "$output_dir/fonts/Figtree.ttf" | awk '{print $1}')"
  printf 'LATINIME_REQUIRED=true\n'
  printf 'QT_REQUIRED=false\n'
  printf 'KWIN_REQUIRED=false\n'
  printf 'ANDROID_RUNTIME_REQUIRED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PACKAGE_INSTALLED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'Luma Stevia AArch64 bundle: %s\n' "$output_dir"
