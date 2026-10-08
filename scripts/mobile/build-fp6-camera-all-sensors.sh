#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build a reproducible, RAM-loadable FP6 camera module bundle on the phone.
# This script only reads the running kernel configuration and writes below the
# requested work directory. It never installs modules, repacks boot images, or
# accesses a partition.

set -euo pipefail
umask 022

inputs=${1:?usage: build-fp6-camera-all-sensors.sh INPUT_DIR WORK_DIR}
work=${2:?usage: build-fp6-camera-all-sensors.sh INPUT_DIR WORK_DIR}
jobs=${LUMA_KERNEL_BUILD_JOBS:-2}

case "$jobs" in
  ''|*[!0-9]*) printf 'error: invalid job count: %s\n' "$jobs" >&2; exit 1 ;;
esac
[ "$jobs" -ge 1 ] || { printf 'error: job count must be positive\n' >&2; exit 1; }
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || {
  printf 'error: this build must run on Linux/aarch64\n' >&2
  exit 1
}
[ ! -e "$work" ] || {
  printf 'error: work directory already exists: %s\n' "$work" >&2
  exit 1
}

for tool in awk bc bison clang flex git gzip install ld.lld llvm-objcopy \
  make mktemp modinfo od pahole python3 readelf sha256sum stat tar tr truncate; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: missing build tool: %s\n' "$tool" >&2
    exit 1
  }
done

check_file() {
  local name=$1 expected=$2 actual
  [ -f "$inputs/$name" ] || { printf 'error: missing input: %s\n' "$name" >&2; exit 1; }
  actual=$(sha256sum "$inputs/$name" | awk '{print $1}')
  [ "$actual" = "$expected" ] || {
    printf 'error: checksum differs for %s: %s\n' "$name" "$actual" >&2
    exit 1
  }
}

check_file linux-v7.1.2-milos.tar.gz 6043a062d595e1b4913512c3414387d9a016c4690d8832720651df07276dc78b
check_file fp6-camera-patches-52f06aa.tar.gz bac37aa2e9d020a8af22bcbe42abcd965c41eebd17eabd57541a6e50b0f716da
check_file 0005-media-i2c-add-fp6-s5kkd1sp-front-camera.patch c0f057726b5a59927314494c47675a023ce5c2cfaf820b22ee47c7e2f460c0d8
check_file 0022-media-i2c-s5kkd1sp-apply-exposure-and-gain-controls.patch 50dd0a42cb5cc60e457d6df7ea8399967d9640a36d9dc863e00877f44f4671b9
check_file 0023-media-i2c-s5kkd1sp-correct-bayer-order-and-crop-default.patch 9596f0712333e66bc5672b2b1e320c28bd16df8d163d0c132388b7b4441779e3
check_file 0024-arm64-dts-qcom-milos-correct-fp6-front-camera-rotation.patch 55cc05a8fe1d1cc979e5e25aba82820e7a2628c4141957fcc7284682822e27dd
check_file 0025-arm64-dts-qcom-milos-enable-fp6-speaker-dai.patch 1af6ae4db68b0226800feb8b30989c6c1ebe532a1dc57085af12f88f7f4fc4bb
check_file 0006-media-qcom-camss-fix-csid340-phy3-selection.patch 5da333d2b9f87c3872052f73b0a26355b9ac5152659ac89e1d445e4224e62540
check_file 0007-arm64-dts-qcom-milos-add-fp6-imx896-main-camera.patch 11e0750bf63096b4433b7ee4ee0f2c5140cd2863013e432c7a4a2d688ab94929
check_file 0014-media-qcom-camss-csiphy-Introduce-PHY-configuration.patch e7b757536d616cd06d70a57b8ce6e0da88614c7f288360d34f6f9f339d6a9fa3
check_file 0021-media-qcom-camss-Enable-C-PHY-where-available.patch 3d63b98ec7c656f443d40429a500c30d82a828272efbc2b25e840a72abf71aab
check_file 0008-media-qcom-camss-add-fp6-imx896-cphy.patch 2c6112284b3e70581be21e129fc53df64d470c5ad96094b544ff9de4b8fbc787
check_file 0009-media-qcom-camss-fix-fp6-cphy-timer-rate.patch 06171407d0b10fec4b6b6af8b0129a25b8eeb8c8d63bac697892f9c7b42f8cc2
check_file 0010-media-qcom-camss-use-2p5gsps-fp6-cphy-tuning.patch e3e3e003fac9a8fba9a8a453f94b0cd5618817031b5331bf2530b39de20f0d84
check_file 0011-media-i2c-add-Sony-IMX896-LYT-700C-image-sensor-driv.patch df30aca6f1f80164b25aef00ee6b69e39d1976ed709a22212f3c1e8db2850388
check_file 0012-media-i2c-add-Dongwoon-DW9784-OIS-AF-controller-driv.patch 908c66a0dbde332faac73c191637ae245d72ea8b588164485aec724dea107f2d
check_file 0013-arm64-dts-qcom-milos-complete-fp6-imx896-camera.patch 19455f4edfa14cf77fca0952ac62bdda2c6368378da493cd92389061a418884b
check_file Module.symvers 0136080d85bcb215aad56324ed61259c1581400d0159489c664b75b476de04e0

mkdir -p "$work"
work=$(CDPATH= cd -- "$work" && pwd)
tar -xzf "$inputs/linux-v7.1.2-milos.tar.gz" -C "$work"
tar -xzf "$inputs/fp6-camera-patches-52f06aa.tar.gz" -C "$work"
source_tree=$work/linux
upstream_patches=$work/patches/kernel

for patch in \
  0001-media-qcom-camss-add-TFE665-VFE-support-for-milos.patch \
  0002-media-qcom-camss-add-CSIPHY-v2.2.1-milos-lane-config.patch \
  0003-media-qcom-camss-add-SM7635-resources-and-compatible.patch \
  0004-media-ov13b10-add-OF-match-selection-API-and-supplies.patch \
  0005-arm64-dts-qcom-milos-add-CAMSS-and-FP6-ultra-wide-ca.patch; do
  git -C "$source_tree" apply "$upstream_patches/$patch"
done
for patch in \
  0005-media-i2c-add-fp6-s5kkd1sp-front-camera.patch \
  0022-media-i2c-s5kkd1sp-apply-exposure-and-gain-controls.patch \
  0023-media-i2c-s5kkd1sp-correct-bayer-order-and-crop-default.patch \
  0006-media-qcom-camss-fix-csid340-phy3-selection.patch \
  0007-arm64-dts-qcom-milos-add-fp6-imx896-main-camera.patch \
  0014-media-qcom-camss-csiphy-Introduce-PHY-configuration.patch \
  0021-media-qcom-camss-Enable-C-PHY-where-available.patch \
  0008-media-qcom-camss-add-fp6-imx896-cphy.patch \
  0009-media-qcom-camss-fix-fp6-cphy-timer-rate.patch \
  0010-media-qcom-camss-use-2p5gsps-fp6-cphy-tuning.patch \
  0011-media-i2c-add-Sony-IMX896-LYT-700C-image-sensor-driv.patch \
  0012-media-i2c-add-Dongwoon-DW9784-OIS-AF-controller-driv.patch \
  0013-arm64-dts-qcom-milos-complete-fp6-imx896-camera.patch \
  0024-arm64-dts-qcom-milos-correct-fp6-front-camera-rotation.patch \
  0025-arm64-dts-qcom-milos-enable-fp6-speaker-dai.patch; do
  git -C "$source_tree" apply "$inputs/$patch"
done

(
  cd "$source_tree"
  gzip -dc /proc/config.gz >.config
  scripts/config --module VIDEO_QCOM_CAMSS
  scripts/config --module VIDEO_OV13B10
  scripts/config --module VIDEO_DW9714
  scripts/config --module VIDEO_DW9784
  scripts/config --module VIDEO_S5KKD1SP
  scripts/config --module VIDEO_IMX896
  make LLVM=1 LOCALVERSION= olddefconfig
  [ "$(make LLVM=1 LOCALVERSION= -s kernelrelease)" = 7.1.2 ]
  grep -qx 'CONFIG_VIDEO_QCOM_CAMSS=m' .config
  grep -qx 'CONFIG_VIDEO_OV13B10=m' .config
  grep -qx 'CONFIG_VIDEO_DW9714=m' .config
  grep -qx 'CONFIG_VIDEO_DW9784=m' .config
  grep -qx 'CONFIG_VIDEO_S5KKD1SP=m' .config
  grep -qx 'CONFIG_VIDEO_IMX896=m' .config
  # This Milos tree has no standalone vmlinux.symvers target. Reuse the
  # complete symbol-version table from Luma's exact-source 7.1.2 control
  # build, whose configuration is byte-identical to this build. The pinned
  # table includes modular videobuf2 and media exports, allowing camera-only
  # compilation without a resource-heavy full vmlinux/BTF relink.
  make -j"$jobs" LLVM=1 LOCALVERSION= modules_prepare
  install -m 0644 "$inputs/Module.symvers" Module.symvers
  make -j"$jobs" LLVM=1 LOCALVERSION= qcom/milos-fairphone-fp6.dtb
  make -j"$jobs" LLVM=1 LOCALVERSION= M=drivers/media/v4l2-core modules
  make -j"$jobs" LLVM=1 LOCALVERSION= M=drivers/media/platform/qcom/camss modules
  make -j"$jobs" LLVM=1 LOCALVERSION= M=drivers/media/i2c modules
)

bundle=$work/bundle
mkdir -p "$bundle"
install -m 0644 "$source_tree/drivers/media/v4l2-core/v4l2-cci.ko" "$bundle/v4l2-cci.ko"
install -m 0644 "$source_tree/drivers/media/platform/qcom/camss/qcom-camss.ko" "$bundle/qcom-camss.ko"
install -m 0644 "$source_tree/drivers/media/i2c/ov13b10.ko" "$bundle/ov13b10.ko"
install -m 0644 "$source_tree/drivers/media/i2c/dw9714.ko" "$bundle/dw9714.ko"
install -m 0644 "$source_tree/drivers/media/i2c/dw9784.ko" "$bundle/dw9784.ko"
install -m 0644 "$source_tree/drivers/media/i2c/s5kkd1sp.ko" "$bundle/s5kkd1sp.ko"
install -m 0644 "$source_tree/drivers/media/i2c/imx896.ko" "$bundle/imx896.ko"
install -m 0644 "$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dtb" "$bundle/milos-fairphone-fp6.dtb"
install -m 0644 "$source_tree/.config" "$bundle/config"

for module in v4l2-cci qcom-camss ov13b10 dw9714 dw9784 s5kkd1sp imx896; do
  [ "$(modinfo -F vermagic "$bundle/$module.ko" | awk '{print $1}')" = 7.1.2 ]
  if readelf -SW "$bundle/$module.ko" | grep -q '[.]BTF'; then
    llvm-objcopy --remove-section=.BTF "$bundle/$module.ko"
  fi
  ! readelf -SW "$bundle/$module.ko" | grep -q '[.]BTF'
done

{
  printf 'LUMA_FP6_CAMERA_ALL_SENSOR_BUILD_VERSION=6\n'
  printf 'KERNEL_RELEASE=7.1.2\n'
  printf 'RAM_LOAD_ONLY=true\n'
  printf 'PARTITION_WRITTEN=false\n'
  printf 'MODULES_INSTALLED=false\n'
  for artifact in v4l2-cci.ko qcom-camss.ko ov13b10.ko dw9714.ko \
    dw9784.ko s5kkd1sp.ko imx896.ko milos-fairphone-fp6.dtb config; do
    key=$(printf '%s' "$artifact" | tr '[:lower:].-' '[:upper:]__')
    printf '%s_SHA256=%s\n' "$key" "$(sha256sum "$bundle/$artifact" | awk '{print $1}')"
  done
} >"$bundle/manifest.env"
chmod 0644 "$bundle/manifest.env"

printf 'FP6 all-sensor camera bundle: %s\n' "$bundle"
