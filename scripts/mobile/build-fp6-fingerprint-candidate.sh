#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

# Add the pinned QSEECOM transport, ELF64 application-image assembly support,
# and Fairphone FocalTech control shim to the exact, already-built FP6 source
# stack. This runs only in the off-device Lima builder and produces artifacts;
# it never contacts a phone, loads a trustlet, or enrolls a fingerprint.

set -eu
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

build_root=${1:?usage: build-fp6-fingerprint-candidate.sh BASE_BUILD_ROOT OUTPUT_DIR}
output_dir=${2:?usage: build-fp6-fingerprint-candidate.sh BASE_BUILD_ROOT OUTPUT_DIR}
rootfs=$build_root/lab/root
source_tree=$rootfs/work/linux
inputs=$rootfs/work/inputs
qsee_patch_dir=$repo_root/patches/linux-qseecom
shmbridge_patch_dir=$repo_root/patches/linux-qseecom-shmbridge
stock_heaps_patch_dir=$repo_root/patches/linux-qseecom-stock-heaps
dedicated_heaps_patch_dir=$repo_root/patches/linux-qseecom-dedicated-heaps
qseelog_patch_dir=$repo_root/patches/linux-qseecom-qseelog
driver_patch_dir=$repo_root/patches/linux-milos-fingerprint
# Consume the clean, pinned microphone stage immediately before its historical
# incremental BTF-fallback relink. Clean images contain generated signing/BTF
# material and are not byte-reproducible, so the immutable gates are the
# source/patch inputs, exact configuration, toolchain, and build timestamp.
base_config_sha=67b55b06313dac5f69d6939459fb58791cef4f656cbf7564a881b17c2fdab4c0
build_timestamp='2026-08-19 00:00:00 UTC'
jobs=${LUMA_KERNEL_BUILD_JOBS:-4}
resume=${LUMA_FINGERPRINT_BUILD_RESUME:-false}
tzmem_mode=${LUMA_FINGERPRINT_TZMEM_MODE:-generic}
stock_heaps=${LUMA_FINGERPRINT_STOCK_HEAPS:-false}
dedicated_heaps=${LUMA_FINGERPRINT_DEDICATED_HEAPS:-false}
qseelog=${LUMA_FINGERPRINT_QSEELOG:-false}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

hash() {
  sha256sum "$1" | awk '{print $1}'
}

hash_patchset() {
  patch_dir=$1
  (
    cd "$patch_dir"
    find . -maxdepth 1 -type f -name '*.patch' | LC_ALL=C sort |
      while IFS= read -r patch; do
        printf '%s  %s\n' "$(hash "$patch")" "$patch"
      done
  ) | sha256sum | awk '{print $1}'
}

case "$jobs" in
  ''|*[!0-9]*) die 'LUMA_KERNEL_BUILD_JOBS must be a positive integer' ;;
esac
[ "$jobs" -ge 1 ] || die 'LUMA_KERNEL_BUILD_JOBS must be at least 1'
[ "$(id -u)" -eq 0 ] || die 'run as root on the isolated builder'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] ||
  die 'run only on an off-device Linux/aarch64 builder'
[ -x /mnt/lima-rosetta/rosetta ] || die 'Lima Rosetta is unavailable'
if [ -r /sys/firmware/devicetree/base/model ]; then
  model=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$model" in *Fairphone*) die 'refuse to compile on the FP6 target' ;; esac
fi
[ -d "$source_tree" ] || die 'exact camera/audio source tree is missing'
[ -f "$source_tree/arch/arm64/boot/vmlinuz" ] || die 'base kernel is missing'
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
case "$resume" in true|false) ;; *) die 'LUMA_FINGERPRINT_BUILD_RESUME must be true or false' ;; esac
case "$tzmem_mode" in
  generic|shmbridge) ;;
  *) die 'LUMA_FINGERPRINT_TZMEM_MODE must be generic or shmbridge' ;;
esac
case "$stock_heaps" in true|false) ;; *) die 'LUMA_FINGERPRINT_STOCK_HEAPS must be true or false' ;; esac
case "$dedicated_heaps" in true|false) ;; *) die 'LUMA_FINGERPRINT_DEDICATED_HEAPS must be true or false' ;; esac
case "$qseelog" in true|false) ;; *) die 'LUMA_FINGERPRINT_QSEELOG must be true or false' ;; esac
[ "$stock_heaps" = false ] || [ "$tzmem_mode" = shmbridge ] ||
  die 'stock QSEECOM heaps require SHM-Bridge mode'
[ "$dedicated_heaps" = false ] || [ "$stock_heaps" = true ] ||
  die 'dedicated QSEECOM heaps require the stock-heaps base patch'
if [ "$resume" = false ]; then
  [ "$(hash "$source_tree/.config")" = "$base_config_sha" ] ||
    die 'base camera/audio configuration differs'
fi
if [ "$resume" = false ]; then
  base_image_sha=$(hash "$source_tree/arch/arm64/boot/vmlinuz")
  base_image_sha_recorded=true
else
  # The resumed tree may already contain a candidate image. Do not relabel it
  # as the immutable input image merely because the interrupted build did not
  # reach manifest creation.
  base_image_sha=UNRECORDED
  base_image_sha_recorded=false
fi

qsee_count=$(find "$qsee_patch_dir" -maxdepth 1 -type f -name '*.patch' | wc -l | tr -d ' ')
[ "$qsee_count" = "$FP6_FINGERPRINT_QSEECOM_PATCH_COUNT" ] ||
  die 'QSEECom patch count differs'
[ "$(hash_patchset "$qsee_patch_dir")" = "$FP6_FINGERPRINT_QSEECOM_PATCHSET_SHA256" ] ||
  die 'QSEECom patchset digest differs'
driver_count=$(find "$driver_patch_dir" -maxdepth 1 -type f -name '*.patch' | wc -l | tr -d ' ')
[ "$driver_count" = "$FP6_FINGERPRINT_DRIVER_PATCH_COUNT" ] ||
  die 'FocalTech patch count differs'
[ "$(hash_patchset "$driver_patch_dir")" = "$FP6_FINGERPRINT_DRIVER_PATCHSET_SHA256" ] ||
  die 'FocalTech patchset digest differs'

shmbridge_count=0
shmbridge_patchset_sha=NONE
if [ "$tzmem_mode" = shmbridge ]; then
  shmbridge_count=$(find "$shmbridge_patch_dir" -maxdepth 1 -type f \
    -name '*.patch' | wc -l | tr -d ' ')
  [ "$shmbridge_count" = "$FP6_FINGERPRINT_SHMBRIDGE_PATCH_COUNT" ] ||
    die 'SHM-Bridge diagnostic patch count differs'
  shmbridge_patchset_sha=$(hash_patchset "$shmbridge_patch_dir")
  [ "$shmbridge_patchset_sha" = "$FP6_FINGERPRINT_SHMBRIDGE_PATCHSET_SHA256" ] ||
    die 'SHM-Bridge diagnostic patchset digest differs'
fi

stock_heaps_count=0
stock_heaps_patchset_sha=NONE
if [ "$stock_heaps" = true ]; then
  stock_heaps_count=$(find "$stock_heaps_patch_dir" -maxdepth 1 -type f \
    -name '*.patch' | wc -l | tr -d ' ')
  [ "$stock_heaps_count" = "$FP6_FINGERPRINT_STOCK_HEAPS_PATCH_COUNT" ] ||
    die 'stock-heaps patch count differs'
  stock_heaps_patchset_sha=$(hash_patchset "$stock_heaps_patch_dir")
  [ "$stock_heaps_patchset_sha" = "$FP6_FINGERPRINT_STOCK_HEAPS_PATCHSET_SHA256" ] ||
    die 'stock-heaps patchset digest differs'
fi

dedicated_heaps_count=0
dedicated_heaps_patchset_sha=NONE
if [ "$dedicated_heaps" = true ]; then
  dedicated_heaps_count=$(find "$dedicated_heaps_patch_dir" -maxdepth 1 \
    -type f -name '*.patch' | wc -l | tr -d ' ')
  [ "$dedicated_heaps_count" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_PATCH_COUNT" ] ||
    die 'dedicated-heaps patch count differs'
  dedicated_heaps_patchset_sha=$(hash_patchset "$dedicated_heaps_patch_dir")
  [ "$dedicated_heaps_patchset_sha" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_PATCHSET_SHA256" ] ||
    die 'dedicated-heaps patchset digest differs'
fi

qseelog_count=0
qseelog_patchset_sha=NONE
if [ "$qseelog" = true ]; then
  qseelog_count=$(find "$qseelog_patch_dir" -maxdepth 1 -type f \
    -name '*.patch' | wc -l | tr -d ' ')
  [ "$qseelog_count" = "$FP6_FINGERPRINT_QSEELOG_PATCH_COUNT" ] ||
    die 'QSEE-log patch count differs'
  qseelog_patchset_sha=$(hash_patchset "$qseelog_patch_dir")
  [ "$qseelog_patchset_sha" = "$FP6_FINGERPRINT_QSEELOG_PATCHSET_SHA256" ] ||
    die 'QSEE-log patchset digest differs'
fi

stage=$inputs/luma-fingerprint
mkdir -p "$stage/qsee" "$stage/focaltech" "$stage/shmbridge" \
  "$stage/stock-heaps" "$stage/dedicated-heaps" "$stage/qseelog"
for patch in "$qsee_patch_dir"/*.patch; do
  install -m 0644 "$patch" "$stage/qsee/$(basename "$patch")"
done
for patch in "$driver_patch_dir"/*.patch; do
  install -m 0644 "$patch" "$stage/focaltech/$(basename "$patch")"
done
if [ "$tzmem_mode" = shmbridge ]; then
  for patch in "$shmbridge_patch_dir"/*.patch; do
    install -m 0644 "$patch" "$stage/shmbridge/$(basename "$patch")"
  done
fi
if [ "$stock_heaps" = true ]; then
  for patch in "$stock_heaps_patch_dir"/*.patch; do
    install -m 0644 "$patch" "$stage/stock-heaps/$(basename "$patch")"
  done
fi
if [ "$dedicated_heaps" = true ]; then
  for patch in "$dedicated_heaps_patch_dir"/*.patch; do
    install -m 0644 "$patch" "$stage/dedicated-heaps/$(basename "$patch")"
  done
fi
if [ "$qseelog" = true ]; then
  for patch in "$qseelog_patch_dir"/*.patch; do
    install -m 0644 "$patch" "$stage/qseelog/$(basename "$patch")"
  done
fi

mounted_dev=false
mounted_proc=false
mounted_rosetta=false
mounted_sys=false
cleanup() {
  if [ "$mounted_rosetta" = true ]; then umount -l -R "$rootfs/mnt/lima-rosetta" 2>/dev/null || true; fi
  if [ "$mounted_sys" = true ]; then umount -l -R "$rootfs/sys" 2>/dev/null || true; fi
  if [ "$mounted_proc" = true ]; then umount -l "$rootfs/proc" 2>/dev/null || true; fi
  if [ "$mounted_dev" = true ]; then umount -l -R "$rootfs/dev" 2>/dev/null || true; fi
}
trap cleanup EXIT HUP INT TERM

mount --rbind /dev "$rootfs/dev"; mounted_dev=true
mount -t proc proc "$rootfs/proc"; mounted_proc=true
mount --rbind /sys "$rootfs/sys"; mounted_sys=true
mount --rbind /mnt/lima-rosetta "$rootfs/mnt/lima-rosetta"; mounted_rosetta=true

chroot "$rootfs" /bin/sh -eu -c "
  clang --version | head -n 1 | grep -Fx 'Alpine clang version 22.1.8'
  pahole --version | grep -Fx 'v1.31'
  cd /work/linux
  if [ '$resume' = false ]; then
    for patch in /work/inputs/luma-fingerprint/qsee/*.patch; do
      git apply --check \"\$patch\"
      git apply \"\$patch\"
    done
    for patch in /work/inputs/luma-fingerprint/focaltech/*.patch; do
      git apply --check \"\$patch\"
      git apply \"\$patch\"
    done
    if [ '$tzmem_mode' = shmbridge ]; then
      for patch in /work/inputs/luma-fingerprint/shmbridge/*.patch; do
        git apply --check \"\$patch\"
        git apply \"\$patch\"
      done
    fi
    if [ '$stock_heaps' = true ]; then
      for patch in /work/inputs/luma-fingerprint/stock-heaps/*.patch; do
        git apply --check "\$patch"
        git apply "\$patch"
      done
    fi
    if [ '$dedicated_heaps' = true ]; then
      for patch in /work/inputs/luma-fingerprint/dedicated-heaps/*.patch; do
        git apply --check "\$patch"
        git apply "\$patch"
      done
    fi
    if [ '$qseelog' = true ]; then
      for patch in /work/inputs/luma-fingerprint/qseelog/*.patch; do
        git apply --check "\$patch"
        git apply "\$patch"
      done
    fi
  else
    test -f drivers/firmware/qcom/qcom_qseecom.c
    test -f drivers/tee/qseecom/core.c
    test -f drivers/input/finger/focal_finger/ff_core.c
    grep -Fq 'config QCOM_QSEECOM' drivers/firmware/qcom/Kconfig
    grep -Fq 'config TEE_QSEECOM' drivers/tee/qseecom/Kconfig
    grep -Fq 'config FINGER_FOCAL' drivers/input/finger/focal_finger/Kconfig
    # The preserved enumeration-v1 builder contains patches 1--41. Apply the
    # later bounded fixes independently when resuming an older exact tree.
    if ! grep -Fq 'QSEECOM_TZ_CMD_LISTENER_REGISTER_SMCINVOKE' \
        drivers/firmware/qcom/qcom_scm.c; then
      git apply --check \
        /work/inputs/luma-fingerprint/qsee/0042-firmware-qcom-scm-try-modern-listener-registration.patch
      git apply \
        /work/inputs/luma-fingerprint/qsee/0042-firmware-qcom-scm-try-modern-listener-registration.patch
    fi
    if ! grep -Fq 'mdt64_header_valid' drivers/soc/qcom/mdt_loader.c; then
      git apply --check \
        /work/inputs/luma-fingerprint/qsee/0043-soc-qcom-mdt-loader-support-ELF64-contiguous-images.patch
      git apply \
        /work/inputs/luma-fingerprint/qsee/0043-soc-qcom-mdt-loader-support-ELF64-contiguous-images.patch
    fi
    if [ '$tzmem_mode' = shmbridge ] && \
        ! grep -Fq 'qseecom: bounded app load' drivers/firmware/qcom/qcom_scm.c; then
      for patch in /work/inputs/luma-fingerprint/shmbridge/*.patch; do
        git apply --check \"\$patch\"
        git apply \"\$patch\"
      done
    fi
    if [ '$stock_heaps' = true ] && \
        ! grep -Fq 'SHM Bridge whole TA pool ready' \
          drivers/firmware/qcom/qcom_tzmem.c && \
        ! grep -Fq 'dedicated QSEECOM heaps active' \
          drivers/tee/qseecom/core.c; then
      for patch in /work/inputs/luma-fingerprint/stock-heaps/*.patch; do
        git apply --check "\$patch"
        git apply "\$patch"
      done
    fi
    if [ '$dedicated_heaps' = true ] && \
        ! grep -Fq 'dedicated QSEECOM heaps active' \
          drivers/tee/qseecom/core.c; then
      for patch in /work/inputs/luma-fingerprint/dedicated-heaps/*.patch; do
        git apply --check "\$patch"
        git apply "\$patch"
      done
    fi
    if [ '$qseelog' = true ] && \
        ! grep -Fq 'root-only QSEE diagnostic log registered' \
          drivers/firmware/qcom/qcom_scm.c; then
      for patch in /work/inputs/luma-fingerprint/qseelog/*.patch; do
        git apply --check "\$patch"
        git apply "\$patch"
      done
    fi
  fi
  scripts/config --enable TEE
  scripts/config --enable MODULE_ALLOW_BTF_MISMATCH
  scripts/config --enable QCOM_QSEECOM
  scripts/config --module TEE_QSEECOM
  scripts/config --enable QCOM_MDT_LOADER
  scripts/config --enable INPUT_FINGERPRINT
  scripts/config --module FINGER_FOCAL
  if [ '$tzmem_mode' = shmbridge ]; then
    scripts/config --disable QCOM_TZMEM_MODE_GENERIC
    scripts/config --enable QCOM_TZMEM_MODE_SHMBRIDGE
  else
    scripts/config --enable QCOM_TZMEM_MODE_GENERIC
    scripts/config --disable QCOM_TZMEM_MODE_SHMBRIDGE
  fi
  # Keep the installed module ABI exact. CONFIG_DMA_CMA conditionally inserts
  # cma_area into struct device, so enabling it in a kernel-only candidate
  # makes every existing non-CMA module layout-invalid. The required QSEE
  # staging memory is supplied by the separate DT-only SCM coherent pool.
  scripts/config --disable DMA_CMA
  make ARCH=arm64 LLVM=1 LOCALVERSION= olddefconfig
  grep -qx 'CONFIG_MODULE_ALLOW_BTF_MISMATCH=y' .config
  # The camera sensor modules are built from this exact source tree in their
  # own pinned module transaction; the accepted boot kernel deliberately
  # leaves both sensor drivers unset in its in-tree configuration.
  grep -qx '# CONFIG_VIDEO_IMX896 is not set' .config
  grep -qx '# CONFIG_VIDEO_S5KKD1SP is not set' .config
  grep -qx 'CONFIG_SND_SOC_WCD9378=m' .config
  grep -qx 'CONFIG_QCOM_QSEECOM=y' .config
  if [ '$tzmem_mode' = shmbridge ]; then
    grep -qx '# CONFIG_QCOM_TZMEM_MODE_GENERIC is not set' .config
    grep -qx 'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y' .config
    grep -Fq 'qseecom: bounded app load' drivers/firmware/qcom/qcom_scm.c
    grep -Fq 'SHM Bridge enabled' drivers/firmware/qcom/qcom_tzmem.c
  else
    grep -qx 'CONFIG_QCOM_TZMEM_MODE_GENERIC=y' .config
    grep -qx '# CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE is not set' .config
  fi
  if [ '$stock_heaps' = true ]; then
    if [ '$dedicated_heaps' = true ]; then
      grep -Fq 'dedicated QSEECOM heaps active' drivers/tee/qseecom/core.c
      grep -Fq 'qcom_tzmem_pool_new_on_device' drivers/firmware/qcom/qcom_tzmem.c
      ! grep -Fq 'qcom_tzmem_register_reserved_pool' drivers/firmware/qcom/qcom_scm.c
    else
      grep -Fq 'SHM Bridge whole TA pool ready' drivers/firmware/qcom/qcom_tzmem.c
      grep -Fq 'SHM Bridge apps pool ready' drivers/firmware/qcom/qcom_scm.c
    fi
  fi
  if [ '$qseelog' = true ]; then
    grep -Fq 'root-only QSEE diagnostic log registered' \
      drivers/firmware/qcom/qcom_scm.c
  fi
  grep -qx 'CONFIG_TEE_QSEECOM=m' .config
  grep -qx 'CONFIG_FINGER_FOCAL=m' .config
  grep -qx '# CONFIG_DMA_CMA is not set' .config
  test \"\$(make ARCH=arm64 LLVM=1 LOCALVERSION= -s kernelrelease)\" = 7.1.2
  kernel_stamp=arch/arm64/boot/.luma-fingerprint-qsee-patchset
  kernel_stamp_value='$FP6_FINGERPRINT_QSEECOM_PATCHSET_SHA256:$shmbridge_patchset_sha:$stock_heaps_patchset_sha:$dedicated_heaps_patchset_sha:$qseelog_patchset_sha:'\$(sha256sum .config | cut -d' ' -f1)
  reuse_kernel=false
  if [ '$resume' = true ] && test -s arch/arm64/boot/vmlinuz && \
      test -s vmlinux.symvers && \
      test -f \"\$kernel_stamp\" && \
      test \"\$(cat \"\$kernel_stamp\")\" = \"\$kernel_stamp_value\" && \
      grep -Fq 'qcom_scm_qseecom_app_send' vmlinux.symvers && \
      test \"\$(sha256sum arch/arm64/boot/vmlinuz | cut -d' ' -f1)\" \
        != '$FP6_FINGERPRINT_ENUMERATION_KERNEL_SHA256'; then
    reuse_kernel=true
  fi
  if [ \"\$reuse_kernel\" = false ]; then
    make -j'$jobs' ARCH=arm64 LLVM=1 KBUILD_BUILD_USER=pmos \\
      KBUILD_BUILD_HOST=build \\
      KBUILD_BUILD_TIMESTAMP='$build_timestamp' \\
      KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos vmlinuz.efi
    printf '%s\n' \"\$kernel_stamp_value\" >\"\$kernel_stamp\"
  fi
  make ARCH=arm64 LLVM=1 modules_prepare
  # A vmlinuz-only build writes the current built-in export table to
  # vmlinux.symvers, while an isolated M= module build consults the root
  # Module.symvers by default. The latter may still describe the input build.
  # Replace it with the table emitted by this exact kernel before checking the
  # two modules, without building every unrelated module in the tree.
  test -s vmlinux.symvers
  install -m 0644 vmlinux.symvers Module.symvers
  # Build explicit .ko targets. This kernel's single-module path generates the
  # .mod.o/final-link graph correctly for a narrow M= build, whereas its broad
  # modules target assumes the full module tree has already been traversed.
  make -j'$jobs' ARCH=arm64 LLVM=1 M=drivers/tee/qseecom qseecomtee.ko
  make -j'$jobs' ARCH=arm64 LLVM=1 \
    M=drivers/input/finger/focal_finger focaltech_fp.ko
"

bundle=$output_dir/bundle
mkdir -p "$bundle"
install -m 0644 "$source_tree/arch/arm64/boot/vmlinuz" "$bundle/Image.gz"
install -m 0644 "$source_tree/.config" "$bundle/config"
install -m 0644 \
  "$source_tree/drivers/input/finger/focal_finger/focaltech_fp.ko" \
  "$bundle/focaltech_fp.ko"
install -m 0644 "$source_tree/drivers/tee/qseecom/qseecomtee.ko" \
  "$bundle/qseecomtee.ko"

gzip -t "$bundle/Image.gz"
for module in focaltech_fp.ko qseecomtee.ko; do
  vermagic=$(modinfo -F vermagic "$bundle/$module")
  [ "${vermagic%% *}" = 7.1.2 ] || die "$module vermagic differs: $vermagic"
done

{
  printf 'LUMA_FP6_FINGERPRINT_BUILD_VERSION=1\n'
  printf 'KERNEL_RELEASE=7.1.2\n'
  printf 'BASE_IMAGE_SHA256=%s\n' "$base_image_sha"
  printf 'BASE_IMAGE_SHA256_RECORDED=%s\n' "$base_image_sha_recorded"
  printf 'BASE_CONFIG_SHA256=%s\n' "$base_config_sha"
  printf 'BASE_IMAGE_BYTE_REPRODUCIBLE=false\n'
  printf 'QSEECOM_PATCH_COUNT=%s\n' "$qsee_count"
  printf 'QSEECOM_PATCHSET_SHA256=%s\n' "$FP6_FINGERPRINT_QSEECOM_PATCHSET_SHA256"
  printf 'FOCALTECH_PATCH_COUNT=%s\n' "$driver_count"
  printf 'FOCALTECH_PATCHSET_SHA256=%s\n' "$FP6_FINGERPRINT_DRIVER_PATCHSET_SHA256"
  printf 'TZMEM_MODE=%s\n' "$tzmem_mode"
  printf 'SHMBRIDGE_DIAGNOSTIC_PATCH_COUNT=%s\n' "$shmbridge_count"
  printf 'SHMBRIDGE_DIAGNOSTIC_PATCHSET_SHA256=%s\n' "$shmbridge_patchset_sha"
  printf 'STOCK_HEAPS_ENABLED=%s\n' "$stock_heaps"
  printf 'STOCK_HEAPS_PATCH_COUNT=%s\n' "$stock_heaps_count"
  printf 'STOCK_HEAPS_PATCHSET_SHA256=%s\n' "$stock_heaps_patchset_sha"
  printf 'DEDICATED_HEAPS_ENABLED=%s\n' "$dedicated_heaps"
  printf 'DEDICATED_HEAPS_PATCH_COUNT=%s\n' "$dedicated_heaps_count"
  printf 'DEDICATED_HEAPS_PATCHSET_SHA256=%s\n' "$dedicated_heaps_patchset_sha"
  printf 'QSEELOG_ENABLED=%s\n' "$qseelog"
  printf 'QSEELOG_PATCH_COUNT=%s\n' "$qseelog_count"
  printf 'QSEELOG_PATCHSET_SHA256=%s\n' "$qseelog_patchset_sha"
  printf 'IMAGE_SHA256=%s\n' "$(hash "$bundle/Image.gz")"
  printf 'CONFIG_SHA256=%s\n' "$(hash "$bundle/config")"
  printf 'FOCALTECH_FP_KO_SHA256=%s\n' "$(hash "$bundle/focaltech_fp.ko")"
  printf 'QSEECOMTEE_KO_SHA256=%s\n' "$(hash "$bundle/qseecomtee.ko")"
  printf 'MODULE_BTF_MISMATCH_FALLBACK=true\n'
  printf 'PROPRIETARY_PAYLOADS_INCLUDED=false\n'
  printf 'SENSOR_NODE_ENABLED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$bundle/manifest.env"
chmod 0644 "$bundle/manifest.env"

printf 'FP6 fingerprint kernel bundle: %s\n' "$bundle"
