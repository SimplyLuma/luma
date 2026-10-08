#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Fetch immutable Tokay bring-up inputs. This script never communicates with a
# phone. A complete kernel checkout requires substantial free storage.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

output_root=${LUMA_PIXEL9_SOURCE_DIR:-$repo_root/build/cache/upstream/pixel9}
minimum_kib=${LUMA_PIXEL9_SOURCE_MINIMUM_KIB:-20971520}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

for tool in git curl base64 mktemp; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool is missing: $tool"
done

verify_sha256() {
  expected=$1
  path=$2
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$path" | sha256sum -c -
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$path" | shasum -a 256 -c -
  else
    die "sha256sum or shasum is required"
  fi
}

decode_base64() {
  if printf '' | base64 --decode >/dev/null 2>&1; then
    base64 --decode
  else
    base64 -D
  fi
}

[ ! -e "$output_root" ] || die "refusing to replace existing source directory: $output_root"
mkdir -p "$(dirname -- "$output_root")"
available_kib=$(df -Pk "$(dirname -- "$output_root")" 2>/dev/null | awk 'NR == 2 {print $4}')
[ -n "$available_kib" ] || die "could not determine free storage"
[ "$available_kib" -ge "$minimum_kib" ] || \
  die "Pixel 9 source checkout requires at least $((minimum_kib / 1048576)) GiB free; found $((available_kib / 1048576)) GiB"

stage=$(mktemp -d "$(dirname -- "$output_root")/.pixel9-source.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT

fetch_exact() {
  name=$1
  url=$2
  commit=$3
  destination="$stage/$name"
  git init -q "$destination"
  git -C "$destination" remote add origin "$url"
  git -C "$destination" fetch --depth=1 origin "$commit"
  git -C "$destination" checkout -q --detach FETCH_HEAD
  [ "$(git -C "$destination" rev-parse HEAD)" = "$commit" ] || \
    die "$name resolved to an unexpected commit"
}

fetch_exact linux-zumapro "$PIXEL9_LINUX_URL" "$PIXEL9_LINUX_COMMIT"
verify_sha256 "$PIXEL9_LINUX_DEFCONFIG_SHA256" \
  "$stage/linux-zumapro/arch/arm64/configs/$PIXEL9_LINUX_DEFCONFIG"

fetch_exact u-boot-zumapro "$PIXEL9_UBOOT_URL" "$PIXEL9_UBOOT_COMMIT"
verify_sha256 "$PIXEL9_UBOOT_DEFCONFIG_SHA256" \
  "$stage/u-boot-zumapro/configs/$PIXEL9_UBOOT_DEFCONFIG"

# Retain only the relevant packaging evidence. It is deliberately not a build
# input for Luma's Fedora userspace or native Tokay kernel.
pmaports="$stage/zumapro-mainline-pmaports"
git init -q "$pmaports"
git -C "$pmaports" remote add origin "$PIXEL9_PMAPORTS_URL"
git -C "$pmaports" fetch --depth=1 --filter=blob:none origin "$PIXEL9_PMAPORTS_COMMIT"
[ "$(git -C "$pmaports" rev-parse FETCH_HEAD)" = "$PIXEL9_PMAPORTS_COMMIT" ] || \
  die 'pmaports reference resolved to an unexpected commit'
git -C "$pmaports" update-ref HEAD "$PIXEL9_PMAPORTS_COMMIT"
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

manifest="$stage/android-gs-caimito-6.1-android16.xml"
curl -L --fail --silent --show-error \
  "$PIXEL9_ANDROID_KERNEL_MANIFEST_URL/+/$PIXEL9_ANDROID_KERNEL_MANIFEST_COMMIT/default.xml?format=TEXT" | \
  decode_base64 >"$manifest"
verify_sha256 "$PIXEL9_ANDROID_KERNEL_MANIFEST_SHA256" "$manifest"

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
  printf 'LUMA_PIXEL9_SOURCE_MANIFEST_VERSION=1\n'
  printf 'LINUX_COMMIT=%s\n' "$PIXEL9_LINUX_COMMIT"
  printf 'UBOOT_COMMIT=%s\n' "$PIXEL9_UBOOT_COMMIT"
  printf 'PMAPORTS_COMMIT=%s\n' "$PIXEL9_PMAPORTS_COMMIT"
  printf 'PMAPORTS_TOKAY_DEVICE_PACKAGE_PRESENT=false\n'
  printf 'ANDROID_KERNEL_MANIFEST_COMMIT=%s\n' "$PIXEL9_ANDROID_KERNEL_MANIFEST_COMMIT"
  printf 'MKBOOTIMG_COMMIT=%s\n' "$PIXEL9_MKBOOTIMG_COMMIT"
  printf 'AUDIT_ONLY=false\n'
  printf 'BUILD_READY=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$stage/luma-source-manifest.env"

mv "$stage" "$output_root"
trap - EXIT
printf 'Pixel 9 source inputs fetched and verified: %s\n' "$output_root"
