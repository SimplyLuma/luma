#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Rebuild only the Fairphone FocalTech control shim for the exact running FP6
# diagnostic kernel. The output is unsigned and intended solely for temporary,
# rollback-free physical validation; this script never contacts the phone.

set -Eeuo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

source_archive=$repo_root/build/mobile/fp6-physical/fp6-camera-source-archives/linux-v7.1.2-milos.tar.gz
symvers=$repo_root/build/mobile/fp6-physical/fp6-camera-source-archives/Module.symvers
runtime_config=$repo_root/build/mobile/fp6-physical/fp6-fingerprint-matched-cma-v14/bundle/config
patch_dir=$repo_root/patches/linux-milos-fingerprint
output_dir=${1:?usage: build-fp6-focaltech-control-module-v21.sh OUTPUT_DIR}
kernel_release=7.1.2-luma-fp-cma1
localversion=-luma-fp-cma1
jobs=${LUMA_KERNEL_BUILD_JOBS:-8}

expected_source_archive_sha=6043a062d595e1b4913512c3414387d9a016c4690d8832720651df07276dc78b
expected_symvers_sha=0136080d85bcb215aad56324ed61259c1581400d0159489c664b75b476de04e0
expected_config_sha=5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }
hash_patchset() {
  (
    cd "$patch_dir"
    find . -maxdepth 1 -type f -name '*.patch' | LC_ALL=C sort |
      while IFS= read -r patch_file; do
        printf '%s  %s\n' "$(hash "$patch_file")" "$patch_file"
      done
  ) | sha256sum | cut -d ' ' -f 1
}

case $jobs in ''|*[!0-9]*) die 'LUMA_KERNEL_BUILD_JOBS must be numeric' ;; esac
[[ $jobs -ge 1 ]] || die 'LUMA_KERNEL_BUILD_JOBS must be positive'
[[ $(id -u) -eq 0 ]] || die 'run as root in the isolated builder'
[[ $(uname -s) == Linux && $(uname -m) == aarch64 ]] || die 'builder must be Linux/aarch64'
clang --version | head -n 1 | grep -Fx 'Ubuntu clang version 21.1.8 (6ubuntu1)' >/dev/null ||
  die 'native AArch64 Clang 21.1.8 differs'
[[ ! -e $output_dir ]] || die "refuse to overwrite output: $output_dir"
[[ $(hash "$source_archive") == "$expected_source_archive_sha" ]] || die 'source archive differs'
[[ $(hash "$symvers") == "$expected_symvers_sha" ]] || die 'symbol table differs'
[[ $(hash "$runtime_config") == "$expected_config_sha" ]] || die 'runtime config differs'
patch_count=$(find "$patch_dir" -maxdepth 1 -type f -name '*.patch' | wc -l)
[[ $patch_count -eq $FP6_FINGERPRINT_DRIVER_PATCH_COUNT ]] || die 'patch count differs'
patchset_sha=$(hash_patchset)
[[ $patchset_sha == "$FP6_FINGERPRINT_DRIVER_PATCHSET_SHA256" ]] || die 'patchset digest differs'

work_dir=$(mktemp -d /tmp/luma-fp6-focaltech-control-v21.XXXXXX)
cleanup() { find "$work_dir" -depth -delete 2>/dev/null || true; }
trap cleanup EXIT INT TERM

tar -xzf "$source_archive" -C "$work_dir"
tree=$work_dir/linux
install -m 0644 "$runtime_config" "$tree/.config"
install -m 0644 "$symvers" "$tree/Module.symvers"
(
  cd "$tree"
  for patch_file in "$patch_dir"/*.patch; do
    patch -p1 --fuzz=0 --no-backup-if-mismatch <"$patch_file"
  done
  prefix_flags="-fdebug-prefix-map=$tree=/usr/src/linux -ffile-prefix-map=$tree=/usr/src/linux -fmacro-prefix-map=$tree=/usr/src/linux"
  scripts/config --module FINGER_FOCAL
  make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" olddefconfig
  grep -qx 'CONFIG_FINGER_FOCAL=m' .config || die 'FocalTech config differs'
  grep -qx '# CONFIG_MODVERSIONS is not set' .config || die 'module versioning policy differs'
  make -j"$jobs" ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" \
    KCFLAGS="$prefix_flags" modules_prepare
  make -j"$jobs" ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" \
    KCFLAGS="$prefix_flags" \
    KBUILD_BUILD_USER=pmos KBUILD_BUILD_HOST=build \
    KBUILD_BUILD_TIMESTAMP='2026-08-23 00:00:00 UTC' \
    KBUILD_BUILD_VERSION=1-postmarketos \
    M=drivers/input/finger/focal_finger focaltech_fp.ko
  [[ $(make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" -s kernelrelease) == "$kernel_release" ]] ||
    die 'kernel release differs'
)

mkdir -p "$output_dir"
install -m 0644 "$tree/drivers/input/finger/focal_finger/focaltech_fp.ko" "$output_dir/focaltech_fp.ko"
[[ $(modinfo -F vermagic "$output_dir/focaltech_fp.ko" | awk '{print $1}') == "$kernel_release" ]] ||
  die 'module vermagic differs'
[[ $(modinfo -F name "$output_dir/focaltech_fp.ko") == focaltech_fp ]] || die 'module name differs'
[[ -z $(modinfo -F depends "$output_dir/focaltech_fp.ko") ]] || die 'module dependencies differ'
[[ -z $(modinfo -F signer "$output_dir/focaltech_fp.ko") ]] || die 'module unexpectedly signed'

{
  printf 'LUMA_FP6_FOCALTECH_CONTROL_BUILD_VERSION=21\n'
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'RUNTIME_CONFIG_SHA256=%s\n' "$expected_config_sha"
  printf 'SOURCE_ARCHIVE_SHA256=%s\n' "$expected_source_archive_sha"
  printf 'MODULE_SYMVERS_SHA256=%s\n' "$expected_symvers_sha"
  printf 'PATCH_COUNT=%s\n' "$patch_count"
  printf 'PATCHSET_SHA256=%s\n' "$patchset_sha"
  printf 'MODULE_SHA256=%s\n' "$(hash "$output_dir/focaltech_fp.ko")"
  printf 'MODULE_SIGNED=false\n'
  printf 'MODULE_SIGNATURE_ENFORCED_BY_RUNTIME=false\n'
  printf 'IRQ_TRIGGER=rising-edge-primary-handler\n'
  printf 'IRQ_ONESHOT=false\n'
  printf 'CAPTURE_ALLOWED=false\n'
  printf 'ENROLL_ALLOWED=false\n'
  printf 'AUTHENTICATE_ALLOWED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

trap - EXIT INT TERM
cleanup
printf 'FP6 FocalTech control-v21 module: %s\n' "$output_dir/focaltech_fp.ko"
