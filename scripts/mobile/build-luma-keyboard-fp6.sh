#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build Luma's small native AOSP LatinIME decoder for Fedora/AArch64. This
# creates an offline runtime bundle only; it never contacts or changes a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/latinime-source.env"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

for command in curl git mktemp patch rg tar xargs; do
  command -v "$command" >/dev/null 2>&1 || die "missing required command: $command"
done

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

case "$(uname -s):$(uname -m)" in
  Darwin:arm64)
    zig_url=$LUMA_KEYBOARD_ZIG_AARCH64_MACOS_URL
    zig_sha256=$LUMA_KEYBOARD_ZIG_AARCH64_MACOS_SHA256
    zig_dirname="zig-aarch64-macos-$LUMA_KEYBOARD_ZIG_VERSION"
    ;;
  Linux:x86_64)
    zig_url=$LUMA_KEYBOARD_ZIG_X86_64_LINUX_URL
    zig_sha256=$LUMA_KEYBOARD_ZIG_X86_64_LINUX_SHA256
    zig_dirname="zig-x86_64-linux-$LUMA_KEYBOARD_ZIG_VERSION"
    ;;
  *) die 'supported hosts are macOS/arm64 and Linux/x86_64' ;;
esac

output_dir=${1:-$repo_root/build/mobile/fp6-physical/luma-keyboard-aarch64}
cache_dir=${LUMA_KEYBOARD_CACHE_DIR:-$repo_root/build/cache/luma-keyboard}
install -d -m 0755 "$output_dir" "$cache_dir"
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT

source_dir="$work_dir/LatinIME"
git init -q "$source_dir"
git -C "$source_dir" remote add origin "$LATINIME_UPSTREAM_URL"
git -C "$source_dir" fetch -q --depth=1 origin "$LATINIME_UPSTREAM_COMMIT"
git -C "$source_dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
[ "$(git -C "$source_dir" rev-parse HEAD)" = "$LATINIME_UPSTREAM_COMMIT" ] || \
  die 'LatinIME source commit mismatch'

git -C "$source_dir" apply \
  "$repo_root/patches/latinime/0001-luma-host-decoder-api.patch"
install -m 0644 "$repo_root/src/luma-keyboard/native/jni.h" \
  "$source_dir/native/jni/src/jni.h"
install -m 0644 "$repo_root/src/luma-keyboard/native/luma_decoder_main.cpp" \
  "$source_dir/native/jni/luma_decoder_main.cpp"

dictionary="$source_dir/java/res/raw/main_en.dict"
[ "$(sha256 "$dictionary")" = "$LATINIME_ENGLISH_DICTIONARY_SHA256" ] || \
  die 'LatinIME English dictionary checksum mismatch'

zig_archive="$cache_dir/$zig_dirname.tar.xz"
if [ ! -f "$zig_archive" ]; then
  curl -fL --retry 3 -o "$zig_archive" "$zig_url"
fi
[ "$(sha256 "$zig_archive")" = "$zig_sha256" ] || \
  die 'Zig archive checksum mismatch'
tar -xf "$zig_archive" -C "$work_dir"
zig="$work_dir/$zig_dirname/zig"
[ -x "$zig" ] || die 'Zig executable is missing after extraction'

binary="$work_dir/luma-latinime-decoder"
zig_global_cache="$work_dir/zig-global-cache"
zig_local_cache="$work_dir/zig-local-cache"
install -d -m 0755 "$zig_global_cache" "$zig_local_cache"
(
  cd "$source_dir/native/jni"
  rg -o '"src/[^"]+\.cpp"' Android.bp \
    | tr -d '"' \
    | rg -v 'jni_data_utils.cpp' \
    | xargs env \
        ZIG_GLOBAL_CACHE_DIR="$zig_global_cache" \
        ZIG_LOCAL_CACHE_DIR="$zig_local_cache" \
        "$zig" c++ \
          -target aarch64-linux-musl \
          -std=c++17 -O2 \
          -ffunction-sections -fdata-sections \
          -Wl,--gc-sections -Wl,-s \
          -DHOST_TOOL -I src \
          luma_decoder_main.cpp \
          -o "$binary"
)

file "$binary" | grep -Fq 'ARM aarch64' || die 'decoder is not AArch64'
file "$binary" | grep -Fq 'statically linked' || die 'decoder is not static'

install -m 0755 "$binary" "$output_dir/luma-latinime-decoder"
install -m 0644 "$dictionary" "$output_dir/main_en.dict"
binary_sha256=$(sha256 "$output_dir/luma-latinime-decoder")
dictionary_sha256=$(sha256 "$output_dir/main_en.dict")
binary_bytes=$(wc -c <"$output_dir/luma-latinime-decoder" | tr -d ' ')
dictionary_bytes=$(wc -c <"$output_dir/main_en.dict" | tr -d ' ')

cat >"$output_dir/manifest.env" <<EOF
LUMA_KEYBOARD_BUNDLE_VERSION=1
ARCHITECTURE=aarch64
LATINIME_UPSTREAM_COMMIT=$LATINIME_UPSTREAM_COMMIT
DECODER_SHA256=$binary_sha256
DECODER_BYTES=$binary_bytes
DICTIONARY_SHA256=$dictionary_sha256
DICTIONARY_BYTES=$dictionary_bytes
ANDROID_RUNTIME_REQUIRED=false
JVM_REQUIRED=false
QT_REQUIRED=false
SECOND_COMPOSITOR_REQUIRED=false
PHONE_ACCESSED=false
EOF
chmod 0644 "$output_dir/manifest.env"

printf 'Luma native keyboard AArch64 bundle: %s\n' "$output_dir"
