#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

# Reproduce the postmarketOS Milos 7.1.2 kernel build in a pinned Alpine build
# root. The release-equivalent default is an x86_64 userspace cross-building
# ARM64; a native-AArch64 profile remains available as a diagnostic control.
# This script creates an artifact only. It never contacts a bootloader or
# writes a phone partition.

set -eu
umask 022

mode=${1:-baseline}
source_dir=${2:-/var/tmp/luma-kernel-ifpc-test1}
lab_dir=${3:-/var/tmp/luma-alpine-kernel-control-20260805}
build_host_profile=${LUMA_PMOS_BUILD_HOST_PROFILE:-cross-x86_64}
kernel_config=${LUMA_PMOS_KERNEL_CONFIG:-/var/tmp/config-postmarketos-qcom-milos.aarch64}
btf_patch=${LUMA_PMOS_BTF_PATCH:-/var/tmp/linux7.0-resolve_btfids.patch}
ifpc_patch=${LUMA_IFPC_PATCH:-/var/tmp/0001-drm-msm-a810-disable-ifpc-diagnostic.patch}
gmu_diag_patch=${LUMA_GMU_DIAG_PATCH:-/var/tmp/0002-drm-msm-a810-gmu-timeout-diagnostics.patch}
gmu_fence_patch=${LUMA_GMU_FENCE_PATCH:-/var/tmp/0003-drm-msm-a810-use-gen8-3-ahb-fence-range.patch}
gmu_bootfreq_patch=${LUMA_GMU_BOOTFREQ_PATCH:-/var/tmp/0004-drm-msm-a810-use-gen8-3-gmu-boot-rate.patch}
mirror=${LUMA_ALPINE_MIRROR:-https://dl-cdn.alpinelinux.org/alpine}
mirror_host_ip=${LUMA_ALPINE_MIRROR_HOST_IP:-}
jobs=${LUMA_KERNEL_BUILD_JOBS:-4}
reuse_lab=${LUMA_REUSE_LAB:-false}

source_commit=dfe0125e73541d1984e4d2d37bd069a036bf0451
config_sha=9bf4fd8e395147f5eb696d2887e7c40d5a5e124a4d27584babeec830ed5d3fb2
btf_patch_sha=e396b277b238ecf8868218d71da7a17da784c671655501211af50a5e6d8e43e7
ifpc_patch_sha=44c332fd51c679d096db679f7f0705b4b0bc0219d4bb3e5d6eeffdfa03bf7e73
gmu_diag_patch_sha=d2fe1cb5402c45d2185d0fe36eae3e1ffe3ed8b44561f8d2f0575e23a24e4b15
gmu_fence_patch_sha=98c44781d92290a503111d2f5436a09eaff719f12d3a1e5c3234a9aec0afcbae
gmu_bootfreq_patch_sha=59afc667c728e0792c154724b225fb59fd74a732e3125708649a5bc5a7cecb88
pmaports_commit=e5422fadf556c3f632f7619a23b13d80f0214a2c

case "$build_host_profile" in
  cross-x86_64)
    minirootfs=${LUMA_ALPINE_MINIROOTFS:-/var/tmp/alpine-minirootfs-20260805-x86_64.tar.gz}
    minirootfs_sha=5acac12c425a0817c1b6a79bdd5df4c73756fc8dc268eab32f8ba830872b835d
    expected_chroot_arch=x86_64
    ;;
  native-aarch64)
    minirootfs=${LUMA_ALPINE_MINIROOTFS:-/var/tmp/alpine-minirootfs-20260805-aarch64.tar.gz}
    minirootfs_sha=78525fd3dc43e1bc7a1996cc036715316196ee435b6991c3a6959be3d3988e57
    expected_chroot_arch=aarch64
    ;;
  *)
    printf 'error: LUMA_PMOS_BUILD_HOST_PROFILE must be cross-x86_64 or native-aarch64\n' >&2
    exit 1
    ;;
esac

case "$mode" in
  baseline) ;;
  ifpc) ;;
  gmu-diag) ;;
  gmu-fence-diag) ;;
  gmu-bootfreq-diag) ;;
  *)
    printf 'error: mode must be baseline, ifpc, gmu-diag, gmu-fence-diag, or gmu-bootfreq-diag\n' >&2
    exit 1
    ;;
esac
case "$jobs" in
  ''|*[!0-9]*)
    printf 'error: LUMA_KERNEL_BUILD_JOBS must be a positive integer\n' >&2
    exit 1
    ;;
esac
case "$reuse_lab" in
  true|false) ;;
  *)
    printf 'error: LUMA_REUSE_LAB must be true or false\n' >&2
    exit 1
    ;;
esac
[ "$jobs" -ge 1 ] || {
  printf 'error: LUMA_KERNEL_BUILD_JOBS must be at least 1\n' >&2
  exit 1
}
[ "$(id -u)" -eq 0 ] || {
  printf 'error: run this isolated-chroot builder as root\n' >&2
  exit 1
}
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || {
  printf 'error: the FP6 control orchestrator must run on Linux/aarch64\n' >&2
  exit 1
}
if [ "$build_host_profile" = cross-x86_64 ]; then
  [ -x /mnt/lima-rosetta/rosetta ] || {
    printf 'error: cross-x86_64 requires Lima Rosetta at /mnt/lima-rosetta/rosetta\n' >&2
    exit 1
  }
  [ -r /proc/sys/fs/binfmt_misc/rosetta ] &&
    grep -Fq enabled /proc/sys/fs/binfmt_misc/rosetta || {
      printf 'error: cross-x86_64 requires the enabled Lima Rosetta binfmt handler\n' >&2
      exit 1
    }
fi
if [ -r /sys/firmware/devicetree/base/model ]; then
  device_model=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$device_model" in
    *Fairphone*)
      printf 'error: refuse to compile on the FP6 test target; use an off-device AArch64 builder\n' >&2
      exit 1
      ;;
  esac
fi

for tool in chroot cmp git gzip mount sha256sum tar umount; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: missing host tool: %s\n' "$tool" >&2
    exit 1
  }
done
for input in "$minirootfs" "$kernel_config" "$btf_patch"; do
  [ -f "$input" ] || {
    printf 'error: missing pinned build input: %s\n' "$input" >&2
    exit 1
  }
done
[ "$(sha256sum "$minirootfs" | awk '{print $1}')" = "$minirootfs_sha" ] || {
  printf 'error: Alpine minirootfs checksum differs\n' >&2
  exit 1
}
[ "$(sha256sum "$kernel_config" | awk '{print $1}')" = "$config_sha" ] || {
  printf 'error: postmarketOS kernel config checksum differs\n' >&2
  exit 1
}
[ "$(sha256sum "$btf_patch" | awk '{print $1}')" = "$btf_patch_sha" ] || {
  printf 'error: postmarketOS BTF patch checksum differs\n' >&2
  exit 1
}
[ -d "$source_dir/.git" ] || {
  printf 'error: missing Milos Git checkout: %s\n' "$source_dir" >&2
  exit 1
}
[ "$(git -C "$source_dir" rev-parse HEAD)" = "$source_commit" ] || {
  printf 'error: Milos source is not pinned to %s\n' "$source_commit" >&2
  exit 1
}
if [ "$mode" = ifpc ]; then
  [ -f "$ifpc_patch" ] || {
    printf 'error: missing IFPC diagnostic patch: %s\n' "$ifpc_patch" >&2
    exit 1
  }
  [ "$(sha256sum "$ifpc_patch" | awk '{print $1}')" = "$ifpc_patch_sha" ] || {
    printf 'error: IFPC diagnostic patch checksum differs\n' >&2
    exit 1
}
fi
if [ "$mode" = gmu-diag ] || [ "$mode" = gmu-fence-diag ] ||
   [ "$mode" = gmu-bootfreq-diag ]; then
  [ -f "$gmu_diag_patch" ] || {
    printf 'error: missing A810 GMU diagnostic patch: %s\n' "$gmu_diag_patch" >&2
    exit 1
  }
  [ "$(sha256sum "$gmu_diag_patch" | awk '{print $1}')" = "$gmu_diag_patch_sha" ] || {
    printf 'error: A810 GMU diagnostic patch checksum differs\n' >&2
    exit 1
  }
fi
if [ "$mode" = gmu-fence-diag ] || [ "$mode" = gmu-bootfreq-diag ]; then
  [ -f "$gmu_fence_patch" ] || {
    printf 'error: missing A810 GMU fence patch: %s\n' "$gmu_fence_patch" >&2
    exit 1
  }
  [ "$(sha256sum "$gmu_fence_patch" | awk '{print $1}')" = "$gmu_fence_patch_sha" ] || {
    printf 'error: A810 GMU fence patch checksum differs\n' >&2
    exit 1
  }
fi
if [ "$mode" = gmu-bootfreq-diag ]; then
  [ -f "$gmu_bootfreq_patch" ] || {
    printf 'error: missing A810 GMU boot-frequency patch: %s\n' "$gmu_bootfreq_patch" >&2
    exit 1
  }
  [ "$(sha256sum "$gmu_bootfreq_patch" | awk '{print $1}')" = "$gmu_bootfreq_patch_sha" ] || {
    printf 'error: A810 GMU boot-frequency patch checksum differs\n' >&2
    exit 1
  }
fi

rootfs=$lab_dir/root
work=$rootfs/work
output_dir=$lab_dir/output-$mode
mounted_dev=false
mounted_proc=false
mounted_rosetta=false
mounted_sys=false

cleanup() {
  if [ "$mounted_rosetta" = true ]; then
    umount -R "$rootfs/mnt/lima-rosetta" 2>/dev/null || true
  fi
  if [ "$mounted_sys" = true ]; then umount -R "$rootfs/sys" 2>/dev/null || true; fi
  if [ "$mounted_proc" = true ]; then umount "$rootfs/proc" 2>/dev/null || true; fi
  if [ "$mounted_dev" = true ]; then umount -R "$rootfs/dev" 2>/dev/null || true; fi
}
trap cleanup EXIT HUP INT TERM

if [ "$reuse_lab" = true ]; then
  [ -d "$work/linux" ] && [ -f "$work/linux/.config" ] || {
    printf 'error: reusable isolated build tree is incomplete: %s\n' "$lab_dir" >&2
    exit 1
  }
  mkdir -p "$output_dir" "$rootfs/proc" "$rootfs/sys" "$rootfs/dev" \
    "$rootfs/mnt/lima-rosetta"
  if [ "$mode" = ifpc ]; then
    install -m 0644 "$ifpc_patch" "$work/ifpc.patch"
  fi
  if [ "$mode" = gmu-diag ] || [ "$mode" = gmu-fence-diag ] ||
     [ "$mode" = gmu-bootfreq-diag ]; then
    install -m 0644 "$gmu_diag_patch" "$work/gmu-diag.patch"
  fi
  if [ "$mode" = gmu-fence-diag ]; then
    install -m 0644 "$gmu_fence_patch" "$work/gmu-fence.patch"
  fi
  if [ "$mode" = gmu-bootfreq-diag ]; then
    install -m 0644 "$gmu_bootfreq_patch" "$work/gmu-bootfreq.patch"
    install -m 0644 "$gmu_fence_patch" "$work/gmu-fence.patch"
  fi
else
  [ ! -e "$lab_dir" ] || {
    printf 'error: isolated lab directory already exists: %s\n' "$lab_dir" >&2
    exit 1
  }
  mkdir -p "$rootfs" "$output_dir"
  tar -xzf "$minirootfs" -C "$rootfs"
  mkdir -p "$work/linux" "$rootfs/proc" "$rootfs/sys" "$rootfs/dev" \
    "$rootfs/mnt/lima-rosetta"

  printf '%s/edge/main\n%s/edge/community\n' "$mirror" "$mirror" \
    >"$rootfs/etc/apk/repositories"
  if [ -n "$mirror_host_ip" ]; then
    printf '%s dl-cdn.alpinelinux.org\n' "$mirror_host_ip" \
      >>"$rootfs/etc/hosts"
  fi

  git -C "$source_dir" archive "$source_commit" | tar -x -C "$work/linux"
  install -m 0644 "$kernel_config" "$work/config-postmarketos-qcom-milos.aarch64"
  install -m 0644 "$btf_patch" "$work/linux7.0-resolve_btfids.patch"
  if [ "$mode" = ifpc ]; then
    install -m 0644 "$ifpc_patch" "$work/ifpc.patch"
  fi
  if [ "$mode" = gmu-diag ] || [ "$mode" = gmu-fence-diag ] ||
     [ "$mode" = gmu-bootfreq-diag ]; then
    install -m 0644 "$gmu_diag_patch" "$work/gmu-diag.patch"
  fi
  if [ "$mode" = gmu-fence-diag ]; then
    install -m 0644 "$gmu_fence_patch" "$work/gmu-fence.patch"
  fi
  if [ "$mode" = gmu-bootfreq-diag ]; then
    install -m 0644 "$gmu_bootfreq_patch" "$work/gmu-bootfreq.patch"
  fi
fi

mount --rbind /dev "$rootfs/dev"
mounted_dev=true
mount -t proc proc "$rootfs/proc"
mounted_proc=true
mount --rbind /sys "$rootfs/sys"
mounted_sys=true
if [ "$build_host_profile" = cross-x86_64 ]; then
  mount --rbind /mnt/lima-rosetta "$rootfs/mnt/lima-rosetta"
  mounted_rosetta=true
fi

[ "$(chroot "$rootfs" uname -m)" = "$expected_chroot_arch" ] || {
  printf 'error: build-root architecture does not match %s\n' "$build_host_profile" >&2
  exit 1
}

chroot "$rootfs" /bin/sh -eu -c '
  apk update
  apk add \
    bash bc bison build-base clang coreutils elfutils-dev findutils flex git \
    installkernel linux-headers lld llvm openssl openssl-dev pahole patch perl python3 \
    zstd
  clang --version | head -n 1
  ld.lld --version | head -n 1
  pahole --version
  clang --version | head -n 1 | grep -Fx "Alpine clang version 22.1.8"
  pahole --version | grep -Fx "v1.31"
'

if [ "$reuse_lab" = false ]; then
  chroot "$rootfs" /bin/sh -eu -c "
    cd /work/linux
    patch -p1 <../linux7.0-resolve_btfids.patch
    if [ '$mode' = ifpc ]; then patch -p1 <../ifpc.patch; fi
    if [ '$mode' = gmu-diag ]; then patch -p1 <../gmu-diag.patch; fi
    if [ '$mode' = gmu-fence-diag ]; then
      patch -p1 <../gmu-diag.patch
      patch -p1 <../gmu-fence.patch
    fi
    if [ '$mode' = gmu-bootfreq-diag ]; then
      patch -p1 <../gmu-diag.patch
      patch -p1 <../gmu-bootfreq.patch
    fi
    cp ../config-postmarketos-qcom-milos.aarch64 .config
  "
elif [ "$mode" = ifpc ]; then
  chroot "$rootfs" /bin/sh -eu -c '
    cd /work/linux
    if patch -p1 --dry-run <../ifpc.patch >/dev/null 2>&1; then
      patch -p1 <../ifpc.patch
    elif patch -R -p1 --dry-run <../ifpc.patch >/dev/null 2>&1; then
      printf "IFPC diagnostic patch is already applied exactly\n"
    else
      printf "error: reusable source is neither the baseline nor the exact IFPC state\n" >&2
      exit 1
    fi
    touch drivers/gpu/drm/msm/adreno/a6xx_catalog.c
  '
elif [ "$mode" = gmu-diag ]; then
  chroot "$rootfs" /bin/sh -eu -c '
    cd /work/linux
    if patch -p1 --dry-run <../gmu-diag.patch >/dev/null 2>&1; then
      patch -p1 <../gmu-diag.patch
    elif patch -R -p1 --dry-run <../gmu-diag.patch >/dev/null 2>&1; then
      printf "A810 GMU diagnostic patch is already applied exactly\n"
    else
      printf "error: reusable source is neither the baseline nor the exact A810 GMU diagnostic state\n" >&2
      exit 1
    fi
    touch drivers/gpu/drm/msm/adreno/a6xx_gmu.c
  '
elif [ "$mode" = gmu-fence-diag ]; then
  chroot "$rootfs" /bin/sh -eu -c '
    cd /work/linux
    if patch -p1 --dry-run <../gmu-diag.patch >/dev/null 2>&1; then
      patch -p1 <../gmu-diag.patch
    elif ! patch -R -p1 --dry-run <../gmu-diag.patch >/dev/null 2>&1; then
      printf "error: reusable source is neither the baseline nor the exact A810 GMU diagnostic state\n" >&2
      exit 1
    fi
    if patch -p1 --dry-run <../gmu-fence.patch >/dev/null 2>&1; then
      patch -p1 <../gmu-fence.patch
    elif patch -R -p1 --dry-run <../gmu-fence.patch >/dev/null 2>&1; then
      printf "A810 Gen8.3 fence patch is already applied exactly\n"
    else
      printf "error: reusable source is not the exact A810 GMU fence diagnostic state\n" >&2
      exit 1
    fi
    touch drivers/gpu/drm/msm/adreno/a6xx_gmu.c
  '
elif [ "$mode" = gmu-bootfreq-diag ]; then
  chroot "$rootfs" /bin/sh -eu -c '
    cd /work/linux
    if patch -p1 --dry-run <../gmu-diag.patch >/dev/null 2>&1; then
      patch -p1 <../gmu-diag.patch
    elif ! patch -R -p1 --dry-run <../gmu-diag.patch >/dev/null 2>&1; then
      printf "error: reusable source is neither the baseline nor the exact A810 GMU diagnostic state\n" >&2
      exit 1
    fi
    if patch -R -p1 --dry-run <../gmu-fence.patch >/dev/null 2>&1; then
      patch -R -p1 <../gmu-fence.patch
    elif ! patch -p1 --dry-run <../gmu-fence.patch >/dev/null 2>&1; then
      printf "error: reusable source has an unknown A810 fence state\n" >&2
      exit 1
    fi
    if patch -p1 --dry-run <../gmu-bootfreq.patch >/dev/null 2>&1; then
      patch -p1 <../gmu-bootfreq.patch
    elif patch -R -p1 --dry-run <../gmu-bootfreq.patch >/dev/null 2>&1; then
      printf "A810 Gen8.3 GMU boot-frequency patch is already applied exactly\n"
    else
      printf "error: reusable source is not the exact A810 GMU boot-frequency diagnostic state\n" >&2
      exit 1
    fi
    touch drivers/gpu/drm/msm/adreno/a6xx_gmu.c
  '
fi

chroot "$rootfs" /bin/sh -eu -c "
  cd /work/linux
  unset LDFLAGS
  make -j'$jobs' ARCH=arm64 LLVM=1 \\
    KBUILD_BUILD_USER=pmos \\
    KBUILD_BUILD_HOST=build \\
    KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos \\
    vmlinuz.efi
"

image=$work/linux/arch/arm64/boot/vmlinuz
[ -s "$image" ] || {
  printf 'error: postmarketOS vmlinuz target was not produced\n' >&2
  exit 1
}
gzip -t "$image"
gzip -dc "$image" >"$output_dir/Image"
install -m 0644 "$image" "$output_dir/Image.gz"
install -m 0644 "$work/linux/.config" "$output_dir/config"

image_sha=$(sha256sum "$output_dir/Image.gz" | awk '{print $1}')
config_output_sha=$(sha256sum "$output_dir/config" | awk '{print $1}')
[ "$build_host_profile" != cross-x86_64 ] || [ "$config_output_sha" = "$config_sha" ] || {
  printf 'error: cross-build Kconfig drifted from the exact postmarketOS release config\n' >&2
  exit 1
}
if [ "$mode" != baseline ] && [ -f "$lab_dir/output-baseline/Image.gz" ]; then
  ! cmp -s "$lab_dir/output-baseline/Image.gz" "$output_dir/Image.gz" || {
    printf 'error: diagnostic kernel is byte-identical to the baseline; reject stale output\n' >&2
    exit 1
  }
fi
decompressed_size=$(wc -c <"$output_dir/Image" | tr -d ' ')
tool_versions=$(chroot "$rootfs" /bin/sh -c \
  "apk info -v clang lld llvm pahole | tr '\n' ' '")
{
  printf 'LUMA_FP6_PMOS_KERNEL_CONTROL_VERSION=1\n'
  printf 'MODE=%s\n' "$mode"
  printf 'BUILD_HOST_PROFILE=%s\n' "$build_host_profile"
  printf 'REUSED_LAB=%s\n' "$reuse_lab"
  printf 'SOURCE_COMMIT=%s\n' "$source_commit"
  printf 'PMAPORTS_COMMIT=%s\n' "$pmaports_commit"
  printf 'ALPINE_MINIROOTFS_SHA256=%s\n' "$minirootfs_sha"
  printf 'PMOS_CONFIG_SHA256=%s\n' "$config_sha"
  printf 'PMOS_BTF_PATCH_SHA256=%s\n' "$btf_patch_sha"
  if [ "$mode" = ifpc ]; then
    printf 'IFPC_PATCH_SHA256=%s\n' "$ifpc_patch_sha"
  fi
  if [ "$mode" = gmu-diag ]; then
    printf 'GMU_DIAG_PATCH_SHA256=%s\n' "$gmu_diag_patch_sha"
  fi
  if [ "$mode" = gmu-fence-diag ]; then
    printf 'GMU_DIAG_PATCH_SHA256=%s\n' "$gmu_diag_patch_sha"
    printf 'GMU_FENCE_PATCH_SHA256=%s\n' "$gmu_fence_patch_sha"
  fi
  if [ "$mode" = gmu-bootfreq-diag ]; then
    printf 'GMU_DIAG_PATCH_SHA256=%s\n' "$gmu_diag_patch_sha"
    printf 'GMU_BOOTFREQ_PATCH_SHA256=%s\n' "$gmu_bootfreq_patch_sha"
    printf 'GMU_FENCE_PATCH_APPLIED=false\n'
  fi
  printf 'IMAGE_SHA256=%s\n' "$image_sha"
  printf 'OUTPUT_CONFIG_SHA256=%s\n' "$config_output_sha"
  printf 'DECOMPRESSED_SIZE=%s\n' "$decompressed_size"
  printf 'TOOL_PACKAGES=%s\n' "$tool_versions"
  printf 'PHONE_PARTITION_WRITTEN=false\n'
  printf 'BOOTLOADER_CONTACTED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'FP6 postmarketOS %s kernel: %s\n' "$mode" "$output_dir/Image.gz"
cat "$output_dir/manifest.env"
