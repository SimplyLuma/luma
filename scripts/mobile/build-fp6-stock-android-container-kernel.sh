#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Reconstruct the exact documented FP6 camera/audio/QSEE/Focal source closure,
# then build the minimal binderfs and memfd/ashmem-compatibility delta needed
# by the stock-Android container.
# This runs only in the isolated Linux/aarch64 builder. It never contacts the
# phone, composes a boot image, or writes a partition.

set -euo pipefail
umask 022

repo_root=${1:?usage: build-fp6-stock-android-container-kernel.sh REPO_ROOT OUTPUT_DIR}
output_dir=${2:?missing output directory}
release_mode=${LUMA_FP6_CONTAINER_KERNEL_RELEASE_MODE:-isolated}
case $release_mode in
  isolated)
    kernel_release=7.1.2-luma-fp-ims-container1
    localversion=-luma-fp-ims-container1
    ;;
  ims1-compatible)
    # Keep the physically accepted release identity so the existing FP6 early
    # boot/module lookup contract remains unchanged. BinderFS is built in and
    # does not add or replace a loadable module.
    kernel_release=7.1.2-luma-fp-ims1
    localversion=-luma-fp-ims1
    ;;
  *) printf 'error: unsupported release mode: %s\n' "$release_mode" >&2; exit 1 ;;
esac
build_timestamp='2026-08-27 00:00:00 UTC'
jobs=${LUMA_KERNEL_BUILD_JOBS:-8}
work_root=${LUMA_KERNEL_WORK_ROOT:-/var/tmp/luma-fp6-stock-android-container-kernel}

source_archive=$repo_root/build/mobile/fp6-physical/fp6-camera-source-archives/linux-v7.1.2-milos.tar.gz
camera_archive=$repo_root/build/mobile/fp6-physical/fp6-camera-source-archives/fp6-camera-patches-52f06aa.tar.gz
base_config=$repo_root/build/mobile/fp6-physical/fp6-stock-container-kernel-input/config
btf_patch=$repo_root/build/mobile/fp6-physical/toolchains/linux7.0-resolve_btfids.patch
source_archive_sha=6043a062d595e1b4913512c3414387d9a016c4690d8832720651df07276dc78b
camera_archive_sha=bac37aa2e9d020a8af22bcbe42abcd965c41eebd17eabd57541a6e50b0f716da
base_config_sha=9f34ca9af43d09ea5165026f2596523d338b0d32222cc75070105d6b3313d130
btf_patch_sha=e396b277b238ecf8868218d71da7a17da784c671655501211af50a5e6d8e43e7
running_kernel_sha=62556c8a87a0b8c345fdfd11770bffad7b112526e17cdb353600bec8648583ef

camera_audio_patchset_sha=fbdca7020f9ce02c7654664ebb6792e3c6956e837bac43b78097b5eb4e3ed56f
qseecom_patchset_sha=1b65c7f49c53f74c11863d4c032b931d59fcc464e869a218fac7ed6535657bcc
focaltech_patchset_sha=9a18beecfd66e4b2479640a326900b2383d764a141d252dff16a5a939e0bacfe
shmbridge_patchset_sha=d1dcf0bd0158fc9faf43ce0c9af3bd705dd4836ea53aad6de506406c66f8ba3e
stock_heaps_patchset_sha=b6f449e4932e12cdb9e85241a85a6656b0911a656ee3f1b4fb500a089274f64d
dedicated_heaps_patchset_sha=523d8f9e79fd8f9a060a55df906b577d5676ad00f8464ad503cddc10ad64a4dc
qseelog_patchset_sha=f71ca241408b9a135e8448c49eef1a8f745e5e73432fa35d2e02aca68b4683ec
android_container_patchset_sha=04ed6190d53377da53d5c40f99ed013213820cfa6aa416c26786a0c43caa3974

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }
hash_patchset() {
  for patch in "$@"; do printf '%s  ./%s\n' "$(hash "$patch")" "$(basename "$patch")"; done |
    sha256sum | awk '{print $1}'
}

[[ $(uname -s) == Linux && $(uname -m) == aarch64 ]] || die 'requires Linux/aarch64'
[[ $(id -u) -eq 0 ]] || die 'run as root in the isolated builder'
[[ -d $repo_root ]] || die 'repository root absent'
[[ ! -e $output_dir ]] || die "refuse to overwrite output: $output_dir"
[[ ! -e $work_root ]] || die "refuse to overwrite work root: $work_root"
[[ $(hash "$source_archive") == "$source_archive_sha" ]] || die 'kernel source archive differs'
[[ $(hash "$camera_archive") == "$camera_archive_sha" ]] || die 'camera patch archive differs'
[[ $(hash "$base_config") == "$base_config_sha" ]] || die 'running base config differs'
[[ $(hash "$btf_patch") == "$btf_patch_sha" ]] || die 'BTF compatibility patch differs'

camera_audio_names=(
  0005-media-i2c-add-fp6-s5kkd1sp-front-camera.patch
  0022-media-i2c-s5kkd1sp-apply-exposure-and-gain-controls.patch
  0023-media-i2c-s5kkd1sp-correct-bayer-order-and-crop-default.patch
  0006-media-qcom-camss-fix-csid340-phy3-selection.patch
  0007-arm64-dts-qcom-milos-add-fp6-imx896-main-camera.patch
  0014-media-qcom-camss-csiphy-Introduce-PHY-configuration.patch
  0021-media-qcom-camss-Enable-C-PHY-where-available.patch
  0008-media-qcom-camss-add-fp6-imx896-cphy.patch
  0009-media-qcom-camss-fix-fp6-cphy-timer-rate.patch
  0010-media-qcom-camss-use-2p5gsps-fp6-cphy-tuning.patch
  0011-media-i2c-add-Sony-IMX896-LYT-700C-image-sensor-driv.patch
  0012-media-i2c-add-Dongwoon-DW9784-OIS-AF-controller-driv.patch
  0013-arm64-dts-qcom-milos-complete-fp6-imx896-camera.patch
  0024-arm64-dts-qcom-milos-correct-fp6-front-camera-rotation.patch
  0025-arm64-dts-qcom-milos-enable-fp6-speaker-dai.patch
  0026-ASoC-qcom-sc8280xp-support-Senary-MI2S.patch
  0027-ASoC-codecs-aw88261-backport-mainline-format-negotiation-power-up-fix.patch
  0028-ASoC-qcom-q6apm-lpass-dais-start-graph-at-prepare.patch
  0029-soundwire-qcom-add-SCP-address-paging-support.patch
  0030-ASoC-codecs-wcd9378-add-SoundWire-skeleton-driver-WI.patch
  0031-arm64-dts-qcom-milos-fairphone-fp6-enable-SoundWire-.patch
  0032-ASoC-codecs-wcd9378-grow-skeleton-into-TX-capture-co.patch
  0033-arm64-dts-qcom-milos-fairphone-fp6-add-WCD9378-codec.patch
  0034-ASoC-codecs-wcd9378-keep-the-TX-SoundWire-bus-out-of.patch
  0035-ASoC-codecs-wcd9378-correct-the-ADC-analog-gain-TLV-.patch
  0036-ASoC-codecs-wcd9378-sync-with-the-mainline-v1-submis.patch
)
camera_audio_patches=()
for name in "${camera_audio_names[@]}"; do
  camera_audio_patches+=("$repo_root/patches/linux-milos/$name")
done
mapfile -t qseecom_patches < <(find "$repo_root/patches/linux-qseecom" -maxdepth 1 -type f -name '*.patch' | sort)
mapfile -t focaltech_patches < <(find "$repo_root/patches/linux-milos-fingerprint" -maxdepth 1 -type f -name '000[1-5]-*.patch' | sort)
shmbridge_patches=("$repo_root/patches/linux-qseecom-shmbridge/0001-firmware-qcom-log-bounded-shmbridge-qsee-load-state.patch")
stock_heaps_patches=("$repo_root/patches/linux-qseecom-stock-heaps/0001-firmware-qcom-register-stock-qseecom-heaps.patch")
dedicated_heaps_patches=("$repo_root/patches/linux-qseecom-dedicated-heaps/0001-tee-qseecom-isolate-stock-heaps-from-global-SCM.patch")
qseelog_patches=("$repo_root/patches/linux-qseecom-qseelog/0001-firmware-qcom-scm-add-opt-in-qsee-log-buffer.patch")
mapfile -t android_container_patches < <(find "$repo_root/patches/linux-android-container" -maxdepth 1 -type f -name '*.patch' | sort)

[[ ${#camera_audio_patches[@]} -eq 26 ]] || die 'camera/audio patch count differs'
[[ ${#qseecom_patches[@]} -eq 43 ]] || die 'QSEECOM patch count differs'
[[ ${#focaltech_patches[@]} -eq 5 ]] || die 'FocalTech v5 patch count differs'
[[ ${#android_container_patches[@]} -eq 2 ]] || die 'Android container patch count differs'
[[ $(hash_patchset "${camera_audio_patches[@]}") == "$camera_audio_patchset_sha" ]] || die 'camera/audio patchset differs'
[[ $(hash_patchset "${qseecom_patches[@]}") == "$qseecom_patchset_sha" ]] || die 'QSEECOM patchset differs'
[[ $(hash_patchset "${focaltech_patches[@]}") == "$focaltech_patchset_sha" ]] || die 'FocalTech v5 patchset differs'
[[ $(hash_patchset "${shmbridge_patches[@]}") == "$shmbridge_patchset_sha" ]] || die 'SHM-Bridge patchset differs'
[[ $(hash_patchset "${stock_heaps_patches[@]}") == "$stock_heaps_patchset_sha" ]] || die 'stock-heaps patchset differs'
[[ $(hash_patchset "${dedicated_heaps_patches[@]}") == "$dedicated_heaps_patchset_sha" ]] || die 'dedicated-heaps patchset differs'
[[ $(hash_patchset "${qseelog_patches[@]}") == "$qseelog_patchset_sha" ]] || die 'QSEE-log patchset differs'
[[ $(hash_patchset "${android_container_patches[@]}") == "$android_container_patchset_sha" ]] || die 'Android container patchset differs'

source_tree=$work_root/linux
camera_tree=$work_root/patches
object_tree=$work_root/obj
module_tree=$work_root/modules
bundle=$output_dir/bundle
mkdir -p "$work_root" "$output_dir" "$object_tree" "$module_tree" "$bundle"
tar -xzf "$source_archive" -C "$work_root"
tar -xzf "$camera_archive" -C "$work_root"

(
  cd "$source_tree"
  patch -p1 <"$btf_patch"
  for candidate in "$camera_tree"/kernel/*.patch; do git apply "$candidate"; done
  for candidate in "${camera_audio_patches[@]}"; do git apply "$candidate"; done
  for candidate in "${qseecom_patches[@]}"; do git apply "$candidate"; done
  for candidate in "${focaltech_patches[@]}"; do git apply "$candidate"; done
  for candidate in "${shmbridge_patches[@]}"; do git apply "$candidate"; done
  for candidate in "${stock_heaps_patches[@]}"; do git apply "$candidate"; done
  for candidate in "${dedicated_heaps_patches[@]}"; do git apply "$candidate"; done
  for candidate in "${qseelog_patches[@]}"; do git apply "$candidate"; done
  for candidate in "${android_container_patches[@]}"; do git apply "$candidate"; done
)

cp "$base_config" "$object_tree/.config"
make -C "$source_tree" O="$object_tree" ARCH=arm64 LLVM=1 \
  LOCALVERSION="$localversion" olddefconfig
"$source_tree/scripts/config" --file "$object_tree/.config" --enable ANDROID_BINDERFS
"$source_tree/scripts/config" --file "$object_tree/.config" --enable MEMFD_ASHMEM_SHIM
make -C "$source_tree" O="$object_tree" ARCH=arm64 LLVM=1 \
  LOCALVERSION="$localversion" olddefconfig

expected_config=$work_root/expected.config
sed 's/^# CONFIG_ANDROID_BINDERFS is not set$/CONFIG_ANDROID_BINDERFS=y/' \
  "$base_config" |
  awk '{ print; if ($0 == "CONFIG_MEMFD_CREATE=y") print "CONFIG_MEMFD_ASHMEM_SHIM=y" }' \
  >"$expected_config"
cmp -s "$expected_config" "$object_tree/.config" || {
  diff -u "$expected_config" "$object_tree/.config" >&2 || true
  die 'configuration changed beyond the bounded Android container options'
}
grep -qx 'CONFIG_ANDROID_BINDER_IPC=y' "$object_tree/.config"
grep -qx 'CONFIG_ANDROID_BINDERFS=y' "$object_tree/.config"
grep -qx 'CONFIG_MEMFD_ASHMEM_SHIM=y' "$object_tree/.config"
grep -qx 'CONFIG_TEE_QSEECOM=m' "$object_tree/.config"
grep -qx 'CONFIG_FINGER_FOCAL=m' "$object_tree/.config"
grep -qx 'CONFIG_SND_SOC_WCD9378=m' "$object_tree/.config"
[[ $(make -s -C "$source_tree" O="$object_tree" ARCH=arm64 LLVM=1 \
  LOCALVERSION="$localversion" kernelrelease) == "$kernel_release" ]]

make -j"$jobs" -C "$source_tree" O="$object_tree" ARCH=arm64 LLVM=1 \
  LOCALVERSION="$localversion" KBUILD_BUILD_USER=pmos KBUILD_BUILD_HOST=build \
  KBUILD_BUILD_TIMESTAMP="$build_timestamp" \
  KBUILD_BUILD_VERSION=1-postmarketos vmlinuz.efi modules
make -C "$source_tree" O="$object_tree" ARCH=arm64 LLVM=1 \
  LOCALVERSION="$localversion" INSTALL_MOD_PATH="$module_tree" modules_install

module_root=$module_tree/lib/modules/$kernel_release
[[ -d $module_root ]] || die 'matching module tree absent'
rm -f "$module_root/build" "$module_root/source"
module_count=$(find "$module_root" -type f \( -name '*.ko' -o -name '*.ko.zst' \) |
  wc -l | tr -d ' ')
[[ $module_count -gt 0 ]] || die 'matching module tree empty'

install -m 0644 "$object_tree/arch/arm64/boot/vmlinuz" "$bundle/Image.gz"
install -m 0644 "$object_tree/arch/arm64/boot/vmlinuz.efi" "$bundle/linux.efi"
install -m 0644 "$object_tree/.config" "$bundle/config"
tar --sort=name --mtime="$build_timestamp" --owner=0 --group=0 --numeric-owner \
  -C "$module_tree" -cf - "lib/modules/$kernel_release" |
  zstd -q -19 -T0 -o "$bundle/modules-$kernel_release.tar.zst"

{
  printf 'LUMA_FP6_STOCK_ANDROID_CONTAINER_KERNEL_VERSION=2\n'
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'RELEASE_MODE=%s\n' "$release_mode"
  printf 'SOURCE_ARCHIVE_SHA256=%s\n' "$source_archive_sha"
  printf 'CAMERA_ARCHIVE_SHA256=%s\n' "$camera_archive_sha"
  printf 'CAMERA_AUDIO_PATCHSET_SHA256=%s\n' "$camera_audio_patchset_sha"
  printf 'QSEECOM_PATCHSET_SHA256=%s\n' "$qseecom_patchset_sha"
  printf 'FOCALTECH_PATCHSET_SHA256=%s\n' "$focaltech_patchset_sha"
  printf 'SHMBRIDGE_PATCHSET_SHA256=%s\n' "$shmbridge_patchset_sha"
  printf 'STOCK_HEAPS_PATCHSET_SHA256=%s\n' "$stock_heaps_patchset_sha"
  printf 'DEDICATED_HEAPS_PATCHSET_SHA256=%s\n' "$dedicated_heaps_patchset_sha"
  printf 'QSEELOG_PATCHSET_SHA256=%s\n' "$qseelog_patchset_sha"
  printf 'ANDROID_CONTAINER_PATCHSET_SHA256=%s\n' "$android_container_patchset_sha"
  printf 'BASE_CONFIG_SHA256=%s\n' "$base_config_sha"
  printf 'RUNNING_KERNEL_SHA256=%s\n' "$running_kernel_sha"
  printf 'IMAGE_SHA256=%s\n' "$(hash "$bundle/Image.gz")"
  printf 'EFI_IMAGE_SHA256=%s\n' "$(hash "$bundle/linux.efi")"
  printf 'CONFIG_SHA256=%s\n' "$(hash "$bundle/config")"
  printf 'MODULE_TREE_SHA256=%s\n' "$(hash "$bundle/modules-$kernel_release.tar.zst")"
  printf 'MODULE_COUNT=%s\n' "$module_count"
  printf 'ANDROID_BINDERFS_BUILTIN=true\n'
  printf 'MEMFD_ASHMEM_SHIM_BUILTIN=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOT_IMAGE_COMPOSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$bundle/manifest.env"

rm -rf "$work_root"
printf 'FP6 stock-Android container kernel bundle: %s\n' "$bundle"
