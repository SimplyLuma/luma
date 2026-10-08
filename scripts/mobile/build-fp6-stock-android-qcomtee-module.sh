#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build the unmodified upstream QCOMTEE object-ABI driver for the exact
# RAM-only FP6 Android-container kernel.  This module is a temporary physical
# gate: it adds the object client /dev/teeN needed by stock QREL KeyMint while
# the separate QSEECOM module continues to own listeners and RPMB on the host.

set -Eeuo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
source_archive=$repo_root/build/mobile/fp6-physical/fp6-camera-source-archives/linux-v7.1.2-milos.tar.gz
symvers=$repo_root/build/mobile/fp6-physical/fp6-camera-source-archives/Module.symvers
runtime_config=$repo_root/build/mobile/fp6-physical/fp6-stock-android-container-kernel-compat1/bundle/config
output_dir=${1:?usage: build-fp6-stock-android-qcomtee-module.sh OUTPUT_DIR}
kernel_release=7.1.2-luma-fp-ims1
localversion=-luma-fp-ims1
jobs=${LUMA_KERNEL_BUILD_JOBS:-8}

expected_source_archive_sha=6043a062d595e1b4913512c3414387d9a016c4690d8832720651df07276dc78b
expected_symvers_sha=0136080d85bcb215aad56324ed61259c1581400d0159489c664b75b476de04e0
expected_config_sha=7eb084e3a201a11e69676aad4c93b9588e72f15dc9ef86d5d5c56ee13fc0f448

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

case $jobs in ''|*[!0-9]*) die 'LUMA_KERNEL_BUILD_JOBS must be numeric' ;; esac
[[ $jobs -ge 1 ]] || die 'LUMA_KERNEL_BUILD_JOBS must be positive'
[[ $(id -u) -eq 0 ]] || die 'run as root in the isolated builder'
[[ $(uname -s) == Linux && $(uname -m) == aarch64 ]] ||
  die 'builder must be Linux/aarch64'
clang --version | head -n 1 | grep -Fx 'Ubuntu clang version 21.1.8 (6ubuntu1)' >/dev/null ||
  die 'native AArch64 Clang 21.1.8 differs'
[[ ! -e $output_dir ]] || die "refuse to overwrite output: $output_dir"
[[ $(hash "$source_archive") == "$expected_source_archive_sha" ]] ||
  die 'source archive differs'
[[ $(hash "$symvers") == "$expected_symvers_sha" ]] || die 'symbol table differs'
[[ $(hash "$runtime_config") == "$expected_config_sha" ]] || die 'runtime config differs'

work_dir=$(mktemp -d /tmp/luma-fp6-stock-qcomtee.XXXXXX)
cleanup() { find "$work_dir" -depth -delete 2>/dev/null || true; }
trap cleanup EXIT INT TERM

tar -xzf "$source_archive" -C "$work_dir"
tree=$work_dir/linux
install -m 0644 "$runtime_config" "$tree/.config"
install -m 0644 "$runtime_config" "$tree/.config.before"
install -m 0644 "$symvers" "$tree/Module.symvers"
# These GPL exports were added after the archived camera symbol snapshot. They
# are built into the accepted running kernel. CONFIG_MODVERSIONS is disabled,
# so modpost needs their exported names rather than CRCs.
printf '0x00000000\tqcom_tzmem_shm_bridge_create\tvmlinux\tEXPORT_SYMBOL_GPL\t\n' \
  >>"$tree/Module.symvers"
printf '0x00000000\tqcom_tzmem_shm_bridge_delete\tvmlinux\tEXPORT_SYMBOL_GPL\t\n' \
  >>"$tree/Module.symvers"

(
  cd "$tree"
  prefix_flags="-fdebug-prefix-map=$tree=/usr/src/linux -ffile-prefix-map=$tree=/usr/src/linux -fmacro-prefix-map=$tree=/usr/src/linux"
  scripts/config --module QCOMTEE
  make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" olddefconfig
  grep -qx 'CONFIG_QCOMTEE=m' .config || die 'QCOMTEE config differs'
  grep -qx '# CONFIG_MODVERSIONS is not set' .config || die 'module versioning policy differs'
  config_delta=$(scripts/diffconfig .config.before .config | sed '/^$/d')
  expected_delta=$'-FINGER_FOCAL m\n-INPUT_FINGERPRINT y\n-REGMAP_SOUNDWIRE m\n-SND_SOC_WCD9378 m\n-SND_SOC_WCD9378_SDW m\n-SND_SOC_WCD_COMMON m\n-TEE_QSEECOM m\n-VIDEO_DW9784 n\n-VIDEO_IMX896 n\n-VIDEO_S5KKD1SP n\n QCOMTEE n -> m'
  # The source archive predates the listed FP6-only additions. They remain in
  # the running kernel; olddefconfig can only discard their unknown symbols
  # while preparing this external module. Pin the full delta so no known
  # in-tree option can change unnoticed.
  [[ $config_delta == "$expected_delta" ]] ||
    die "unexpected configuration delta: ${config_delta:-<empty>}"
  make -j"$jobs" ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" \
    KCFLAGS="$prefix_flags" modules_prepare
  make -j"$jobs" ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" \
    KCFLAGS="$prefix_flags" \
    KBUILD_BUILD_USER=pmos KBUILD_BUILD_HOST=build \
    KBUILD_BUILD_TIMESTAMP='2026-08-28 00:00:00 UTC' \
    KBUILD_BUILD_VERSION=1-postmarketos \
    M=drivers/tee/qcomtee modules
  [[ $(make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" -s kernelrelease) == "$kernel_release" ]] ||
    die 'kernel release differs'
)

mkdir -p "$output_dir"
install -m 0644 "$tree/drivers/tee/qcomtee/qcomtee.ko" "$output_dir/qcomtee.ko"
[[ $(modinfo -F vermagic "$output_dir/qcomtee.ko" | awk '{print $1}') == "$kernel_release" ]] ||
  die 'module vermagic differs'
[[ $(modinfo -F name "$output_dir/qcomtee.ko") == qcomtee ]] || die 'module name differs'
[[ -z $(modinfo -F depends "$output_dir/qcomtee.ko") ]] || die 'module dependencies differ'
[[ -z $(modinfo -F signer "$output_dir/qcomtee.ko") ]] || die 'module unexpectedly signed'

{
  printf 'LUMA_FP6_STOCK_ANDROID_QCOMTEE_BUILD_VERSION=1\n'
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'RUNTIME_CONFIG_SHA256=%s\n' "$expected_config_sha"
  printf 'SOURCE_ARCHIVE_SHA256=%s\n' "$expected_source_archive_sha"
  printf 'MODULE_SYMVERS_SHA256=%s\n' "$expected_symvers_sha"
  printf 'MODULE_SHA256=%s\n' "$(hash "$output_dir/qcomtee.ko")"
  printf 'SOURCE_PATCHED=false\n'
  printf 'CONFIG_DELTA=QCOMTEE=m\n'
  printf 'MODULE_SIGNED=false\n'
  printf 'MODULE_SIGNATURE_ENFORCED_BY_RUNTIME=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

trap - EXIT INT TERM
cleanup
printf 'FP6 stock Android QCOMTEE module: %s\n' "$output_dir/qcomtee.ko"
