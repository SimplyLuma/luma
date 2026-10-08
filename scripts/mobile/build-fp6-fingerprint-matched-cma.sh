#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

# Build a rollback-safe FP6 kernel plus complete matching module tree with the
# stock reusable/CMA allocator contract. This runs only in the isolated Lima
# builder and never contacts a phone or includes proprietary firmware.

set -eu
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

build_root=${1:?usage: build-fp6-fingerprint-matched-cma.sh BASE_BUILD_ROOT OUTPUT_DIR}
output_dir=${2:?missing output directory}
rootfs=$build_root/lab/root
source_tree=$rootfs/work/linux
cma_tree=$rootfs/work/linux-fingerprint-matched-cma-v14
install_tree=$rootfs/work/fingerprint-matched-cma-v14-modules
kernel_release=7.1.2-luma-fp-cma1
localversion=-luma-fp-cma1
build_timestamp='2026-08-22 00:00:00 UTC'
jobs=${LUMA_KERNEL_BUILD_JOBS:-8}
resume=${LUMA_MATCHED_CMA_RESUME:-false}
signing_key=${LUMA_MATCHED_CMA_SIGNING_KEY:-}
module_patch=${LUMA_MATCHED_CMA_MODULE_PATCH:-}
module_patch_sha=${LUMA_MATCHED_CMA_MODULE_PATCH_SHA256:-}
enable_qcomtee=${LUMA_MATCHED_CMA_ENABLE_QCOMTEE:-false}
signing_key_sha=94f151fcd2cec29112beb22cf226ea9cc2acb5f000de05b518ffab7657e14220

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

case "$jobs" in ''|*[!0-9]*) die 'LUMA_KERNEL_BUILD_JOBS must be numeric' ;; esac
[ "$jobs" -ge 1 ] || die 'LUMA_KERNEL_BUILD_JOBS must be positive'
case "$resume" in true|false) ;; *) die 'LUMA_MATCHED_CMA_RESUME must be true or false' ;; esac
case "$enable_qcomtee" in true|false) ;; *) die 'LUMA_MATCHED_CMA_ENABLE_QCOMTEE must be true or false' ;; esac
[ "$(id -u)" -eq 0 ] || die 'run as root in the isolated builder'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || die 'builder must be Linux/aarch64'
clang --version | head -n 1 | grep -Fx 'Ubuntu clang version 21.1.8 (6ubuntu1)' >/dev/null ||
  die 'native AArch64 Clang 21.1.8 differs'
pahole --version | grep -Fx 'v1.31' >/dev/null || die 'pahole version differs'
[ -d "$source_tree" ] || die 'accepted patched source tree is missing'
if [ "$resume" = true ]; then
  [ -d "$output_dir/bundle" ] || die 'resume output bundle is absent'
  resume_file_count=6
  if [ "$enable_qcomtee" = true ]; then
    resume_file_count=7
  fi
  [ "$(find "$output_dir" -type f | wc -l | tr -d ' ')" -eq "$resume_file_count" ] ||
    die 'resume output file count differs'
  [ "$(find "$output_dir" -type l | wc -l | tr -d ' ')" -eq 0 ] || die 'resume output contains a symlink'
  [ -d "$cma_tree" ] || die 'resume CMA build tree is absent'
  [ -d "$install_tree" ] || die 'resume install tree is absent'
else
  [ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
  [ ! -e "$cma_tree" ] || die "stale CMA build tree exists: $cma_tree"
  [ ! -e "$install_tree" ] || die "stale CMA install tree exists: $install_tree"
fi
[ "$(hash "$source_tree/.config")" = "$FP6_FINGERPRINT_QSEELOG_CONFIG_SHA256" ] || die 'accepted source config differs'
[ "$(hash "$source_tree/arch/arm64/boot/vmlinuz")" = "$FP6_FINGERPRINT_QSEELOG_KERNEL_SHA256" ] || die 'accepted source kernel differs'
grep -Fq 'dedicated QSEECOM heaps active' "$source_tree/drivers/tee/qseecom/core.c" || die 'dedicated-heaps source absent'
grep -Fq 'root-only QSEE diagnostic log registered' "$source_tree/drivers/firmware/qcom/qcom_scm.c" || die 'QSEE-log source absent'

if [ "$resume" = false ]; then
  # Hard-link the immutable accepted tree, then remove build products in the
  # clone. `make clean` unlinks generated files before rebuilding, so the
  # source tree and its accepted artifacts remain byte-exact without consuming
  # another 4.9 GiB up front.
  cp -al "$source_tree" "$cma_tree"

  (
    cd "$cma_tree"
    make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" clean
    if [ -n "$module_patch" ]; then
      [ -f "$module_patch" ] && [ ! -L "$module_patch" ] || die 'module patch identity differs'
      [ -n "$module_patch_sha" ] || die 'module patch hash is required'
      [ "$(hash "$module_patch")" = "$module_patch_sha" ] || die 'module patch hash differs'
      patch -d "$cma_tree" -p1 --fuzz=0 --no-backup-if-mismatch <"$module_patch"
    fi
    # The kernel otherwise creates a random development module-signing key and
    # certificate, changing both the kernel and every installed module on each
    # clean build. This pinned PEM is the exact combined private key and X.509
    # certificate from the accepted reproducibility build. It stays private to
    # the isolated builder and is not a production trust root.
    [ -n "$signing_key" ] || die 'LUMA_MATCHED_CMA_SIGNING_KEY is required'
    [ -f "$signing_key" ] && [ ! -L "$signing_key" ] || die 'signing key identity differs'
    [ "$(hash "$signing_key")" = "$signing_key_sha" ] || die 'signing key hash differs'
    scripts/config --enable DMA_CMA
    scripts/config --set-val CMA_SIZE_MBYTES 32
    scripts/config --enable CMA_SIZE_SEL_MBYTES
    scripts/config --disable CMA_SIZE_SEL_PERCENTAGE
    scripts/config --disable CMA_SIZE_SEL_MIN
    scripts/config --disable CMA_SIZE_SEL_MAX
    if [ "$enable_qcomtee" = true ]; then
      scripts/config --module QCOMTEE
    fi
    make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" olddefconfig
    # Keep CONFIG_MODULE_SIG_KEY byte-identical to the accepted build while
    # disabling only Kbuild's FORCE regeneration rule in this disposable
    # clone. Generate its static OpenSSL config first, then install the pinned
    # combined PEM so it is newer and is used as-is by the full build.
    grep -Fqx '$(obj)/signing_key.pem: $(obj)/x509.genkey FORCE' certs/Makefile ||
      die 'module-signing generation rule differs'
    sed -i 's|^$(obj)/signing_key.pem: $(obj)/x509.genkey FORCE$|$(obj)/signing_key.pem: $(obj)/x509.genkey|' certs/Makefile
    make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" certs/x509.genkey
    install -m 0600 "$signing_key" certs/signing_key.pem
    grep -qx 'CONFIG_DMA_CMA=y' .config
    grep -qx 'CONFIG_CMA_SIZE_MBYTES=32' .config
    grep -qx 'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y' .config
    grep -qx 'CONFIG_TEE_QSEECOM=m' .config
    grep -qx 'CONFIG_FINGER_FOCAL=m' .config
    if [ "$enable_qcomtee" = true ]; then
      grep -qx 'CONFIG_QCOMTEE=m' .config
    else
      grep -qx '# CONFIG_QCOMTEE is not set' .config
    fi
    [ "$(make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" -s kernelrelease)" = "$kernel_release" ]
    make -j"$jobs" ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" \
      KBUILD_BUILD_USER=pmos KBUILD_BUILD_HOST=build \
      KBUILD_BUILD_TIMESTAMP="$build_timestamp" \
      KBUILD_BUILD_VERSION=1-postmarketos vmlinuz.efi modules
    make ARCH=arm64 LLVM=1 LOCALVERSION="$localversion" \
      INSTALL_MOD_PATH="$install_tree" modules_install
  )
fi
[ -d "$install_tree/lib/modules/$kernel_release" ] || die 'installed native module tree absent'
rm -f "$install_tree/lib/modules/$kernel_release/build"
rm -f "$install_tree/lib/modules/$kernel_release/source"
module_root=$install_tree/lib/modules/$kernel_release

bundle=$output_dir/bundle
mkdir -p "$bundle"
install -m 0644 "$cma_tree/arch/arm64/boot/vmlinuz" "$bundle/Image.gz"
install -m 0644 "$cma_tree/.config" "$bundle/config"
install -m 0644 "$cma_tree/drivers/tee/qseecom/qseecomtee.ko" "$bundle/qseecomtee.ko"
install -m 0644 "$cma_tree/drivers/input/finger/focal_finger/focaltech_fp.ko" "$bundle/focaltech_fp.ko"
if [ "$enable_qcomtee" = true ]; then
  # modules_install applies the pinned PKCS#7 signature before compression;
  # export that signed artifact rather than the unsigned build-tree module.
  zstd -q -d -c "$module_root/kernel/drivers/tee/qcomtee/qcomtee.ko.zst" >"$bundle/qcomtee.ko"
  chmod 0644 "$bundle/qcomtee.ko"
fi

[ -d "$module_root" ] || die 'installed module root missing'
module_count=$(find "$module_root" -type f \( -name '*.ko' -o -name '*.ko.zst' -o -name '*.ko.xz' -o -name '*.ko.gz' \) | wc -l | tr -d ' ')
ordered_module_count=$(sed '/^[[:space:]]*$/d' "$cma_tree/modules.order" | wc -l | tr -d ' ')
built_module_count=$(find "$cma_tree" -type f -name '*.ko' | wc -l | tr -d ' ')
[ "$module_count" -gt 0 ] || die 'installed module tree is empty'
[ "$module_count" -eq "$ordered_module_count" ] ||
  die "installed modules differ from modules.order: $module_count/$ordered_module_count"
[ "$module_count" -eq "$built_module_count" ] ||
  die "installed modules differ from built modules: $module_count/$built_module_count"
tar --sort=name --mtime='2026-08-22 00:00:00 UTC' --owner=0 --group=0 \
  --numeric-owner -C "$install_tree" -cf - lib/modules/$kernel_release |
  zstd -q -19 -T0 -o "$bundle/modules-$kernel_release.tar.zst"

gzip -t "$bundle/Image.gz"
modules='qseecomtee.ko focaltech_fp.ko'
if [ "$enable_qcomtee" = true ]; then
  modules="$modules qcomtee.ko"
fi
for module in $modules; do
  vermagic=$(modinfo -F vermagic "$bundle/$module")
  [ "${vermagic%% *}" = "$kernel_release" ] || die "$module vermagic differs: $vermagic"
done

{
  printf 'LUMA_FP6_FINGERPRINT_MATCHED_CMA_BUILD_VERSION=14\n'
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'BASE_KERNEL_SHA256=%s\n' "$FP6_FINGERPRINT_QSEELOG_KERNEL_SHA256"
  printf 'BASE_CONFIG_SHA256=%s\n' "$FP6_FINGERPRINT_QSEELOG_CONFIG_SHA256"
  printf 'IMAGE_SHA256=%s\n' "$(hash "$bundle/Image.gz")"
  printf 'CONFIG_SHA256=%s\n' "$(hash "$bundle/config")"
  printf 'QSEECOMTEE_KO_SHA256=%s\n' "$(hash "$bundle/qseecomtee.ko")"
  printf 'FOCALTECH_FP_KO_SHA256=%s\n' "$(hash "$bundle/focaltech_fp.ko")"
  printf 'MODULE_TREE_SHA256=%s\n' "$(hash "$bundle/modules-$kernel_release.tar.zst")"
  printf 'MODULE_COUNT=%s\n' "$module_count"
  printf 'DMA_CMA_ENABLED=true\n'
  printf 'CMA_SIZE_MBYTES=32\n'
  printf 'TZMEM_MODE=shmbridge\n'
  printf 'DEDICATED_QSEECOM_HEAPS=true\n'
  printf 'QSEELOG_ENABLED=true\n'
  printf 'QCOMTEE_MODULE_ENABLED=%s\n' "$enable_qcomtee"
  if [ "$enable_qcomtee" = true ]; then
    printf 'QCOMTEE_KO_SHA256=%s\n' "$(hash "$bundle/qcomtee.ko")"
  fi
  printf 'COMPLETE_MATCHING_MODULE_TREE=true\n'
  printf 'REPRODUCIBLE_SIGNING_KEY_SHA256=%s\n' "$signing_key_sha"
  if [ -n "$module_patch" ]; then
    printf 'MODULE_PATCH_SHA256=%s\n' "$module_patch_sha"
  fi
  printf 'NATIVE_AARCH64_BUILD=true\n'
  printf 'CLANG_VERSION=21.1.8\n'
  printf 'PROPRIETARY_PAYLOADS_INCLUDED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$bundle/manifest.env"
chmod 0644 "$bundle/manifest.env"

# Reclaim only the disposable hard-link clone and install staging tree. The
# accepted source tree remains intact and independently hash-gated above.
find "$cma_tree" -depth -delete
find "$install_tree" -depth -delete

printf 'FP6 matched-CMA kernel/module bundle: %s\n' "$bundle"
