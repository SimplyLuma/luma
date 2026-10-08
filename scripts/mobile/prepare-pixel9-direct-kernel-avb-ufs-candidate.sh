#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Package two byte-identical delayed-UFS AVB proofs and bind them to the
# physically accepted copy-before-unflatten v4 diagnostic. Offline only; this
# script has no phone transport and cannot authorize a boot or flash.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

avb_a=${1:?usage: prepare-pixel9-direct-kernel-avb-ufs-candidate.sh AVB_A AVB_B ACCEPTED_COPY_DTB_CANDIDATE OUTPUT_DIR}
avb_b=${2:?usage: prepare-pixel9-direct-kernel-avb-ufs-candidate.sh AVB_A AVB_B ACCEPTED_COPY_DTB_CANDIDATE OUTPUT_DIR}
accepted_candidate=${3:?usage: prepare-pixel9-direct-kernel-avb-ufs-candidate.sh AVB_A AVB_B ACCEPTED_COPY_DTB_CANDIDATE OUTPUT_DIR}
output_dir=${4:?usage: prepare-pixel9-direct-kernel-avb-ufs-candidate.sh AVB_A AVB_B ACCEPTED_COPY_DTB_CANDIDATE OUTPUT_DIR}
accepted_acceptance=$accepted_candidate/acceptance.env
accepted_reproducibility=$accepted_candidate/reproducibility.env
accepted_manifest=$accepted_candidate/avb-proof/manifest.env
accepted_partition=$accepted_candidate/avb-proof/boot.img
ufs_init=$repo_root/config/mobile/pixel9-physical/$PIXEL9_DIAGNOSTIC_UFS_INIT

die() {
	printf 'error: %s\n' "$*" >&2
	exit 1
}

[ "$(uname -s)" = Linux ] || die 'the delayed-UFS candidate must be packaged off-device on Linux'
if [ -r /sys/firmware/devicetree/base/model ]; then
	build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
	case "$build_device" in
		*Pixel*|*tokay*|*Tokay*) die 'refusing to package a candidate on a Pixel target' ;;
	esac
fi
for tool in awk cmp grep install mkdir sed sha256sum stat tr; do
	command -v "$tool" >/dev/null 2>&1 || die "missing delayed-UFS candidate tool: $tool"
done
for required in "$accepted_acceptance" "$accepted_reproducibility" \
	"$accepted_manifest" "$accepted_partition" "$ufs_init"; do
	[ -f "$required" ] || die "candidate provenance input is absent: $required"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

[ "$(sha256sum "$ufs_init" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_UFS_INIT_SHA256" ] ||
	die 'delayed-UFS init source differs from its pin'
grep -Fqx 'snapshot_delay=15' "$ufs_init" || die 'delayed-UFS snapshot interval differs'
grep -Fq "echo 'LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=3'" "$ufs_init" ||
	die 'delayed-UFS report version differs'

for avb in "$avb_a" "$avb_b"; do
	for required in boot.img manifest.env avb.info.txt avb.verify.txt; do
		[ -f "$avb/$required" ] || die "delayed-UFS AVB proof is incomplete: $avb/$required"
	done
	for boundary in \
		'LUMA_PIXEL9_DIRECT_KERNEL_AVB_WRAPPER_VERSION=1' \
		'SCOPE=offline-stock-size-avb-parser-shape-experiment' \
		'DIAGNOSTIC_VARIANT=ufs-delay-copy-dtb-v5' \
		'REPORT_TRANSPORTS=usb-ecm-http' \
		"LINUX_COMMIT=$PIXEL9_LINUX_COMMIT" \
		"DIRECT_KERNEL_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_PATCH_SHA256" \
		'PERSISTENT_DTB_PATCH_SHA256=none' \
		"COPY_DTB_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256" \
		'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata' \
		"INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_UFS_INITRAMFS_SHA256" \
		'STOCK_PARTITION_SIZE_MATCH=true' \
		'AVB_FOOTER_PRESENT=true' \
		'AVB_SIGNATURE_VERIFIED_OFFLINE=true' \
		'PUBLIC_TEST_KEY=true' \
		'PRODUCTION_TRUST_ROOT=false' \
		'CUSTOM_KEY_INSTALLED=false' \
		'PHONE_ACCESSED=false' \
		'BOOT_AUTHORIZED=false' \
		'FLASH_AUTHORIZED=false'; do
		grep -Fqx "$boundary" "$avb/manifest.env" || die "delayed-UFS AVB proof lacks: $boundary"
	done
done

for relative in boot.img manifest.env avb.info.txt avb.verify.txt; do
	cmp "$avb_a/$relative" "$avb_b/$relative" ||
		die "delayed-UFS AVB reproducibility differs: $relative"
done

candidate_bytes=$(stat -c %s "$avb_a/boot.img")
candidate_sha=$(sha256sum "$avb_a/boot.img" | awk '{print $1}')
inner_sha=$(sed -n 's/^INNER_WRAPPER_SHA256=//p' "$avb_a/manifest.env")
tokay_dtb_sha=$(sed -n 's/^TOKAY_DTB_SHA256=//p' "$avb_a/manifest.env")
initramfs_sha=$(sed -n 's/^INITRAMFS_SHA256=//p' "$avb_a/manifest.env")
accepted_bytes=$(stat -c %s "$accepted_partition")
accepted_sha=$(sha256sum "$accepted_partition" | awk '{print $1}')
accepted_result_sha=$(sha256sum "$accepted_acceptance" | awk '{print $1}')
accepted_dtb_sha=$(sed -n 's/^TOKAY_DTB_SHA256=//p' "$accepted_manifest")
accepted_initramfs_sha=$(sed -n 's/^INITRAMFS_SHA256=//p' "$accepted_manifest")
for value in "$inner_sha" "$tokay_dtb_sha" "$initramfs_sha" "$accepted_result_sha" \
	"$accepted_dtb_sha" "$accepted_initramfs_sha"; do
	[ -n "$value" ] || die 'delayed-UFS provenance value is absent'
done
[ "$candidate_sha" != "$accepted_sha" ] || die 'delayed-UFS partition did not change from accepted v4'
[ "$tokay_dtb_sha" = "$accepted_dtb_sha" ] || die 'Tokay DTB bytes changed from accepted v4'
[ "$initramfs_sha" != "$accepted_initramfs_sha" ] || die 'delayed-UFS initramfs did not change from accepted v4'
[ "$initramfs_sha" = "$PIXEL9_DIAGNOSTIC_UFS_INITRAMFS_SHA256" ] || die 'delayed-UFS initramfs differs from pin'
[ "$candidate_bytes" = "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] || die 'candidate size differs from stock'
[ "$accepted_bytes" = "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] || die 'accepted predecessor size differs from stock'
grep -Fqx "PARTITION_SHA256=$candidate_sha" "$avb_a/manifest.env" || die 'candidate digest differs from manifest'

grep -Fqx 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_COPY_DTB_ACCEPTANCE_VERSION=2' "$accepted_acceptance" ||
	die 'accepted copy-DTB result version differs'
grep -Fqx "CANDIDATE_SHA256=$accepted_sha" "$accepted_acceptance" ||
	die 'accepted copy-DTB bytes differ from acceptance'
for boundary in \
	'PHYSICAL_DIAGNOSTIC_ACCEPTED=true' \
	'NATIVE_KERNEL_BOOT_CONFIRMED=true' \
	'USB_ECM_HTTP_REPORT_RECEIVED=true' \
	'DT_PROPERTY_INTEGRITY_CONFIRMED=true' \
	'UFS_INVENTORY_RECEIVED=true' \
	'REAL_UFS_BLOCK_DEVICE_OBSERVED=false' \
	'UFS_FUNCTIONAL_ACCEPTED=false' \
	'BOOT_AUTHORIZATION_CONSUMED=true' \
	'RETRY_AUTHORIZED=false' \
	'PARTITIONS_FLASHED=false' \
	'SLOTS_CHANGED=false' \
	'CUSTOM_KEY_INSTALLED=false' \
	'FLASH_AUTHORIZED=false'; do
	grep -Fqx "$boundary" "$accepted_acceptance" || die "accepted copy-DTB result lacks: $boundary"
done

mkdir -p "$output_dir/avb-proof"
for artifact in boot.img manifest.env avb.info.txt avb.verify.txt; do
	install -m 0644 "$avb_a/$artifact" "$output_dir/avb-proof/$artifact"
done
install -m 0644 "$accepted_acceptance" "$output_dir/accepted-v4-acceptance.env"
install -m 0644 "$accepted_reproducibility" "$output_dir/accepted-v4-reproducibility.env"

{
	printf 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_UFS_CANDIDATE_VERSION=1\n'
	printf 'CANDIDATE_KIND=direct-kernel-embedded-copied-dtb-delayed-ufs-initramfs-stock-size-avb-ecm-v5\n'
	printf 'DIAGNOSTIC_VARIANT=ufs-delay-copy-dtb-v5\n'
	printf 'REPORT_VERSION=3\n'
	printf 'SNAPSHOT_DELAY_SECONDS=15\n'
	printf 'REPORT_TRANSPORTS=usb-ecm-http\n'
	printf 'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata\n'
	printf 'COMPARISON=byte-for-byte\n'
	printf 'AVB_PARTITION_IDENTICAL=true\n'
	printf 'AVB_MANIFEST_IDENTICAL=true\n'
	printf 'AVB_INFO_IDENTICAL=true\n'
	printf 'AVB_VERIFY_REPORT_IDENTICAL=true\n'
	printf 'PRIOR_COPY_DTB_PHYSICAL_ACCEPTANCE_BOUND=true\n'
	printf 'PRIOR_CANDIDATE_SHA256=%s\n' "$accepted_sha"
	printf 'PRIOR_ACCEPTANCE_SHA256=%s\n' "$accepted_result_sha"
	printf 'PRIOR_REPORT_SHA256=%s\n' "$(sed -n 's/^REPORT_SHA256=//p' "$accepted_acceptance")"
	printf 'INNER_WRAPPER_SHA256=%s\n' "$inner_sha"
	printf 'TOKAY_DTB_SHA256=%s\n' "$tokay_dtb_sha"
	printf 'INITRAMFS_SHA256=%s\n' "$initramfs_sha"
	printf 'PERSISTENT_DTB_PATCH_SHA256=none\n'
	printf 'COPY_DTB_PATCH_SHA256=%s\n' "$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256"
	printf 'CANDIDATE_BYTES=%s\n' "$candidate_bytes"
	printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
	printf 'PHONE_ACCESSED=false\n'
	printf 'BOOT_AUTHORIZED=false\n'
	printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/reproducibility.env"
chmod 0644 "$output_dir/reproducibility.env"

printf 'Pixel 9 delayed-UFS AVB diagnostic candidate (unauthorized): %s\n' "$output_dir"
printf 'Exact candidate SHA-256: %s\n' "$candidate_sha"
