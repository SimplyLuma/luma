#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Assemble the deterministic no-shell ACM+ECM diagnostic initramfs. This is an
# offline operation and never contacts a phone or packages an Android image.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

busybox=${1:?usage: build-pixel9-diagnostic-ecm-initramfs.sh BUSYBOX OUTPUT_DIR}
output_dir=${2:?usage: build-pixel9-diagnostic-ecm-initramfs.sh BUSYBOX OUTPUT_DIR}
init_source=$repo_root/config/mobile/pixel9-physical/diagnostic-init-ecm
root=$output_dir/root
archive=$output_dir/diagnostic-initramfs.cpio

die() {
	printf 'error: %s\n' "$*" >&2
	exit 1
}

[ "$(uname -s)" = Linux ] || die 'the diagnostic initramfs requires an off-device Linux builder'
if [ -r /sys/firmware/devicetree/base/model ]; then
	build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
	case "$build_device" in
		*Pixel*|*tokay*|*Tokay*) die 'refusing to assemble an initramfs on a Pixel target' ;;
	esac
fi
for tool in awk chmod cpio find install sha256sum sort stat touch tr; do
	command -v "$tool" >/dev/null 2>&1 || die "missing initramfs tool: $tool"
done
for required in "$busybox" "$init_source"; do
	[ -f "$required" ] || die "diagnostic initramfs input is absent: $required"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"
[ "$(stat -c %s "$busybox")" = "$PIXEL9_DIAGNOSTIC_BUSYBOX_BYTES" ] ||
	die 'BusyBox byte size differs from the pin'
[ "$(sha256sum "$busybox" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_BUSYBOX_SHA256" ] ||
	die 'BusyBox SHA-256 differs from the pin'
[ "$(sha256sum "$init_source" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_ECM_INIT_SHA256" ] ||
	die 'ECM diagnostic init differs from the pin'

mkdir -p "$root/bin"
install -m 0755 "$busybox" "$root/bin/busybox"
install -m 0755 "$init_source" "$root/init"
find "$root" -exec touch -h -d "@$PIXEL9_DIAGNOSTIC_SOURCE_DATE_EPOCH" {} +
(
	cd "$root"
	find . -print0 | LC_ALL=C sort -z |
		cpio --null --quiet --create --format=newc --reproducible --owner=0:0
) >"$archive"

cpio -i --quiet --to-stdout init <"$archive" |
	grep -Fq 'LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=2' ||
	die 'ECM diagnostic report marker is absent'
cpio -i --quiet --to-stdout init <"$archive" |
	grep -Fq 'USB_ECM_HTTP_READY=true' || die 'ECM transport marker is absent'

{
	printf 'LUMA_PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_VERSION=1\n'
	printf 'SCOPE=offline-no-shell-acm-ecm-http-initramfs\n'
	printf 'SOURCE_DATE_EPOCH=%s\n' "$PIXEL9_DIAGNOSTIC_SOURCE_DATE_EPOCH"
	printf 'BUSYBOX_BYTES=%s\n' "$PIXEL9_DIAGNOSTIC_BUSYBOX_BYTES"
	printf 'BUSYBOX_SHA256=%s\n' "$PIXEL9_DIAGNOSTIC_BUSYBOX_SHA256"
	printf 'INIT_SHA256=%s\n' "$PIXEL9_DIAGNOSTIC_ECM_INIT_SHA256"
	printf 'INITRAMFS_BYTES=%s\n' "$(stat -c %s "$archive")"
	printf 'INITRAMFS_SHA256=%s\n' "$(sha256sum "$archive" | awk '{print $1}')"
	printf 'REPORT_TRANSPORTS=usb-acm,usb-ecm-http\n'
	printf 'PERSISTENT_FILESYSTEM_MOUNTS=false\n'
	printf 'INTERACTIVE_SHELL=false\n'
	printf 'COMMAND_ENDPOINT=false\n'
	printf 'PHONE_ACCESSED=false\n'
	printf 'ANDROID_BOOT_IMAGE_ASSEMBLED=false\n'
	printf 'BOOT_AUTHORIZED=false\n'
	printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$archive" "$output_dir/manifest.env"

printf 'Pixel 9 ACM+ECM diagnostic initramfs: %s\n' "$archive"
