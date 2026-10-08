#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

# Build the narrow, static normal-world driver sequencer used by the bounded
# FP6 stock-initialization test. It exposes no capture or biometric command.

set -eu
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output_dir=${1:?usage: build-fp6-focal-driver-stage.sh OUTPUT_DIR}
control=$repo_root/src/fp6-fingerprint-backend/focal_driver_control.c
control_header=$repo_root/src/fp6-fingerprint-backend/focal_driver_control.h
runner=$repo_root/src/fp6-fingerprint-backend/fp6_focal_driver_stage.c

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] ||
  die 'builder must be Linux/aarch64'
clang --version | head -n 1 | grep -Fx 'Ubuntu clang version 21.1.8 (6ubuntu1)' >/dev/null ||
  die 'native AArch64 Clang 21.1.8 differs'
[ ! -e "$output_dir" ] || die 'refuse to overwrite output directory'

mkdir -p "$output_dir"
clang -std=gnu11 -O2 -static -Wall -Wextra -Werror -Wformat=2 -Wshadow \
  -Wconversion -Wstrict-prototypes -Wmissing-prototypes \
  -Wmissing-declarations -Wformat-security \
  "$control" "$runner" -o "$output_dir/fp6-focal-driver-stage"

file "$output_dir/fp6-focal-driver-stage" |
  grep -Fq 'ELF 64-bit LSB executable, ARM aarch64' ||
  die 'output ELF identity differs'
file "$output_dir/fp6-focal-driver-stage" | grep -Fq 'statically linked' ||
  die 'output is not static'

{
  printf 'LUMA_FP6_FOCAL_DRIVER_STAGE_BUILD_VERSION=1\n'
  printf 'CONTROL_SOURCE_SHA256=%s\n' "$(hash "$control")"
  printf 'CONTROL_HEADER_SHA256=%s\n' "$(hash "$control_header")"
  printf 'RUNNER_SHA256=%s\n' "$(hash "$runner")"
  printf 'BINARY_SHA256=%s\n' "$(hash "$output_dir/fp6-focal-driver-stage")"
  printf 'STATIC_BINARY=true\n'
  printf 'DEVICE=/dev/focaltech_fp\n'
  printf 'OPERATIONS=prepare,prepare-probe,retry-probe,finish-probe,cleanup\n'
  printf 'CAPTURE_ALLOWED=false\n'
  printf 'ENROLL_ALLOWED=false\n'
  printf 'AUTHENTICATE_ALLOWED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'FP6 FocalTech driver stage: %s\n' "$output_dir/fp6-focal-driver-stage"
