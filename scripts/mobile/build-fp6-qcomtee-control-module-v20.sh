#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Rebuild only the bounded QCOMTEE control module from the pinned Milos source
# archive and the exact running FP6 configuration. The accepted development
# signing key was intentionally not retained, and the running diagnostic kernel
# does not enforce module signatures, so this artifact remains explicitly
# unsigned and temporary.

set -Eeuo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
source_archive=$repo_root/build/mobile/fp6-physical/fp6-camera-source-archives/linux-v7.1.2-milos.tar.gz
symvers=$repo_root/build/mobile/fp6-physical/fp6-camera-source-archives/Module.symvers
runtime_config=$repo_root/build/mobile/fp6-physical/fp6-fingerprint-matched-cma-v14/bundle/config
overlay=$repo_root/src/fp6-fingerprint-qcomtee/qcomtee-control.c
patch_file=$repo_root/patches/linux-qcomtee-control-v18/0001-qcomtee-add-bounded-luma-control-identity-hook.patch
extractor=$repo_root/scripts/mobile/extract-fp6-focal-config.py
stock_hal=$repo_root/build/mobile/fp6-physical/fp6-stock-hal-qrel1695/fingerprint.default.so
output_dir=${1:?usage: build-fp6-qcomtee-control-module-v20.sh OUTPUT_DIR}
kernel_release=7.1.2-luma-fp-cma1
localversion=-luma-fp-cma1
jobs=${LUMA_KERNEL_BUILD_JOBS:-8}

expected_source_archive_sha=6043a062d595e1b4913512c3414387d9a016c4690d8832720651df07276dc78b
expected_symvers_sha=0136080d85bcb215aad56324ed61259c1581400d0159489c664b75b476de04e0
expected_config_sha=5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe
expected_overlay_sha=d5ff598d70c78bb2b5899f321dc83960f9a11a6432e28103bdaf017093daea81
expected_patch_sha=e67edc32cdd7f2812b24ee8f6ad732892fea5def6e68938f0134f65be76b944d
expected_extractor_sha=9f70dec6ceab973382e25248d7e8f9693264f338198feb23701aaad308b2024a
expected_stock_hal_sha=ae08b39c8c78b40a769795684afc7d342c19acf8c1b443fdc56be47b5ca8bedd
expected_focal_config_sha=8b013facc735799355dd39b78d0df0a11b547b5faa0f93ff18dd080a78a220c5
expected_focal_header_sha=5ad0afbf523607a26d5e8bd0b6b655cbfdf93bf97617e51db877c71a034e334c

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

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
[[ $(hash "$overlay") == "$expected_overlay_sha" ]] || die 'control source differs'
[[ $(hash "$patch_file") == "$expected_patch_sha" ]] || die 'integration patch differs'
[[ $(hash "$extractor") == "$expected_extractor_sha" ]] || die 'configuration extractor differs'
[[ $(hash "$stock_hal") == "$expected_stock_hal_sha" ]] || die 'stock fingerprint HAL differs'

work_dir=$(mktemp -d /tmp/luma-fp6-qcomtee-control-v20.XXXXXX)
cleanup() { find "$work_dir" -depth -delete 2>/dev/null || true; }
trap cleanup EXIT INT TERM

tar -xzf "$source_archive" -C "$work_dir"
tree=$work_dir/linux
install -m 0644 "$runtime_config" "$tree/.config"
install -m 0644 "$symvers" "$tree/Module.symvers"
# These two GPL exports were added after the archived camera symbol snapshot.
# They are built into the accepted running kernel; CONFIG_MODVERSIONS is off,
# so modpost needs only their exact exported identities, not CRCs.
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
  make -j"$jobs" ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" \
    KCFLAGS="$prefix_flags" modules_prepare
  patch -p1 --fuzz=0 --no-backup-if-mismatch <"$patch_file"
  install -m 0644 "$overlay" drivers/tee/qcomtee/qcomtee-control.c
  python3 "$extractor" "$stock_hal" \
    drivers/tee/qcomtee/luma-focal-config.h --format c-header --nul
  [[ $(hash drivers/tee/qcomtee/luma-focal-config.h) == "$expected_focal_header_sha" ]] ||
    die 'generated FocalTech configuration header differs'
  make -j"$jobs" ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" \
    KCFLAGS="$prefix_flags" \
    KBUILD_BUILD_USER=pmos KBUILD_BUILD_HOST=build \
    KBUILD_BUILD_TIMESTAMP='2026-08-23 00:00:00 UTC' \
    KBUILD_BUILD_VERSION=1-postmarketos \
    M=drivers/tee/qcomtee modules
  [[ $(make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" -s kernelrelease) == "$kernel_release" ]] ||
    die 'kernel release differs'
)

mkdir -p "$output_dir"
install -m 0644 "$tree/drivers/tee/qcomtee/qcomtee.ko" "$output_dir/qcomtee-control.ko"
[[ $(modinfo -F vermagic "$output_dir/qcomtee-control.ko" | awk '{print $1}') == "$kernel_release" ]] ||
  die 'module vermagic differs'
[[ $(modinfo -F name "$output_dir/qcomtee-control.ko") == qcomtee ]] || die 'module name differs'
[[ -z $(modinfo -F depends "$output_dir/qcomtee-control.ko") ]] || die 'module dependencies differ'
[[ -z $(modinfo -F signer "$output_dir/qcomtee-control.ko") ]] || die 'module unexpectedly signed'

{
	printf 'LUMA_FP6_QCOMTEE_CONTROL_BUILD_VERSION=41\n'
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'RUNTIME_CONFIG_SHA256=%s\n' "$expected_config_sha"
  printf 'SOURCE_ARCHIVE_SHA256=%s\n' "$expected_source_archive_sha"
  printf 'MODULE_SYMVERS_SHA256=%s\n' "$expected_symvers_sha"
  printf 'CONTROL_SOURCE_SHA256=%s\n' "$expected_overlay_sha"
  printf 'INTEGRATION_PATCH_SHA256=%s\n' "$expected_patch_sha"
  printf 'FOCAL_CONFIG_EXTRACTOR_SHA256=%s\n' "$expected_extractor_sha"
  printf 'FOCAL_STOCK_HAL_SHA256=%s\n' "$expected_stock_hal_sha"
  printf 'FOCAL_SYNC_CONFIG_SHA256=%s\n' "$expected_focal_config_sha"
  printf 'FOCAL_CONFIG_HEADER_SHA256=%s\n' "$expected_focal_header_sha"
  printf 'MODULE_SHA256=%s\n' "$(hash "$output_dir/qcomtee-control.ko")"
  printf 'MODULE_SIGNED=false\n'
  printf 'MODULE_SIGNATURE_ENFORCED_BY_RUNTIME=false\n'
  printf 'CONTROL_APPS=smplap64,focal64,keymaster64\n'
  printf 'CONTROL_DISTINGUISHED_IDENTITIES=smplap64,focal64,keymaster\n'
  printf 'CONTROL_LOADERS=QSEEComCompat-122,AppLoader-3\n'
	printf 'TA_COMMANDS_ALLOWED=keymaster64-load,android-keymint-credential,keymaster-get-version,keymaster-set-client-version,keymaster-set-keymint-version,keymaster-get-hmac-sharing-params,keymaster-compute-sharing-hmac,keymaster64-unload,keymaster-stock-qseecom-encapsulated-key,focal-key-sync,focal-sync-config,focal-init-spi,focal-probe-device,focal-init-device,focal-initialize,focal-enumerate,focal-free-spi,keymaster-lookup\n'
  printf 'BIOMETRIC_COMMANDS_ALLOWED=enumerate-only\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

trap - EXIT INT TERM
cleanup
printf 'FP6 QCOMTEE control-v20 module: %s\n' "$output_dir/qcomtee-control.ko"
