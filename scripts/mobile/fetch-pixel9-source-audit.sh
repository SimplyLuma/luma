#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Fetch a blob-filtered, sparse subset of the exact Pixel 9 source pins for
# offline architecture and safety review. This is not a build checkout and it
# never communicates with a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

output_root=${LUMA_PIXEL9_SOURCE_AUDIT_DIR:-$repo_root/build/cache/upstream/pixel9-audit}
minimum_kib=${LUMA_PIXEL9_SOURCE_AUDIT_MINIMUM_KIB:-2097152}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

verify_sha256() {
  expected=$1
  path=$2
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$path" | sha256sum -c -
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$path" | shasum -a 256 -c -
  else
    die 'sha256sum or shasum is required'
  fi
}

decode_base64() {
  if printf '' | base64 --decode >/dev/null 2>&1; then
    base64 --decode
  else
    base64 -D
  fi
}

for tool in git curl base64 mktemp; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool is missing: $tool"
done

[ ! -e "$output_root" ] || die "refusing to replace existing audit directory: $output_root"
mkdir -p "$(dirname -- "$output_root")"
available_kib=$(df -Pk "$(dirname -- "$output_root")" | awk 'NR == 2 {print $4}')
[ "$available_kib" -ge "$minimum_kib" ] || \
  die "Pixel 9 sparse source audit requires at least $((minimum_kib / 1048576)) GiB free; found $((available_kib / 1048576)) GiB"

stage=$(mktemp -d "$(dirname -- "$output_root")/.pixel9-audit.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT

fetch_filtered() {
  destination=$1
  url=$2
  commit=$3
  mkdir -p "$destination"
  git -C "$destination" init -q
  git -C "$destination" remote add origin "$url"
  git -C "$destination" fetch --depth=1 --filter=blob:none origin "$commit"
  [ "$(git -C "$destination" rev-parse FETCH_HEAD)" = "$commit" ] || \
    die 'filtered source fetch resolved to an unexpected commit'
  git -C "$destination" update-ref HEAD "$commit"
}

linux="$stage/linux-zumapro"
fetch_filtered "$linux" "$PIXEL9_LINUX_URL" "$PIXEL9_LINUX_COMMIT"
git -C "$linux" sparse-checkout init --cone
git -C "$linux" sparse-checkout set \
  arch/arm64/boot/dts/exynos/google \
  arch/arm64/configs \
  Documentation/devicetree/bindings/gnss \
  Documentation/devicetree/bindings/net/wwan \
  Documentation/devicetree/bindings/power/supply \
  drivers/gnss \
  drivers/gpu/drm/panel \
  drivers/input/touchscreen \
  drivers/mfd \
  drivers/net/wireless/broadcom \
  drivers/net/wwan \
  drivers/nfc \
  drivers/nvmem \
  drivers/pci/controller/dwc \
  drivers/power/supply \
  drivers/soc/google \
  drivers/thermal/samsung \
  sound/soc/google
git -C "$linux" checkout -q -f HEAD
verify_sha256 "$PIXEL9_LINUX_DEFCONFIG_SHA256" \
  "$linux/arch/arm64/configs/$PIXEL9_LINUX_DEFCONFIG"

uboot="$stage/u-boot-zumapro"
fetch_filtered "$uboot" "$PIXEL9_UBOOT_URL" "$PIXEL9_UBOOT_COMMIT"
git -C "$uboot" sparse-checkout init --cone
git -C "$uboot" sparse-checkout set \
  arch/arm/dts \
  arch/arm/mach-exynos \
  board/samsung/exynos-mobile \
  configs \
  doc/board/samsung \
  drivers/clk/exynos \
  drivers/pinctrl/exynos \
  drivers/ufs \
  drivers/usb/gadget \
  drivers/video
git -C "$uboot" checkout -q -f HEAD
verify_sha256 "$PIXEL9_UBOOT_DEFCONFIG_SHA256" \
  "$uboot/configs/$PIXEL9_UBOOT_DEFCONFIG"

pmaports="$stage/zumapro-mainline-pmaports"
fetch_filtered "$pmaports" "$PIXEL9_PMAPORTS_URL" "$PIXEL9_PMAPORTS_COMMIT"
git -C "$pmaports" sparse-checkout init --cone
git -C "$pmaports" sparse-checkout set \
  device/testing/device-google-komodo \
  device/testing/device-google-tegu \
  device/testing/linux-postmarketos-zumapro
git -C "$pmaports" checkout -q -f HEAD
verify_sha256 "$PIXEL9_PMAPORTS_KERNEL_APKBUILD_SHA256" \
  "$pmaports/device/testing/linux-postmarketos-zumapro/APKBUILD"
verify_sha256 "$PIXEL9_PMAPORTS_KOMODO_APKBUILD_SHA256" \
  "$pmaports/device/testing/device-google-komodo/APKBUILD"
verify_sha256 "$PIXEL9_PMAPORTS_KOMODO_DEVICEINFO_SHA256" \
  "$pmaports/device/testing/device-google-komodo/deviceinfo"
[ ! -e "$pmaports/device/testing/device-google-tokay" ] || \
  die 'pmaports reference unexpectedly contains a Tokay device package'

manifest_xml="$stage/android-gs-caimito-6.1-android16.xml"
curl -L --fail --silent --show-error \
  "$PIXEL9_ANDROID_KERNEL_MANIFEST_URL/+/$PIXEL9_ANDROID_KERNEL_MANIFEST_COMMIT/default.xml?format=TEXT" | \
  decode_base64 >"$manifest_xml"
verify_sha256 "$PIXEL9_ANDROID_KERNEL_MANIFEST_SHA256" "$manifest_xml"

mkbootimg_dir="$stage/aosp-mkbootimg"
mkdir -p "$mkbootimg_dir/gki"
curl -L --fail --silent --show-error \
  "$PIXEL9_MKBOOTIMG_URL/+/$PIXEL9_MKBOOTIMG_COMMIT/mkbootimg.py?format=TEXT" | \
  decode_base64 >"$mkbootimg_dir/mkbootimg.py"
curl -L --fail --silent --show-error \
  "$PIXEL9_MKBOOTIMG_URL/+/$PIXEL9_MKBOOTIMG_COMMIT/gki/generate_gki_certificate.py?format=TEXT" | \
  decode_base64 >"$mkbootimg_dir/gki/generate_gki_certificate.py"
touch "$mkbootimg_dir/gki/__init__.py"
verify_sha256 "$PIXEL9_MKBOOTIMG_SHA256" "$mkbootimg_dir/mkbootimg.py"
verify_sha256 "$PIXEL9_GKI_CERTIFICATE_TOOL_SHA256" \
  "$mkbootimg_dir/gki/generate_gki_certificate.py"

{
  printf 'LUMA_PIXEL9_SOURCE_AUDIT_VERSION=1\n'
  printf 'LINUX_COMMIT=%s\n' "$PIXEL9_LINUX_COMMIT"
  printf 'UBOOT_COMMIT=%s\n' "$PIXEL9_UBOOT_COMMIT"
  printf 'PMAPORTS_COMMIT=%s\n' "$PIXEL9_PMAPORTS_COMMIT"
  printf 'PMAPORTS_TOKAY_DEVICE_PACKAGE_PRESENT=false\n'
  printf 'ANDROID_KERNEL_MANIFEST_COMMIT=%s\n' "$PIXEL9_ANDROID_KERNEL_MANIFEST_COMMIT"
  printf 'MKBOOTIMG_COMMIT=%s\n' "$PIXEL9_MKBOOTIMG_COMMIT"
  printf 'AUDIT_ONLY=true\n'
  printf 'BUILD_READY=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$stage/luma-source-audit-manifest.env"

mv "$stage" "$output_root"
trap - EXIT
printf 'Pixel 9 sparse source audit fetched and verified: %s\n' "$output_root"
