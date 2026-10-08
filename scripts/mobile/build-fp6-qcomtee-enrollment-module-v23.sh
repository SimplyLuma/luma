#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build the temporary root-only QTEE enrollment transport. The resulting
# module is unsigned, RAM-only, and tied to the exact accepted FP6 kernel.

set -Eeuo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
source_archive=$repo_root/build/mobile/fp6-physical/fp6-camera-source-archives/linux-v7.1.2-milos.tar.gz
symvers=$repo_root/build/mobile/fp6-physical/fp6-camera-source-archives/Module.symvers
runtime_config=${LUMA_FP6_QCOMTEE_RUNTIME_CONFIG:-$repo_root/build/mobile/fp6-physical/fp6-fingerprint-matched-cma-v14/bundle/config}
control_source=$repo_root/src/fp6-fingerprint-qcomtee/qcomtee-control.c
extension=$repo_root/src/fp6-fingerprint-qcomtee/qcomtee-enroll-extension.inc
abi_header=$repo_root/src/fp6-fingerprint-qcomtee/luma-fp6-enrollment-abi.h
listener_extension=$repo_root/src/fp6-fingerprint-qcomtee/qcomtee-listener-extension.inc
listener_abi_header=$repo_root/src/fp6-fingerprint-qcomtee/luma-fp6-listener-abi.h
integration_patch=$repo_root/patches/linux-qcomtee-control-v18/0001-qcomtee-add-bounded-luma-control-identity-hook.patch
persistent_shm_patch=$repo_root/patches/linux-qcomtee-control-v24/0001-qcomtee-retain-one-bounded-invoke-buffer-pair.patch
kernel_shm_patch=$repo_root/patches/linux-qcomtee-control-v25/0001-qcomtee-wrap-kernel-shm-as-memory-object.patch
challenge_patch=$repo_root/patches/linux-qcomtee-control-v22/0001-qcomtee-add-bounded-FocalTech-challenge-cycle.patch
enrollment_patch=$repo_root/patches/linux-qcomtee-control-v23/0001-qcomtee-wire-root-only-fingerprint-enrollment-transport.patch
extractor=$repo_root/scripts/mobile/extract-fp6-focal-config.py
stock_hal=$repo_root/build/mobile/fp6-physical/fp6-stock-hal-qrel1695/fingerprint.default.so
output_dir=${1:?usage: build-fp6-qcomtee-enrollment-module-v23.sh OUTPUT_DIR}
kernel_release=${LUMA_FP6_QCOMTEE_KERNEL_RELEASE:-7.1.2-luma-fp-cma1}
localversion=${LUMA_FP6_QCOMTEE_LOCALVERSION:--luma-fp-cma1}
jobs=${LUMA_KERNEL_BUILD_JOBS:-8}
linux_native_enrollment=${LUMA_FP6_LINUX_NATIVE_ENROLLMENT:-false}

expected_source_archive_sha=6043a062d595e1b4913512c3414387d9a016c4690d8832720651df07276dc78b
expected_symvers_sha=0136080d85bcb215aad56324ed61259c1581400d0159489c664b75b476de04e0
expected_config_sha=${LUMA_FP6_QCOMTEE_RUNTIME_CONFIG_SHA256:-5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe}
expected_control_source_sha=d9590b359ee72b235c8c0fa28e5fac8f6d8cbff7dffacaaa5f3337e7443b394c
expected_extension_sha=628a26aabfd111b3d6b9264687e5188e58b5573e6d9155b9ec194f54327c613f
expected_abi_sha=a19a98c229d10b4e439b236617d3f3a87f3c0727f3f755beb944eeccb69ce75d
expected_listener_extension_sha=5fe2bee849f633e2e7b6842d122d0c00f1a45a2b127801c5ec2bc3a0821c888d
expected_listener_abi_sha=a856d0c47d4264369c83d97ca67b054b3765251c157143700114d6e8f644a460
expected_integration_patch_sha=e67edc32cdd7f2812b24ee8f6ad732892fea5def6e68938f0134f65be76b944d
expected_persistent_shm_patch_sha=699737d9d8e07fc690e46c9874a734faed46e6a616fb3c01899e3e1bd078ccfc
expected_kernel_shm_patch_sha=ddc0377c526861bc10559a64096a7d9fbabb6a0a9f86cab92a869a9550508420
expected_challenge_patch_sha=f9fb71a48b9d5017c85a05e584c752d1ee357f8118c5cea671e593894b8c3a20
expected_enrollment_patch_sha=e9e498d0b83ffb6aa3f491ec0c3b13f46d8d8800c8667a183e6c95d328534cb9
expected_extractor_sha=4a4179d7ce319635e1a1ba1fdcee4686281533cb1234bfeef3d5eb3737f249c4
expected_stock_hal_sha=ae08b39c8c78b40a769795684afc7d342c19acf8c1b443fdc56be47b5ca8bedd
expected_focal_header_sha=5ad0afbf523607a26d5e8bd0b6b655cbfdf93bf97617e51db877c71a034e334c

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

if [[ $linux_native_enrollment == true ]]; then
  expected_focal_header_sha=73144f954a9da47b3db4d83b26730600e7cc9b01b29a271029fcf28d65a92e94
elif [[ $linux_native_enrollment != false ]]; then
  die 'LUMA_FP6_LINUX_NATIVE_ENROLLMENT must be true or false'
fi

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
[[ $(hash "$control_source") == "$expected_control_source_sha" ]] || die 'control source differs'
[[ $(hash "$extension") == "$expected_extension_sha" ]] || die 'enrollment extension differs'
[[ $(hash "$abi_header") == "$expected_abi_sha" ]] || die 'enrollment ABI differs'
[[ $(hash "$listener_extension") == "$expected_listener_extension_sha" ]] || die 'listener extension differs'
[[ $(hash "$listener_abi_header") == "$expected_listener_abi_sha" ]] || die 'listener ABI differs'
[[ $(hash "$integration_patch") == "$expected_integration_patch_sha" ]] || die 'integration patch differs'
[[ $(hash "$persistent_shm_patch") == "$expected_persistent_shm_patch_sha" ]] || die 'persistent SHM patch differs'
[[ $(hash "$kernel_shm_patch") == "$expected_kernel_shm_patch_sha" ]] || die 'kernel SHM patch differs'
[[ $(hash "$challenge_patch") == "$expected_challenge_patch_sha" ]] || die 'challenge patch differs'
[[ $(hash "$enrollment_patch") == "$expected_enrollment_patch_sha" ]] || die 'enrollment patch differs'
[[ $(hash "$extractor") == "$expected_extractor_sha" ]] || die 'configuration extractor differs'
[[ $(hash "$stock_hal") == "$expected_stock_hal_sha" ]] || die 'stock fingerprint HAL differs'

work_dir=$(mktemp -d /tmp/luma-fp6-qcomtee-enrollment-v23.XXXXXX)
cleanup() { find "$work_dir" -depth -delete 2>/dev/null || true; }
trap cleanup EXIT INT TERM

tar -xzf "$source_archive" -C "$work_dir"
tree=$work_dir/linux
install -m 0644 "$runtime_config" "$tree/.config"
install -m 0644 "$symvers" "$tree/Module.symvers"
printf '0x00000000\tqcom_tzmem_shm_bridge_create\tvmlinux\tEXPORT_SYMBOL_GPL\t\n' >>"$tree/Module.symvers"
printf '0x00000000\tqcom_tzmem_shm_bridge_delete\tvmlinux\tEXPORT_SYMBOL_GPL\t\n' >>"$tree/Module.symvers"
(
  cd "$tree"
  prefix_flags="-fdebug-prefix-map=$tree=/usr/src/linux -ffile-prefix-map=$tree=/usr/src/linux -fmacro-prefix-map=$tree=/usr/src/linux"
  scripts/config --module QCOMTEE
  make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" olddefconfig
  grep -qx 'CONFIG_QCOMTEE=m' .config || die 'QCOMTEE config differs'
  grep -qx '# CONFIG_MODVERSIONS is not set' .config || die 'module versioning policy differs'
  make -j"$jobs" ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" KCFLAGS="$prefix_flags" modules_prepare
  patch -p1 --fuzz=0 --no-backup-if-mismatch <"$integration_patch"
  patch -p1 --fuzz=0 --no-backup-if-mismatch <"$persistent_shm_patch"
  patch -p1 --fuzz=0 --no-backup-if-mismatch <"$kernel_shm_patch"
  install -m 0644 "$control_source" drivers/tee/qcomtee/qcomtee-control.c
  patch -p1 --fuzz=0 --no-backup-if-mismatch <"$challenge_patch"
  install -m 0644 "$extension" drivers/tee/qcomtee/qcomtee-enroll-extension.inc
  install -m 0644 "$abi_header" drivers/tee/qcomtee/luma-fp6-enrollment-abi.h
  install -m 0644 "$listener_extension" drivers/tee/qcomtee/qcomtee-listener-extension.inc
  install -m 0644 "$listener_abi_header" drivers/tee/qcomtee/luma-fp6-listener-abi.h
  patch -p1 --fuzz=0 --no-backup-if-mismatch <"$enrollment_patch"
  extractor_args=(--format c-header --nul)
  if [[ $linux_native_enrollment == true ]]; then
    extractor_args+=(--linux-native-enrollment)
  fi
  python3 "$extractor" "$stock_hal" drivers/tee/qcomtee/luma-focal-config.h "${extractor_args[@]}"
  [[ $(hash drivers/tee/qcomtee/luma-focal-config.h) == "$expected_focal_header_sha" ]] ||
    die 'generated FocalTech configuration header differs'
  make -j"$jobs" ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" \
    KCFLAGS="$prefix_flags" KBUILD_BUILD_USER=pmos KBUILD_BUILD_HOST=build \
    KBUILD_BUILD_TIMESTAMP='2026-08-23 00:00:00 UTC' \
    KBUILD_BUILD_VERSION=1-postmarketos M=drivers/tee/qcomtee modules
  [[ $(make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" -s kernelrelease) == "$kernel_release" ]] ||
    die 'kernel release differs'
)

mkdir -p "$output_dir"
install -m 0644 "$tree/drivers/tee/qcomtee/qcomtee.ko" "$output_dir/qcomtee-enrollment.ko"
[[ $(modinfo -F vermagic "$output_dir/qcomtee-enrollment.ko" | awk '{print $1}') == "$kernel_release" ]] ||
  die 'module vermagic differs'
[[ $(modinfo -F name "$output_dir/qcomtee-enrollment.ko") == qcomtee ]] || die 'module name differs'
[[ -z $(modinfo -F signer "$output_dir/qcomtee-enrollment.ko") ]] || die 'module unexpectedly signed'

{
  if [[ $linux_native_enrollment == true ]]; then
    printf 'LUMA_FP6_QCOMTEE_ENROLLMENT_BUILD_VERSION=44\n'
  else
    printf 'LUMA_FP6_QCOMTEE_ENROLLMENT_BUILD_VERSION=44\n'
  fi
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'RUNTIME_CONFIG_SHA256=%s\n' "$expected_config_sha"
  printf 'CONTROL_SOURCE_SHA256=%s\n' "$expected_control_source_sha"
  printf 'ENROLLMENT_EXTENSION_SHA256=%s\n' "$expected_extension_sha"
  printf 'ENROLLMENT_ABI_SHA256=%s\n' "$expected_abi_sha"
  printf 'LISTENER_EXTENSION_SHA256=%s\n' "$expected_listener_extension_sha"
  printf 'LISTENER_ABI_SHA256=%s\n' "$expected_listener_abi_sha"
  printf 'ENROLLMENT_PATCH_SHA256=%s\n' "$expected_enrollment_patch_sha"
  printf 'MODULE_SHA256=%s\n' "$(hash "$output_dir/qcomtee-enrollment.ko")"
  printf 'MODULE_SIGNED=false\n'
  printf 'DEVICE_MODE=0600\n'
  printf 'PIN_LOGGED=false\n'
  printf 'HAT_LOGGED=false\n'
  printf 'RAW_IMAGES_ALLOWED=false\n'
  printf 'LINUX_NATIVE_ENROLLMENT_CONFIG=%s\n' "$linux_native_enrollment"
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

trap - EXIT INT TERM
cleanup
printf 'FP6 QCOMTEE enrollment-v23 module: %s\n' "$output_dir/qcomtee-enrollment.ko"
