#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Package two byte-identical Fedora/Zstandard kernel and AVB proofs. Bind the
# one-variable size experiment to the rejected uncompressed v6 result and the
# physically accepted v5 UFS result. Offline only: this script has no phone
# transport and cannot authorize a boot or flash.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

usage='prepare-pixel9-direct-kernel-avb-fedora-zstd-candidate.sh AVB_A AVB_B PROOF_A PROOF_B REJECTED_V6_CANDIDATE REJECTED_V6_RESULT REJECTED_V6_PROOF OUTPUT_DIR'
avb_a=${1:?usage: $usage}
avb_b=${2:?usage: $usage}
proof_a=${3:?usage: $usage}
proof_b=${4:?usage: $usage}
rejected_candidate=${5:?usage: $usage}
rejected_result=${6:?usage: $usage}
rejected_proof=${7:?usage: $usage}
output_dir=${8:?usage: $usage}
rejected_reproducibility=$rejected_candidate/reproducibility.env
rejected_revocation=$rejected_candidate/revocation.env
rejected_manifest=$rejected_candidate/avb-proof/manifest.env
rejected_partition=$rejected_candidate/avb-proof/boot.img
accepted_acceptance=$rejected_candidate/accepted-v5-acceptance.env
accepted_reproducibility=$rejected_candidate/accepted-v5-reproducibility.env
rejected_proof_manifest=$rejected_proof/manifest.env
rejected_proof_image=$rejected_proof/artifacts/Image
rejected_proof_initramfs=$rejected_proof/artifacts/diagnostic-initramfs.cpio
fedora_init=$repo_root/config/mobile/pixel9-physical/$PIXEL9_DIAGNOSTIC_FEDORA_INIT
fedora_lock=$repo_root/config/mobile/pixel9-physical/$PIXEL9_DIAGNOSTIC_FEDORA_RPM_LOCK

die() {
	printf 'error: %s\n' "$*" >&2
	exit 1
}

manifest_value() {
	local key=$1 file=$2 value
	value=$(sed -n "s/^${key}=//p" "$file")
	[ -n "$value" ] || die "manifest value is absent: $key in $file"
	printf '%s\n' "$value"
}

[ "$(uname -s)" = Linux ] || die 'the Fedora/Zstandard candidate must be packaged off-device on Linux'
if [ -r /sys/firmware/devicetree/base/model ]; then
	build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
	case "$build_device" in
		*Pixel*|*tokay*|*Tokay*) die 'refusing to package a candidate on a Pixel target' ;;
	esac
fi
for tool in awk cmp grep install mkdir sed sha256sum stat tr zstd; do
	command -v "$tool" >/dev/null 2>&1 || die "missing Fedora/Zstandard candidate tool: $tool"
done
for required in "$rejected_reproducibility" "$rejected_revocation" \
	"$rejected_manifest" "$rejected_partition" "$rejected_result" \
	"$accepted_acceptance" "$accepted_reproducibility" \
	"$rejected_proof_manifest" "$rejected_proof_image" \
	"$rejected_proof_initramfs" "$fedora_init" "$fedora_lock"; do
	[ -f "$required" ] || die "candidate provenance input is absent: $required"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

[ "$(sha256sum "$fedora_init" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_FEDORA_INIT_SHA256" ] ||
	die 'Fedora init source differs from its pin'
[ "$(sha256sum "$fedora_lock" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_FEDORA_RPM_LOCK_SHA256" ] ||
	die 'Fedora RPM lock differs from its pin'

proof_files='artifacts/Image artifacts/config artifacts/diagnostic-init artifacts/diagnostic-initramfs.cpio artifacts/embedded-initramfs.data artifacts/entry-contract.env artifacts/zumapro-tokay.dtb manifest.env'
for proof in "$proof_a" "$proof_b"; do
	for relative in $proof_files; do
		[ -f "$proof/$relative" ] || die "Fedora/Zstandard kernel proof is incomplete: $proof/$relative"
	done
	for boundary in \
		'LUMA_PIXEL9_DIRECT_KERNEL_PROOF_VERSION=1' \
		'DIAGNOSTIC_VARIANT=fedora-zstd-userspace-copy-dtb-v7' \
		'REPORT_TRANSPORTS=usb-ecm-http' \
		"LINUX_COMMIT=$PIXEL9_LINUX_COMMIT" \
		"INITRAMFS_BYTES=$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_BYTES" \
		"INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_SHA256" \
		'INITRAMFS_KERNEL_COMPRESSION=zstd' \
		'PERSISTENT_FILESYSTEM_MOUNTS=false' \
		'INTERACTIVE_SHELL=false' \
		'PHONE_ACCESSED=false' \
		'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
		'BOOT_AUTHORIZED=false' \
		'FLASH_AUTHORIZED=false'; do
		grep -Fqx "$boundary" "$proof/manifest.env" || die "Fedora/Zstandard kernel proof lacks: $boundary"
	done
	[ "$(stat -c %s "$proof/artifacts/diagnostic-initramfs.cpio")" = "$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_BYTES" ] ||
		die 'Fedora/Zstandard raw CPIO byte size differs from pin'
	[ "$(sha256sum "$proof/artifacts/diagnostic-initramfs.cpio" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_SHA256" ] ||
		die 'Fedora/Zstandard raw CPIO digest differs from pin'
	zstd -q -d -c "$proof/artifacts/embedded-initramfs.data" |
		cmp -s - "$proof/artifacts/diagnostic-initramfs.cpio" ||
		die 'embedded Zstandard initramfs does not decompress byte-exact to the audited CPIO'
done
for relative in $proof_files; do
	cmp -s "$proof_a/$relative" "$proof_b/$relative" || die "Fedora/Zstandard kernel reproducibility differs: $relative"
done

for avb in "$avb_a" "$avb_b"; do
	for required in boot.img manifest.env avb.info.txt avb.verify.txt; do
		[ -f "$avb/$required" ] || die "Fedora/Zstandard AVB proof is incomplete: $avb/$required"
	done
	for boundary in \
		'LUMA_PIXEL9_DIRECT_KERNEL_AVB_WRAPPER_VERSION=1' \
		'SCOPE=offline-stock-size-avb-parser-shape-experiment' \
		'DIAGNOSTIC_VARIANT=fedora-zstd-userspace-copy-dtb-v7' \
		'REPORT_TRANSPORTS=usb-ecm-http' \
		"LINUX_COMMIT=$PIXEL9_LINUX_COMMIT" \
		"DIRECT_KERNEL_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_PATCH_SHA256" \
		'PERSISTENT_DTB_PATCH_SHA256=none' \
		"COPY_DTB_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256" \
		'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata' \
		"INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_SHA256" \
		'INITRAMFS_KERNEL_COMPRESSION=zstd' \
		'STOCK_PARTITION_SIZE_MATCH=true' \
		'AVB_FOOTER_PRESENT=true' \
		'AVB_SIGNATURE_VERIFIED_OFFLINE=true' \
		'PUBLIC_TEST_KEY=true' \
		'PRODUCTION_TRUST_ROOT=false' \
		'CUSTOM_KEY_INSTALLED=false' \
		'PHONE_ACCESSED=false' \
		'BOOT_AUTHORIZED=false' \
		'FLASH_AUTHORIZED=false'; do
		grep -Fqx "$boundary" "$avb/manifest.env" || die "Fedora/Zstandard AVB proof lacks: $boundary"
	done
done
for relative in boot.img manifest.env avb.info.txt avb.verify.txt; do
	cmp -s "$avb_a/$relative" "$avb_b/$relative" || die "Fedora/Zstandard AVB reproducibility differs: $relative"
done

candidate_bytes=$(stat -c %s "$avb_a/boot.img")
candidate_sha=$(sha256sum "$avb_a/boot.img" | awk '{print $1}')
kernel_bytes=$(manifest_value KERNEL_BYTES "$avb_a/manifest.env")
kernel_sha=$(manifest_value KERNEL_SHA256 "$avb_a/manifest.env")
embedded_bytes=$(manifest_value EMBEDDED_INITRAMFS_BYTES "$proof_a/manifest.env")
embedded_sha=$(manifest_value EMBEDDED_INITRAMFS_SHA256 "$avb_a/manifest.env")
inner_sha=$(manifest_value INNER_WRAPPER_SHA256 "$avb_a/manifest.env")
tokay_dtb_sha=$(manifest_value TOKAY_DTB_SHA256 "$avb_a/manifest.env")
[ "$(stat -c %s "$proof_a/artifacts/Image")" = "$kernel_bytes" ] || die 'v7 kernel byte size differs from AVB manifest'
[ "$(sha256sum "$proof_a/artifacts/Image" | awk '{print $1}')" = "$kernel_sha" ] || die 'v7 kernel digest differs from AVB manifest'
[ "$(stat -c %s "$proof_a/artifacts/embedded-initramfs.data")" = "$embedded_bytes" ] || die 'v7 embedded initramfs byte size differs from proof manifest'
[ "$(sha256sum "$proof_a/artifacts/embedded-initramfs.data" | awk '{print $1}')" = "$embedded_sha" ] || die 'v7 embedded initramfs digest differs from AVB manifest'
[ "$embedded_bytes" -lt "$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_BYTES" ] || die 'Zstandard payload is not smaller than raw CPIO'
[ "$kernel_bytes" -lt "$PIXEL9_STOCK_KERNEL_IMAGE_BYTES" ] || die 'v7 raw kernel is not smaller than stock raw kernel'

rejected_sha=$(sha256sum "$rejected_partition" | awk '{print $1}')
rejected_result_sha=$(sha256sum "$rejected_result" | awk '{print $1}')
rejected_revocation_sha=$(sha256sum "$rejected_revocation" | awk '{print $1}')
rejected_proof_sha=$(sha256sum "$rejected_proof_manifest" | awk '{print $1}')
rejected_kernel_bytes=$(manifest_value IMAGE_BYTES "$rejected_proof_manifest")
rejected_kernel_sha=$(manifest_value IMAGE_SHA256 "$rejected_proof_manifest")
[ "$(stat -c %s "$rejected_proof_image")" = "$rejected_kernel_bytes" ] || die 'rejected v6 kernel byte size differs from proof'
[ "$(sha256sum "$rejected_proof_image" | awk '{print $1}')" = "$rejected_kernel_sha" ] || die 'rejected v6 kernel digest differs from proof'
[ "$kernel_bytes" -lt "$rejected_kernel_bytes" ] || die 'v7 raw kernel is not smaller than rejected v6'
cmp -s "$proof_a/artifacts/diagnostic-initramfs.cpio" "$rejected_proof_initramfs" ||
	die 'v7 raw Fedora CPIO differs from rejected v6'
grep -Fqx 'DIAGNOSTIC_VARIANT=fedora-userspace-copy-dtb-v6' "$rejected_proof_manifest" || die 'rejected proof is not Fedora v6'
grep -Fqx "CANDIDATE_SHA256=$rejected_sha" "$rejected_reproducibility" || die 'rejected v6 partition differs from reproducibility record'
grep -Fqx "PARTITION_SHA256=$rejected_sha" "$rejected_manifest" || die 'rejected v6 partition differs from AVB manifest'
grep -Fqx "CANDIDATE_SHA256=$rejected_sha" "$rejected_revocation" || die 'rejected v6 partition differs from revocation'
grep -Fqx "CANDIDATE_SHA256=$rejected_sha" "$rejected_result" || die 'rejected v6 partition differs from result'
grep -Fqx "RESULT_SHA256=$rejected_result_sha" "$rejected_revocation" || die 'rejected v6 result differs from revocation'
for boundary in \
	'PHYSICAL_DIAGNOSTIC_ACCEPTED=false' \
	'NATIVE_KERNEL_BOOT_CONFIRMED=false' \
	'FEDORA_EXECUTION_TOKEN_RECEIVED=false' \
	'DIAGNOSTIC_USB_PRODUCT_OBSERVED=false' \
	'RETURN_TO_STOCK_ANDROID_CONFIRMED=true' \
	'BOOT_AUTHORIZATION_CONSUMED=true' \
	'RETRY_AUTHORIZED=false' \
	'PARTITIONS_FLASHED=false' \
	'SLOTS_CHANGED=false' \
	'CUSTOM_KEY_INSTALLED=false' \
	'FLASH_AUTHORIZED=false'; do
	grep -Fqx "$boundary" "$rejected_result" || die "rejected v6 result lacks: $boundary"
	grep -Fqx "$boundary" "$rejected_revocation" 2>/dev/null || case "$boundary" in FEDORA_EXECUTION_TOKEN_RECEIVED=false) ;; *) die "rejected v6 revocation lacks: $boundary" ;; esac
done

accepted_sha=$(manifest_value PRIOR_CANDIDATE_SHA256 "$rejected_reproducibility")
accepted_result_sha=$(manifest_value PRIOR_ACCEPTANCE_SHA256 "$rejected_reproducibility")
grep -Fqx 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_UFS_ACCEPTANCE_VERSION=1' "$accepted_acceptance" || die 'accepted UFS result version differs'
grep -Fqx "CANDIDATE_SHA256=$accepted_sha" "$accepted_acceptance" || die 'accepted UFS digest differs from v6 provenance'
[ "$(sha256sum "$accepted_acceptance" | awk '{print $1}')" = "$accepted_result_sha" ] || die 'accepted UFS record digest differs from v6 provenance'
for boundary in \
	'PHYSICAL_DIAGNOSTIC_ACCEPTED=true' \
	'NATIVE_KERNEL_BOOT_CONFIRMED=true' \
	'REAL_UFS_BLOCK_DEVICE_OBSERVED=true' \
	'UFS_FUNCTIONAL_ACCEPTED=true' \
	'PERSISTENT_FILESYSTEMS_MOUNTED=false' \
	'PERSISTENT_STORAGE_WRITES_ISSUED=false' \
	'BOOT_AUTHORIZATION_CONSUMED=true' \
	'RETRY_AUTHORIZED=false' \
	'PARTITIONS_FLASHED=false' \
	'SLOTS_CHANGED=false' \
	'CUSTOM_KEY_INSTALLED=false' \
	'FLASH_AUTHORIZED=false'; do
	grep -Fqx "$boundary" "$accepted_acceptance" || die "accepted UFS result lacks: $boundary"
done

[ "$candidate_sha" != "$rejected_sha" ] || die 'v7 partition did not change from rejected v6'
[ "$candidate_bytes" = "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] || die 'candidate size differs from stock partition'
grep -Fqx "PARTITION_SHA256=$candidate_sha" "$avb_a/manifest.env" || die 'candidate digest differs from AVB manifest'

mkdir -p "$output_dir/avb-proof"
for artifact in boot.img manifest.env avb.info.txt avb.verify.txt; do
	install -m 0644 "$avb_a/$artifact" "$output_dir/avb-proof/$artifact"
done
install -m 0644 "$accepted_acceptance" "$output_dir/accepted-v5-acceptance.env"
install -m 0644 "$accepted_reproducibility" "$output_dir/accepted-v5-reproducibility.env"
install -m 0644 "$rejected_result" "$output_dir/rejected-v6-result.env"
install -m 0644 "$rejected_revocation" "$output_dir/rejected-v6-revocation.env"
install -m 0644 "$rejected_proof_manifest" "$output_dir/rejected-v6-proof.env"
install -m 0644 "$fedora_lock" "$output_dir/fedora44-ram-userspace-rpms.lock"

{
	printf 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_FEDORA_ZSTD_CANDIDATE_VERSION=1\n'
	printf 'CANDIDATE_KIND=direct-kernel-embedded-copied-dtb-fedora44-zstd-userspace-stock-size-avb-ecm-v7\n'
	printf 'DIAGNOSTIC_VARIANT=fedora-zstd-userspace-copy-dtb-v7\n'
	printf 'REPORT_VERSION=4\n'
	printf 'REPORT_TRANSPORTS=usb-ecm-http\n'
	printf 'FEDORA_RELEASE=44\nFEDORA_ARCH=aarch64\n'
	printf 'FEDORA_RPM_COUNT=%s\n' "$PIXEL9_DIAGNOSTIC_FEDORA_RPM_COUNT"
	printf 'FEDORA_RPM_LOCK_SHA256=%s\n' "$PIXEL9_DIAGNOSTIC_FEDORA_RPM_LOCK_SHA256"
	printf 'FEDORA_BASH_EXECUTION_REQUIRED=true\nFEDORA_GLIBC_EXECUTION_REQUIRED=true\n'
	printf 'INITRAMFS_KERNEL_COMPRESSION=zstd\n'
	printf 'RAW_INITRAMFS_IDENTICAL_TO_REJECTED_V6=true\n'
	printf 'RAW_KERNEL_SMALLER_THAN_STOCK=true\nRAW_KERNEL_SMALLER_THAN_REJECTED_V6=true\n'
	printf 'KERNEL_SIZE_HYPOTHESIS=reduce-raw-image-below-stock-by-zstd-compressing-identical-initramfs\n'
	printf 'COMPARISON=byte-for-byte\nAVB_PARTITION_IDENTICAL=true\nAVB_MANIFEST_IDENTICAL=true\nAVB_INFO_IDENTICAL=true\nAVB_VERIFY_REPORT_IDENTICAL=true\n'
	printf 'KERNEL_PROOF_IDENTICAL=true\nEMBEDDED_INITRAMFS_ROUNDTRIP_IDENTICAL=true\n'
	printf 'PRIOR_UFS_PHYSICAL_ACCEPTANCE_BOUND=true\n'
	printf 'PRIOR_CANDIDATE_SHA256=%s\nPRIOR_ACCEPTANCE_SHA256=%s\n' "$accepted_sha" "$accepted_result_sha"
	printf 'REJECTED_V6_RESULT_BOUND=true\nREJECTED_V6_REVOCATION_BOUND=true\nREJECTED_V6_PROOF_BOUND=true\n'
	printf 'REJECTED_V6_CANDIDATE_SHA256=%s\n' "$rejected_sha"
	printf 'REJECTED_V6_RESULT_SHA256=%s\n' "$rejected_result_sha"
	printf 'REJECTED_V6_REVOCATION_SHA256=%s\n' "$rejected_revocation_sha"
	printf 'REJECTED_V6_PROOF_SHA256=%s\n' "$rejected_proof_sha"
	printf 'RAW_INITRAMFS_BYTES=%s\nRAW_INITRAMFS_SHA256=%s\n' "$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_BYTES" "$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_SHA256"
	printf 'EMBEDDED_INITRAMFS_BYTES=%s\nEMBEDDED_INITRAMFS_SHA256=%s\n' "$embedded_bytes" "$embedded_sha"
	printf 'STOCK_KERNEL_BYTES=%s\nSTOCK_KERNEL_SHA256=%s\n' "$PIXEL9_STOCK_KERNEL_IMAGE_BYTES" "$PIXEL9_STOCK_KERNEL_IMAGE_SHA256"
	printf 'REJECTED_V6_KERNEL_BYTES=%s\nREJECTED_V6_KERNEL_SHA256=%s\n' "$rejected_kernel_bytes" "$rejected_kernel_sha"
	printf 'V7_KERNEL_BYTES=%s\nV7_KERNEL_SHA256=%s\n' "$kernel_bytes" "$kernel_sha"
	printf 'INNER_WRAPPER_SHA256=%s\nTOKAY_DTB_SHA256=%s\n' "$inner_sha" "$tokay_dtb_sha"
	printf 'CANDIDATE_BYTES=%s\nCANDIDATE_SHA256=%s\n' "$candidate_bytes" "$candidate_sha"
	printf 'PERSISTENT_FILESYSTEM_MOUNTS=false\nPERSISTENT_BLOCK_DEVICE_WRITES=false\n'
	printf 'INTERACTIVE_SHELL=false\nCOMMAND_ENDPOINT=false\nPHONE_ACCESSED=false\n'
	printf 'BOOT_AUTHORIZED=false\nFLASH_AUTHORIZED=false\n'
} >"$output_dir/reproducibility.env"
chmod 0644 "$output_dir/reproducibility.env"

printf 'Pixel 9 Fedora/Zstandard AVB diagnostic candidate (unauthorized): %s\n' "$output_dir"
printf 'Exact candidate SHA-256: %s\n' "$candidate_sha"
