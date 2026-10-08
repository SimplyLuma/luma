#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

# Build the bounded native focal64 identity probe in the isolated AArch64
# builder. The probe can attach to an already loaded TA and issue only the
# audited non-capture commands implemented by focal_protocol.c.

set -eu
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output_dir=${1:?usage: build-fp6-fingerprint-probe.sh OUTPUT_DIR}
source_dir=$repo_root/src/fp6-fingerprint-backend
bridge_dir=$repo_root/src/fp6-fingerprint-qsee-bridge
build_dir=$output_dir/.build
binary=$output_dir/fp6-fingerprint-probe
build_timestamp='2026-08-22 00:00:00 UTC'

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

[ "$(id -u)" -eq 0 ] || die 'run as root in the isolated builder'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] ||
  die 'builder must be Linux/aarch64'
clang --version | head -n 1 | grep -Fx 'Ubuntu clang version 21.1.8 (6ubuntu1)' >/dev/null ||
  die 'native AArch64 Clang 21.1.8 differs'
command -v llvm-strip >/dev/null 2>&1 || die 'llvm-strip is missing'
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"

mkdir -p "$build_dir"

clang -O2 -g -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion \
  -ffile-prefix-map="$repo_root=/usr/src/luma" \
  -fdebug-prefix-map="$repo_root=/usr/src/luma" \
  -Wl,--build-id=none -I"$bridge_dir" \
  -o "$build_dir/test-focal-protocol" \
  "$source_dir/focal_protocol.c" "$source_dir/test_focal_protocol.c"
"$build_dir/test-focal-protocol" | grep -Fx 'FocalTech protocol tests: PASS' >/dev/null ||
  die 'synthetic protocol tests failed'

SOURCE_DATE_EPOCH=1787356800 clang -O2 -g -Wall -Wextra -Werror \
  -Wformat=2 -Wshadow -Wconversion \
  -ffile-prefix-map="$repo_root=/usr/src/luma" \
  -fdebug-prefix-map="$repo_root=/usr/src/luma" \
  -Wl,--build-id=none -I"$bridge_dir" -o "$build_dir/probe" \
  "$source_dir/fp6_fingerprint_probe.c" "$source_dir/focal_protocol.c" \
  "$bridge_dir/qseecom_bridge.c" -pthread
llvm-strip --strip-debug "$build_dir/probe" -o "$binary"
chmod 0755 "$binary"

{
  printf 'LUMA_FP6_FINGERPRINT_PROBE_VERSION=3\n'
  printf 'BINARY_SHA256=%s\n' "$(hash "$binary")"
  printf 'BINARY_SIZE=%s\n' "$(stat -c %s "$binary")"
  printf 'TARGET_ARCH=aarch64\n'
  printf 'TRUSTLET_NAME=focal64\n'
  printf 'INITIALIZATION_COMMAND=true\n'
  printf 'DEVICE_PROBE_COMMAND=true\n'
  printf 'CAPTURE_COMMANDS=false\n'
  printf 'ENROLL_COMMANDS=false\n'
  printf 'AUTHENTICATE_COMMANDS=false\n'
  printf 'SYNTHETIC_TESTS=passed\n'
  printf 'BUILD_TIMESTAMP=%s\n' "$build_timestamp"
  printf 'PHONE_ACCESSED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

find "$build_dir" -depth -delete
printf 'FP6 bounded fingerprint probe: %s\n' "$binary"
