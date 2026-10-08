#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build a recovery-only rmtfs binary on native Fedora 44 AArch64.
# The result is never installed and normal rmtfs behavior remains unchanged.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output_dir=${1:-$repo_root/build/mobile/fp6-cellular/rmtfs-nv-recovery}
source_sha512=15b8dd0e0b2105e331feb7632c44e213566b1feb42c5bef7fe342e96a948fc6fe1e16c767fb67d726a771088a04f4b17c8fd813c469bf6c289beb2b9d11627d8
source_url=https://github.com/linux-msm/rmtfs/archive/v1.1.1/rmtfs-1.1.1.tar.gz
study_patch=$repo_root/patches/rmtfs/0001-storage-add-fp6-modem-study.patch
recovery_patch=$repo_root/patches/rmtfs/0002-fp6-bounded-zero-nv-recovery.patch

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Linux ] || die 'build requires Linux'
[ "$(uname -m)" = aarch64 ] || die 'build requires native AArch64'
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}:${VERSION_ID:-}" = fedora:44 ] || die 'build requires Fedora 44'
for command in curl gcc make patch sha256sum sha512sum strings; do
  command -v "$command" >/dev/null || die "missing build command: $command"
done
rpm -q qrtr-devel >/dev/null || die 'missing build dependency: qrtr-devel'
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

install -d -m 0700 "$output_dir/source"
archive=$output_dir/rmtfs-1.1.1.tar.gz
curl -L --fail --silent --show-error -o "$archive" "$source_url"
printf '%s  %s\n' "$source_sha512" "$archive" | sha512sum --check --status ||
  die 'upstream source checksum mismatch'
tar -xf "$archive" -C "$output_dir/source" --strip-components=1
patch -d "$output_dir/source" -p1 --fuzz=0 <"$study_patch"
patch -d "$output_dir/source" -p1 --fuzz=0 <"$recovery_patch"
make -C "$output_dir/source" clean
make -C "$output_dir/source" -j"$(nproc)" CFLAGS='-O2 -g0 -fstack-protector-strong -D_FORTIFY_SOURCE=3'
install -m 0700 "$output_dir/source/rmtfs" "$output_dir/rmtfs-fp6-nv-recovery"

strings "$output_dir/rmtfs-fp6-nv-recovery" | grep -Fqx '/boot/modem_study'
strings "$output_dir/rmtfs-fp6-nv-recovery" | grep -Fqx '[storage] bounded blank-NV recovery enabled for '\''%s'\'''
{
  printf 'LUMA_FP6_MODEM_NV_RECOVERY_BUILD_VERSION=1\n'
  printf 'UPSTREAM_VERSION=1.1.1\n'
  printf 'ARCHITECTURE=aarch64\n'
  printf 'SOURCE_SHA512=%s\n' "$source_sha512"
  printf 'STUDY_PATCH_SHA256=%s\n' "$(sha256sum "$study_patch" | awk '{print $1}')"
  printf 'RECOVERY_PATCH_SHA256=%s\n' "$(sha256sum "$recovery_patch" | awk '{print $1}')"
  printf 'BINARY_SHA256=%s\n' "$(sha256sum "$output_dir/rmtfs-fp6-nv-recovery" | awk '{print $1}')"
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITIONS_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0600 "$output_dir/manifest.env"
printf 'Built recovery-only binary: %s\n' "$output_dir/rmtfs-fp6-nv-recovery"
