#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

# Build the FP6 WCD9378 microphone kernel, codec modules, and composite DTB in
# the same pinned Alpine x86_64/Clang 22 environment used for the proven Milos
# control kernel. The orchestrator must run off-device on Linux/aarch64 with
# Lima Rosetta. It creates artifacts only and never contacts a phone or writes
# a partition.

set -eu
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
kernel_archive=${1:?usage: build-fp6-microphone-candidate.sh KERNEL_ARCHIVE CAMERA_PATCH_ARCHIVE KERNEL_CONFIG MODULE_SYMVERS ALPINE_MINIROOTFS BTF_PATCH OUTPUT_DIR}
camera_patch_archive=${2:?usage: build-fp6-microphone-candidate.sh KERNEL_ARCHIVE CAMERA_PATCH_ARCHIVE KERNEL_CONFIG MODULE_SYMVERS ALPINE_MINIROOTFS BTF_PATCH OUTPUT_DIR}
kernel_config=${3:?usage: build-fp6-microphone-candidate.sh KERNEL_ARCHIVE CAMERA_PATCH_ARCHIVE KERNEL_CONFIG MODULE_SYMVERS ALPINE_MINIROOTFS BTF_PATCH OUTPUT_DIR}
module_symvers=${4:?usage: build-fp6-microphone-candidate.sh KERNEL_ARCHIVE CAMERA_PATCH_ARCHIVE KERNEL_CONFIG MODULE_SYMVERS ALPINE_MINIROOTFS BTF_PATCH OUTPUT_DIR}
minirootfs=${5:?usage: build-fp6-microphone-candidate.sh KERNEL_ARCHIVE CAMERA_PATCH_ARCHIVE KERNEL_CONFIG MODULE_SYMVERS ALPINE_MINIROOTFS BTF_PATCH OUTPUT_DIR}
btf_patch=${6:?usage: build-fp6-microphone-candidate.sh KERNEL_ARCHIVE CAMERA_PATCH_ARCHIVE KERNEL_CONFIG MODULE_SYMVERS ALPINE_MINIROOTFS BTF_PATCH OUTPUT_DIR}
output_dir=${7:?usage: build-fp6-microphone-candidate.sh KERNEL_ARCHIVE CAMERA_PATCH_ARCHIVE KERNEL_CONFIG MODULE_SYMVERS ALPINE_MINIROOTFS BTF_PATCH OUTPUT_DIR}
jobs=${LUMA_KERNEL_BUILD_JOBS:-4}
resume=${LUMA_KERNEL_BUILD_RESUME:-false}
build_timestamp='2026-08-19 00:00:00 UTC'

kernel_release=7.1.2
kernel_archive_sha=6043a062d595e1b4913512c3414387d9a016c4690d8832720651df07276dc78b
camera_patch_archive_sha=bac37aa2e9d020a8af22bcbe42abcd965c41eebd17eabd57541a6e50b0f716da
kernel_config_sha=9bf4fd8e395147f5eb696d2887e7c40d5a5e124a4d27584babeec830ed5d3fb2
module_symvers_sha=0136080d85bcb215aad56324ed61259c1581400d0159489c664b75b476de04e0
minirootfs_sha=5acac12c425a0817c1b6a79bdd5df4c73756fc8dc268eab32f8ba830872b835d
btf_patch_sha=e396b277b238ecf8868218d71da7a17da784c671655501211af50a5e6d8e43e7

case "$jobs" in
  ''|*[!0-9]*)
    printf 'error: LUMA_KERNEL_BUILD_JOBS must be a positive integer\n' >&2
    exit 1
    ;;
esac
[ "$jobs" -ge 1 ] || {
  printf 'error: LUMA_KERNEL_BUILD_JOBS must be at least 1\n' >&2
  exit 1
}
[ "$(id -u)" -eq 0 ] || {
  printf 'error: run the isolated-chroot builder as root\n' >&2
  exit 1
}
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || {
  printf 'error: build on an off-device Linux/aarch64 host\n' >&2
  exit 1
}
[ -x /mnt/lima-rosetta/rosetta ] || {
  printf 'error: exact Clang 22 build requires Lima Rosetta\n' >&2
  exit 1
}
[ -r /proc/sys/fs/binfmt_misc/rosetta ] &&
  grep -Fq enabled /proc/sys/fs/binfmt_misc/rosetta || {
  printf 'error: Lima Rosetta binfmt handler is unavailable\n' >&2
  exit 1
}
if [ -r /sys/firmware/devicetree/base/model ]; then
  model=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$model" in
    *Fairphone*)
      printf 'error: refuse to compile on the FP6 test target\n' >&2
      exit 1
      ;;
  esac
fi
case "$resume" in
  true|false) ;;
  *)
    printf 'error: LUMA_KERNEL_BUILD_RESUME must be true or false\n' >&2
    exit 1
    ;;
esac
if [ "$resume" = false ]; then
  [ ! -e "$output_dir" ] || {
    printf 'error: output directory already exists: %s\n' "$output_dir" >&2
    exit 1
  }
else
  [ -d "$output_dir/lab/root/work/linux" ] || {
    printf 'error: resumable kernel tree is missing: %s\n' "$output_dir" >&2
    exit 1
  }
  [ ! -e "$output_dir/bundle" ] || {
    printf 'error: refuse to resume over an existing artifact bundle\n' >&2
    exit 1
  }
fi

for tool in awk chroot cmp git gzip install llvm-objcopy modinfo mount readelf \
  sha256sum tar truncate umount; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: missing host build tool: %s\n' "$tool" >&2
    exit 1
  }
done

check_file() {
  actual=$(sha256sum "$1" | awk '{print $1}')
  [ "$actual" = "$2" ] || {
    printf 'error: checksum differs for %s: %s\n' "$1" "$actual" >&2
    exit 1
  }
}

check_file "$kernel_archive" "$kernel_archive_sha"
check_file "$camera_patch_archive" "$camera_patch_archive_sha"
check_file "$kernel_config" "$kernel_config_sha"
check_file "$module_symvers" "$module_symvers_sha"
check_file "$minirootfs" "$minirootfs_sha"
check_file "$btf_patch" "$btf_patch_sha"

patch_names='0005-media-i2c-add-fp6-s5kkd1sp-front-camera.patch
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
0036-ASoC-codecs-wcd9378-sync-with-the-mainline-v1-submis.patch'

patch_hashes='c0f057726b5a59927314494c47675a023ce5c2cfaf820b22ee47c7e2f460c0d8
50dd0a42cb5cc60e457d6df7ea8399967d9640a36d9dc863e00877f44f4671b9
9596f0712333e66bc5672b2b1e320c28bd16df8d163d0c132388b7b4441779e3
5da333d2b9f87c3872052f73b0a26355b9ac5152659ac89e1d445e4224e62540
11e0750bf63096b4433b7ee4ee0f2c5140cd2863013e432c7a4a2d688ab94929
e7b757536d616cd06d70a57b8ce6e0da88614c7f288360d34f6f9f339d6a9fa3
3d63b98ec7c656f443d40429a500c30d82a828272efbc2b25e840a72abf71aab
2c6112284b3e70581be21e129fc53df64d470c5ad96094b544ff9de4b8fbc787
06171407d0b10fec4b6b6af8b0129a25b8eeb8c8d63bac697892f9c7b42f8cc2
e3e3e003fac9a8fba9a8a453f94b0cd5618817031b5331bf2530b39de20f0d84
df30aca6f1f80164b25aef00ee6b69e39d1976ed709a22212f3c1e8db2850388
908c66a0dbde332faac73c191637ae245d72ea8b588164485aec724dea107f2d
19455f4edfa14cf77fca0952ac62bdda2c6368378da493cd92389061a418884b
55cc05a8fe1d1cc979e5e25aba82820e7a2628c4141957fcc7284682822e27dd
1af6ae4db68b0226800feb8b30989c6c1ebe532a1dc57085af12f88f7f4fc4bb
6a28abddc3e34a05cb65de70f186facd84ab2c0823cbb7a208aa440e26180df8
d08280d0b5e090caee8dc73631be3527c39625298a96c5fd6958d2abe24ee01d
d90c97cf73261656bbd77c81cdf95d3b08edb09bbba5fe1e1d9dfa75bb1af5c0
9af84a04e48a5595a44bde84b71c21e410397381dc24c07563aa8b74ee2b62a5
8f16511593487aaf94e1e212e280a78528103c7e74d41d414d0909c6b066e982
d14462efc9afc03158b3f5e8cbeac13ccf9c83f5b196538b06052860af4a32f8
c7ef475aace517b2feadbb29e727d103fa4527c12dc4a8a5f6f1eaf907a90e95
4fd48fab3768e32b864f02f03e2518d6acac021513d6898882d0b17e991e5171
b25f96065e4e9b0eb7b32d3ac71b45df32a45bfcb142e0d685da95bb6f3e8d6d
585ccdf3ddbea08ffe9b7d860751961bbb9215dacb47cfa0f851a19b90a9251e
615c2ea8646c82acb6b4c6f635b92f52832145e23c0af1e264189115d6917625'

set -- $patch_hashes
for patch_name in $patch_names; do
  [ "$#" -gt 0 ] || {
    printf 'error: missing expected hash for %s\n' "$patch_name" >&2
    exit 1
  }
  check_file "$repo_root/patches/linux-milos/$patch_name" "$1"
  shift
done
[ "$#" -eq 0 ] || {
  printf 'error: extra microphone patch hashes remain\n' >&2
  exit 1
}
patch_names_flat=$(printf '%s\n' "$patch_names" | tr '\n' ' ')

if [ "$resume" = false ]; then
  mkdir -p "$output_dir"
fi
output_dir=$(CDPATH= cd -- "$output_dir" && pwd)
lab_dir=$output_dir/lab
rootfs=$lab_dir/root
inputs=$rootfs/work/inputs
source_tree=$rootfs/work/linux
mkdir -p "$inputs" "$source_tree" "$rootfs/dev" "$rootfs/proc" \
  "$rootfs/sys" "$rootfs/mnt/lima-rosetta"
if [ "$resume" = false ]; then
  tar -xzf "$minirootfs" -C "$rootfs"
  install -m 0644 /etc/resolv.conf "$rootfs/etc/resolv.conf"

  install -m 0644 "$kernel_archive" "$inputs/linux.tar.gz"
  install -m 0644 "$camera_patch_archive" "$inputs/camera-patches.tar.gz"
  install -m 0644 "$kernel_config" "$inputs/config"
  install -m 0644 "$module_symvers" "$inputs/Module.symvers"
  install -m 0644 "$btf_patch" "$inputs/btf.patch"
  for patch_name in $patch_names; do
    install -m 0644 "$repo_root/patches/linux-milos/$patch_name" \
      "$inputs/$patch_name"
  done

  printf '%s/edge/main\n%s/edge/community\n' \
    https://dl-cdn.alpinelinux.org/alpine \
    https://dl-cdn.alpinelinux.org/alpine >"$rootfs/etc/apk/repositories"
else
  check_file "$inputs/linux.tar.gz" "$kernel_archive_sha"
  check_file "$inputs/camera-patches.tar.gz" "$camera_patch_archive_sha"
  check_file "$inputs/config" "$kernel_config_sha"
  check_file "$inputs/Module.symvers" "$module_symvers_sha"
  check_file "$inputs/btf.patch" "$btf_patch_sha"
  for patch_name in $patch_names; do
    check_file "$inputs/$patch_name" \
      "$(sha256sum "$repo_root/patches/linux-milos/$patch_name" | awk '{print $1}')"
  done
fi

mounted_dev=false
mounted_proc=false
mounted_rosetta=false
mounted_sys=false
cleanup() {
  if [ "$mounted_rosetta" = true ]; then
    umount -l -R "$rootfs/mnt/lima-rosetta" 2>/dev/null || true
  fi
  if [ "$mounted_sys" = true ]; then umount -l -R "$rootfs/sys" 2>/dev/null || true; fi
  if [ "$mounted_proc" = true ]; then umount -l "$rootfs/proc" 2>/dev/null || true; fi
  if [ "$mounted_dev" = true ]; then umount -l -R "$rootfs/dev" 2>/dev/null || true; fi
}
trap cleanup EXIT HUP INT TERM

mount --rbind /dev "$rootfs/dev"
mounted_dev=true
mount -t proc proc "$rootfs/proc"
mounted_proc=true
mount --rbind /sys "$rootfs/sys"
mounted_sys=true
mount --rbind /mnt/lima-rosetta "$rootfs/mnt/lima-rosetta"
mounted_rosetta=true
[ "$(chroot "$rootfs" uname -m)" = x86_64 ] || {
  printf 'error: isolated builder is not executing as x86_64\n' >&2
  exit 1
}

if [ "$resume" = false ]; then
  chroot "$rootfs" /bin/sh -eu -c '
    apk update
    apk add bash bc bison build-base clang coreutils elfutils-dev findutils \
      flex git installkernel linux-headers lld llvm openssl openssl-dev pahole \
      patch perl python3 zstd
  '

  chroot "$rootfs" /bin/sh -eu -c "
    cd /work
    tar -xzf inputs/linux.tar.gz
    tar -xzf inputs/camera-patches.tar.gz
    cd linux
    patch -p1 <../inputs/btf.patch
    for p in ../patches/kernel/*.patch; do git apply \"\$p\"; done
    for patch_name in $patch_names_flat; do git apply ../inputs/\"\$patch_name\"; done
    cp ../inputs/config .config
    scripts/config --module SND_SOC_WCD9378_SDW
    # The Qualcomm SoundWire paging fix changes built-in vmlinux BTF while the
    # retained Fedora module set carries split BTF from the proven kernel.
    # Their binary kABI and symbol CRCs are unchanged; permit only the kernel's
    # explicit fallback that discards mismatched module debug metadata.
    scripts/config --enable MODULE_ALLOW_BTF_MISMATCH
    make ARCH=arm64 LLVM=1 LOCALVERSION= olddefconfig
  "
fi

chroot "$rootfs" /bin/sh -eu -c "
  clang --version | head -n 1 | grep -Fx 'Alpine clang version 22.1.8'
  ld.lld --version | head -n 1
  pahole --version | grep -Fx 'v1.31'
  cd /work/linux
  test \"\$(make ARCH=arm64 LLVM=1 LOCALVERSION= -s kernelrelease)\" = '$kernel_release'
  grep -qx 'CONFIG_SOUNDWIRE_QCOM=y' .config
  grep -qx 'CONFIG_REGMAP_SOUNDWIRE=m' .config
  grep -qx 'CONFIG_SND_SOC_WCD_COMMON=m' .config
  grep -qx 'CONFIG_SND_SOC_WCD9378=m' .config
  grep -qx 'CONFIG_SND_SOC_WCD9378_SDW=m' .config
  grep -qx 'CONFIG_MODULE_ALLOW_BTF_MISMATCH=y' .config
  unset LDFLAGS
  if [ '$resume' = false ] || [ ! -s arch/arm64/boot/vmlinuz ] || \
    [ ! -s arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dtb ]; then
    make -j'$jobs' ARCH=arm64 LLVM=1 KBUILD_BUILD_USER=pmos \
      KBUILD_BUILD_HOST=build \
      KBUILD_BUILD_TIMESTAMP='$build_timestamp' \
      KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos vmlinuz.efi \
      qcom/milos-fairphone-fp6.dtb
  fi
  cp ../inputs/Module.symvers Module.symvers
  make -j'$jobs' ARCH=arm64 LLVM=1 M=drivers/base/regmap modules
  make -j'$jobs' ARCH=arm64 LLVM=1 M=sound/soc/codecs \
    KBUILD_EXTRA_SYMBOLS=/work/linux/drivers/base/regmap/Module.symvers modules
"

bundle=$output_dir/bundle
mkdir -p "$bundle"
install -m 0644 "$source_tree/arch/arm64/boot/vmlinuz" "$bundle/Image.gz"
install -m 0644 \
  "$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dtb" \
  "$bundle/milos-fairphone-fp6.dtb"
install -m 0644 "$source_tree/drivers/base/regmap/regmap-sdw.ko" "$bundle/"
for module in snd-soc-wcd-common snd-soc-wcd9378 snd-soc-wcd9378-sdw; do
  install -m 0644 "$source_tree/sound/soc/codecs/$module.ko" "$bundle/"
done
install -m 0644 "$source_tree/.config" "$bundle/config"

for module in regmap-sdw snd-soc-wcd-common snd-soc-wcd9378 \
  snd-soc-wcd9378-sdw; do
  vermagic=$(modinfo -F vermagic "$bundle/$module.ko")
  [ "${vermagic%% *}" = "$kernel_release" ] || {
    printf 'error: %s vermagic differs: %s\n' "$module.ko" "$vermagic" >&2
    exit 1
  }
  if readelf -SW "$bundle/$module.ko" | grep -Eq '[.]BTF([.]base)?'; then
    llvm-objcopy --remove-section=.BTF --remove-section=.BTF.base \
      "$bundle/$module.ko"
  fi
  if readelf -SW "$bundle/$module.ko" | grep -Eq '[.]BTF([.]base)?'; then
    printf 'error: %s retains incompatible split BTF\n' "$module.ko" >&2
    exit 1
  fi
done
gzip -t "$bundle/Image.gz"

tool_versions=$(chroot "$rootfs" /bin/sh -c \
  "apk list --installed clang22 lld22 llvm pahole 2>/dev/null | sed 's/ .*//' | tr '\n' ',' | sed 's/,$//'")
[ -n "$tool_versions" ] || {
  printf 'error: failed to record isolated tool package versions\n' >&2
  exit 1
}
{
  printf 'LUMA_FP6_MICROPHONE_BUILD_VERSION=2\n'
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'BUILD_HOST_PROFILE=lima-rosetta-cross-x86_64\n'
  printf 'RESUMED_AFTER_BUILDER_RESOURCE_FAILURE=%s\n' "$resume"
  printf 'TOOL_PACKAGES=%s\n' "$tool_versions"
  printf 'BASE_CONFIG_SHA256=%s\n' "$kernel_config_sha"
  printf 'OUTPUT_CONFIG_SHA256=%s\n' \
    "$(sha256sum "$bundle/config" | awk '{print $1}')"
  printf 'IMAGE_SHA256=%s\n' \
    "$(sha256sum "$bundle/Image.gz" | awk '{print $1}')"
  printf 'DTB_SHA256=%s\n' \
    "$(sha256sum "$bundle/milos-fairphone-fp6.dtb" | awk '{print $1}')"
  for artifact in regmap-sdw.ko snd-soc-wcd-common.ko snd-soc-wcd9378.ko \
    snd-soc-wcd9378-sdw.ko; do
    key=$(printf '%s' "$artifact" | tr '[:lower:].-' '[:upper:]__')
    printf '%s_SHA256=%s\n' "$key" \
      "$(sha256sum "$bundle/$artifact" | awk '{print $1}')"
  done
  printf 'RAM_BOOT_ONLY=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'MODULES_INSTALLED=false\n'
  printf 'BOOT_IMAGE_REPACKED=false\n'
  printf 'MODULE_BTF_MISMATCH_FALLBACK=true\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$bundle/manifest.env"
chmod 0644 "$bundle/manifest.env"

printf 'FP6 WCD9378 microphone bundle: %s\n' "$bundle"
