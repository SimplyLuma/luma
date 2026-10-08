#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

# Build the bounded FP6 QSEEComCompat identity client from Luma's narrow
# runner and a pinned Qualcomm object-transport source. This script runs only
# on the isolated native AArch64 builder and does not contact the phone.

set -eu
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
upstream=${1:?usage: build-fp6-qcomtee-identity-client.sh QUIC_TEEC_SOURCE OUTPUT_DIR}
output_dir=${2:?missing output directory}
runner=$repo_root/src/fp6-fingerprint-qcomtee/qseecompat-identity.c
commit=736419e25a2036aac3292a10a93e394a90750ca3

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] ||
  die 'builder must be Linux/aarch64'
clang --version | head -n 1 | grep -Fx 'Ubuntu clang version 21.1.8 (6ubuntu1)' >/dev/null ||
  die 'native AArch64 Clang 21.1.8 differs'
[ -d "$upstream/.git" ] || die 'quic-teec Git source is missing'
[ "$(git -C "$upstream" rev-parse HEAD)" = "$commit" ] || die 'quic-teec commit differs'
[ "$(git -C "$upstream" status --porcelain)" = '' ] || die 'quic-teec source is dirty'
[ ! -e "$output_dir" ] || die 'refuse to overwrite output directory'
[ "$(hash "$upstream/libqcomtee/src/qcomtee_object.c")" = 44d0d8b2258973cd63f651146811aeeb9b91fd1b6fc212144a6c41d27490b3b5 ] ||
  die 'qcomtee object transport differs'
[ "$(hash "$upstream/libqcomtee/src/qcomtee_object_private.h")" = b44381ef2f87c9cfdac5a2693dd24bc879434503a7d646a6eb519353a10b625d ] ||
  die 'qcomtee private header differs'
[ "$(hash "$upstream/libqcomtee/include/qcomtee_object.h")" = 76256d11dee88517db12f9fb33e4f8f5017b661c77194185bc080cc520341f22 ] ||
  die 'qcomtee public header differs'
[ "$(hash "$upstream/libqcomtee/include/qcomtee_errno.h")" = 14a4d43a6cb6d0873571476e4a2c8b8bdf3a53555a0f789b693c59525cadead6 ] ||
  die 'qcomtee errno header differs'
[ "$(hash "$upstream/libqcomtee/src/linux/tee.h")" = 375e7c088bf4fcd6cae5f5d9cbd7ad77b037a5b5065dc7ac2ce71b8f8117a645 ] ||
  die 'qcomtee Linux UAPI header differs'

mkdir -p "$output_dir"
clang -std=gnu11 -O2 -static -pthread -include alloca.h \
  -Wall -Wextra -Werror -Wshadow \
  -Wcast-align -Wstrict-prototypes -Wmissing-prototypes \
  -Wmissing-declarations -Wformat-security \
  -I"$upstream/libqcomtee/include" -I"$upstream/libqcomtee/src" \
  "$upstream/libqcomtee/src/qcomtee_object.c" "$runner" \
  -o "$output_dir/fp6-qseecompat-identity"

file "$output_dir/fp6-qseecompat-identity" |
  grep -Fq 'ELF 64-bit LSB executable, ARM aarch64' || die 'output ELF identity differs'

{
  printf 'LUMA_FP6_QCOMTEE_IDENTITY_BUILD_VERSION=1\n'
  printf 'QUIC_TEEC_COMMIT=%s\n' "$commit"
  printf 'RUNNER_SHA256=%s\n' "$(hash "$runner")"
  printf 'BINARY_SHA256=%s\n' "$(hash "$output_dir/fp6-qseecompat-identity")"
  printf 'STATIC_BINARY=true\n'
  printf 'ALLOWLIST=smplap64\n'
  printf 'TA_COMMANDS_ALLOWED=false\n'
  printf 'BIOMETRIC_COMMANDS_ALLOWED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'FP6 QCOMTEE identity client: %s\n' "$output_dir/fp6-qseecompat-identity"
