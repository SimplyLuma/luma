#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Add a deterministic AVB hash footer and stock-size padding to one already
# verified direct-kernel header-v4 wrapper. This is an offline parser-shape
# experiment only. The well-known AOSP test key is not a production trust root.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

stock_audit=${1:?usage: build-pixel9-direct-kernel-avb-wrapper.sh STOCK_AUDIT DIRECT_WRAPPER AVB_INPUT OUTPUT_DIR}
direct_wrapper=${2:?usage: build-pixel9-direct-kernel-avb-wrapper.sh STOCK_AUDIT DIRECT_WRAPPER AVB_INPUT OUTPUT_DIR}
avb_input=${3:?usage: build-pixel9-direct-kernel-avb-wrapper.sh STOCK_AUDIT DIRECT_WRAPPER AVB_INPUT OUTPUT_DIR}
output_dir=${4:?usage: build-pixel9-direct-kernel-avb-wrapper.sh STOCK_AUDIT DIRECT_WRAPPER AVB_INPUT OUTPUT_DIR}
avbtool=$stock_audit/tools/avbtool.py
stock_avb=$stock_audit/metadata/boot.img.avb.txt
stock_summary=$stock_audit/metadata/boot.img.summary.env
inner=$direct_wrapper/pixel9-read-only-ramboot.img
inner_manifest=$direct_wrapper/manifest.env
key=$avb_input/testkey_rsa4096.pem
public_key=$avb_input/testkey_rsa4096.avbpubkey
input_manifest=$avb_input/manifest.env
candidate=$output_dir/boot.img
info=$output_dir/avb.info.txt

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] || die 'the AVB wrapper requires an off-device Linux builder'
if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to assemble an AVB wrapper on a Pixel target' ;;
  esac
fi
for tool in awk chmod cp grep head mkdir python3 sha1sum sha256sum stat tail tr wc; do
  command -v "$tool" >/dev/null 2>&1 || die "missing AVB-wrapper tool: $tool"
done
for required in "$avbtool" "$stock_avb" "$stock_summary" "$inner" \
  "$inner_manifest" "$key" "$public_key" "$input_manifest"; do
  [ -f "$required" ] || die "AVB-wrapper input is absent: $required"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

[ "$(sha256sum "$avbtool" | awk '{print $1}')" = "$PIXEL9_AVBTOOL_SHA256" ] ||
  die 'avbtool.py differs from the pin'
[ "$(wc -c <"$key" | tr -d '[:space:]')" = "$PIXEL9_AVB_TESTKEY_BYTES" ] ||
  die 'AOSP test-key byte size differs from the pin'
[ "$(sha256sum "$key" | awk '{print $1}')" = "$PIXEL9_AVB_TESTKEY_SHA256" ] ||
  die 'AOSP test-key digest differs from the pin'
[ "$(sha256sum "$public_key" | awk '{print $1}')" = "$PIXEL9_AVB_TEST_PUBLIC_KEY_SHA256" ] ||
  die 'AOSP test public-key digest differs from the pin'
[ "$(sha1sum "$public_key" | awk '{print $1}')" = "$PIXEL9_AVB_TEST_PUBLIC_KEY_SHA1" ] ||
  die 'AOSP test public-key SHA-1 differs from the pin'
for boundary in \
  'PUBLIC_TEST_KEY=true' \
  'PRODUCTION_TRUST_ROOT=false' \
  'CUSTOM_KEY_INSTALLED=false' \
  'PHONE_ACCESSED=false' \
  'FLASH_AUTHORIZED=false'; do
  grep -Fqx "$boundary" "$input_manifest" || die "AVB test input lacks: $boundary"
done

grep -Fqx "BYTES=$PIXEL9_STOCK_BOOT_IMAGE_BYTES" "$stock_summary" ||
  die 'stock boot partition size differs from the pin'
grep -Fqx "SHA256=$PIXEL9_STOCK_BOOT_IMAGE_SHA256" "$stock_summary" ||
  die 'stock boot image digest differs from the pin'
for stock_boundary in \
  "Image size:               $PIXEL9_STOCK_BOOT_IMAGE_BYTES bytes" \
  "Original image size:      $PIXEL9_STOCK_BOOT_ORIGINAL_IMAGE_BYTES bytes" \
  'Algorithm:                SHA256_RSA4096' \
  "Rollback Index:           $PIXEL9_STOCK_BOOT_ROLLBACK_INDEX" \
  '      Partition Name:        boot'; do
  grep -Fqx "$stock_boundary" "$stock_avb" || die "stock AVB boundary changed: $stock_boundary"
done

inner_bytes=$(stat -c %s "$inner")
inner_sha=$(sha256sum "$inner" | awk '{print $1}')
diagnostic_variant=$(sed -n 's/^DIAGNOSTIC_VARIANT=//p' "$inner_manifest")
report_transports=$(sed -n 's/^REPORT_TRANSPORTS=//p' "$inner_manifest")
linux_commit=$(sed -n 's/^LINUX_COMMIT=//p' "$inner_manifest")
direct_patch_sha=$(sed -n 's/^DIRECT_KERNEL_PATCH_SHA256=//p' "$inner_manifest")
persistent_dtb_patch_sha=$(sed -n 's/^PERSISTENT_DTB_PATCH_SHA256=//p' "$inner_manifest")
copy_dtb_patch_sha=$(sed -n 's/^COPY_DTB_PATCH_SHA256=//p' "$inner_manifest")
embedded_dtb_lifetime=$(sed -n 's/^EMBEDDED_DTB_LIFETIME=//p' "$inner_manifest")
tokay_dtb_sha=$(sed -n 's/^TOKAY_DTB_SHA256=//p' "$inner_manifest")
initramfs_sha=$(sed -n 's/^INITRAMFS_SHA256=//p' "$inner_manifest")
initramfs_kernel_compression=$(sed -n 's/^INITRAMFS_KERNEL_COMPRESSION=//p' "$inner_manifest")
embedded_initramfs_sha=$(sed -n 's/^EMBEDDED_INITRAMFS_SHA256=//p' "$inner_manifest")
kernel_bytes=$(sed -n 's/^KERNEL_BYTES=//p' "$inner_manifest")
kernel_sha=$(sed -n 's/^KERNEL_SHA256=//p' "$inner_manifest")
grep -Fqx 'LUMA_PIXEL9_DIRECT_KERNEL_WRAPPER_VERSION=1' "$inner_manifest" ||
  die 'direct-wrapper manifest version differs'
[ -n "$diagnostic_variant" ] || die 'direct-wrapper diagnostic variant is absent'
[ -n "$report_transports" ] || die 'direct-wrapper report transports are absent'
[ "$linux_commit" = "$PIXEL9_LINUX_COMMIT" ] || die 'direct-wrapper Linux revision differs from the pin'
[ "$direct_patch_sha" = "$PIXEL9_DIRECT_KERNEL_PATCH_SHA256" ] ||
  die 'direct-wrapper patch digest differs from the pin'
[ -n "$persistent_dtb_patch_sha" ] || persistent_dtb_patch_sha=none
[ -n "$copy_dtb_patch_sha" ] || copy_dtb_patch_sha=none
[ -n "$embedded_dtb_lifetime" ] || embedded_dtb_lifetime=init-rodata
[ -n "$initramfs_kernel_compression" ] || initramfs_kernel_compression=none
[ -n "$embedded_initramfs_sha" ] || embedded_initramfs_sha=$initramfs_sha
case "$diagnostic_variant:$persistent_dtb_patch_sha:$copy_dtb_patch_sha:$embedded_dtb_lifetime" in
  acm-v1:none:none:init-rodata|acm-ecm-v2:none:none:init-rodata) ;;
  acm-ecm-persistent-dtb-v3:$PIXEL9_DIRECT_KERNEL_PERSISTENT_DTB_PATCH_SHA256:none:persistent-rodata) ;;
  acm-ecm-copy-dtb-v4:none:$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256:copied-from-init-rodata) ;;
  ufs-delay-copy-dtb-v5:none:$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256:copied-from-init-rodata) ;;
  fedora-userspace-copy-dtb-v6:none:$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256:copied-from-init-rodata) ;;
  fedora-zstd-userspace-copy-dtb-v7:none:$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256:copied-from-init-rodata) ;;
  *) die 'direct-wrapper DTB lifetime provenance is inconsistent' ;;
esac
[ -n "$tokay_dtb_sha" ] || die 'direct-wrapper Tokay DTB digest is absent'
[ -n "$initramfs_sha" ] || die 'direct-wrapper initramfs digest is absent'
[ -n "$kernel_bytes" ] || die 'direct-wrapper kernel byte size is absent'
[ -n "$kernel_sha" ] || die 'direct-wrapper kernel digest is absent'
case "$diagnostic_variant:$initramfs_kernel_compression" in
  fedora-zstd-userspace-copy-dtb-v7:zstd) ;;
  fedora-zstd-userspace-copy-dtb-v7:*) die 'compressed-Fedora wrapper lacks Zstandard initramfs provenance' ;;
  *:none) ;;
  *) die 'unexpected initramfs compression provenance' ;;
esac
grep -Fqx "WRAPPER_BYTES=$inner_bytes" "$inner_manifest" ||
  die 'direct-wrapper byte size differs from its manifest'
grep -Fqx "WRAPPER_SHA256=$inner_sha" "$inner_manifest" ||
  die 'direct-wrapper digest differs from its manifest'
for boundary in \
  'HEADER_VERSION=4' \
  'ENTRY_CONTRACT_MATCHES_STOCK=true' \
  'TOKAY_DTB_LINKED_IN_KERNEL=true' \
  'INITRAMFS_LINKED_IN_KERNEL=true' \
  'LZ4_ROUNDTRIP_IDENTICAL=true' \
  'ROUNDTRIP_KERNEL_IDENTICAL=true' \
  'ROUNDTRIP_RAMDISK_IDENTICAL=true' \
  'PHONE_ACCESSED=false' \
  'AVB_SIGNED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  grep -Fqx "$boundary" "$inner_manifest" || die "direct wrapper lacks: $boundary"
done

mkdir -p "$output_dir"
cp "$inner" "$candidate"
chmod 0644 "$candidate"
python3 "$avbtool" add_hash_footer \
  --image "$candidate" \
  --partition_size "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" \
  --partition_name boot \
  --hash_algorithm sha256 \
  --salt "$inner_sha" \
  --algorithm SHA256_RSA4096 \
  --key "$key" \
  --rollback_index "$PIXEL9_STOCK_BOOT_ROLLBACK_INDEX" \
  --append_to_release_string ' Luma; AOSP test key; RAM only'
python3 "$avbtool" info_image --image "$candidate" >"$info"
(
  cd "$output_dir"
  python3 "$avbtool" verify_image --image boot.img --key "$key"
) >"$output_dir/avb.verify.txt"

candidate_bytes=$(stat -c %s "$candidate")
candidate_sha=$(sha256sum "$candidate" | awk '{print $1}')
[ "$candidate_bytes" = "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] ||
  die 'AVB candidate does not equal the stock boot partition size'
[ "$(tail -c 64 "$candidate" | head -c 4)" = AVBf ] ||
  die 'AVB footer magic is absent from the padded candidate tail'
for boundary in \
  'Footer version:           1.0' \
  "Image size:               $PIXEL9_STOCK_BOOT_IMAGE_BYTES bytes" \
  "Original image size:      $inner_bytes bytes" \
  'Algorithm:                SHA256_RSA4096' \
  "Rollback Index:           $PIXEL9_STOCK_BOOT_ROLLBACK_INDEX" \
  "Public key (sha1):        $PIXEL9_AVB_TEST_PUBLIC_KEY_SHA1" \
  "      Image Size:            $inner_bytes bytes" \
  '      Hash Algorithm:        sha256' \
  '      Partition Name:        boot' \
  "      Salt:                  $inner_sha"; do
  grep -Fqx "$boundary" "$info" || die "AVB candidate boundary differs: $boundary"
done

{
  printf 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_WRAPPER_VERSION=1\n'
  printf 'SCOPE=offline-stock-size-avb-parser-shape-experiment\n'
  printf 'DIAGNOSTIC_VARIANT=%s\n' "$diagnostic_variant"
  printf 'REPORT_TRANSPORTS=%s\n' "$report_transports"
  printf 'LINUX_COMMIT=%s\n' "$linux_commit"
  printf 'DIRECT_KERNEL_PATCH_SHA256=%s\n' "$direct_patch_sha"
  printf 'PERSISTENT_DTB_PATCH_SHA256=%s\n' "$persistent_dtb_patch_sha"
  printf 'COPY_DTB_PATCH_SHA256=%s\n' "$copy_dtb_patch_sha"
  printf 'EMBEDDED_DTB_LIFETIME=%s\n' "$embedded_dtb_lifetime"
  printf 'TOKAY_DTB_SHA256=%s\n' "$tokay_dtb_sha"
  printf 'INITRAMFS_SHA256=%s\n' "$initramfs_sha"
  printf 'INITRAMFS_KERNEL_COMPRESSION=%s\n' "$initramfs_kernel_compression"
  printf 'EMBEDDED_INITRAMFS_SHA256=%s\n' "$embedded_initramfs_sha"
  printf 'KERNEL_BYTES=%s\n' "$kernel_bytes"
  printf 'KERNEL_SHA256=%s\n' "$kernel_sha"
  printf 'INNER_WRAPPER_BYTES=%s\n' "$inner_bytes"
  printf 'INNER_WRAPPER_SHA256=%s\n' "$inner_sha"
  printf 'PARTITION_NAME=boot\n'
  printf 'PARTITION_BYTES=%s\n' "$candidate_bytes"
  printf 'PARTITION_SHA256=%s\n' "$candidate_sha"
  printf 'STOCK_PARTITION_BYTES=%s\n' "$PIXEL9_STOCK_BOOT_IMAGE_BYTES"
  printf 'STOCK_PARTITION_SIZE_MATCH=true\n'
  printf 'AVB_FOOTER_PRESENT=true\n'
  printf 'AVB_ALGORITHM=SHA256_RSA4096\n'
  printf 'AVB_HASH_ALGORITHM=sha256\n'
  printf 'AVB_SALT_SOURCE=inner-wrapper-sha256\n'
  printf 'AVB_ROLLBACK_INDEX=%s\n' "$PIXEL9_STOCK_BOOT_ROLLBACK_INDEX"
  printf 'AVB_TEST_KEY_SHA256=%s\n' "$PIXEL9_AVB_TESTKEY_SHA256"
  printf 'AVB_PUBLIC_KEY_SHA1=%s\n' "$PIXEL9_AVB_TEST_PUBLIC_KEY_SHA1"
  printf 'AVB_SIGNATURE_VERIFIED_OFFLINE=true\n'
  printf 'PUBLIC_TEST_KEY=true\n'
  printf 'PRODUCTION_TRUST_ROOT=false\n'
  printf 'GOOGLE_PRODUCTION_SIGNATURE_COPIED=false\n'
  printf 'ANDROID_IDENTITY_PROPERTIES_COPIED=false\n'
  printf 'CUSTOM_KEY_INSTALLED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env" "$info" "$output_dir/avb.verify.txt"

printf 'Pixel 9 offline direct-kernel AVB wrapper: %s\n' "$candidate"
printf 'Exact partition SHA-256: %s\n' "$candidate_sha"
