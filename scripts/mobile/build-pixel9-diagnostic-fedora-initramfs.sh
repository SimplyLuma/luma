#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Assemble a deterministic, RAM-only Fedora 44 AArch64 execution proof. The
# exact signed RPM closure is validated before extraction. This is an offline
# artifact build and contains no phone transport, shell listener, or storage
# mutation path.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

rpm_dir=${1:?usage: build-pixel9-diagnostic-fedora-initramfs.sh RPM_DIR BUSYBOX OUTPUT_DIR}
busybox=${2:?usage: build-pixel9-diagnostic-fedora-initramfs.sh RPM_DIR BUSYBOX OUTPUT_DIR}
output_dir=${3:?usage: build-pixel9-diagnostic-fedora-initramfs.sh RPM_DIR BUSYBOX OUTPUT_DIR}
lock=$repo_root/config/mobile/pixel9-physical/$PIXEL9_DIAGNOSTIC_FEDORA_RPM_LOCK
init_source=$repo_root/config/mobile/pixel9-physical/$PIXEL9_DIAGNOSTIC_FEDORA_INIT
root=$output_dir/root
archive=$output_dir/diagnostic-initramfs.cpio
validated=$output_dir/validated-rpms.tsv

die() {
	printf 'error: %s\n' "$*" >&2
	exit 1
}

[ "$(uname -s)" = Linux ] || die 'the Fedora diagnostic initramfs requires an off-device Linux builder'
if [ -r /sys/firmware/devicetree/base/model ]; then
	build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
	case "$build_device" in
		*Pixel*|*tokay*|*Tokay*) die 'refusing to assemble an initramfs on a Pixel target' ;;
	esac
fi
for tool in awk chmod cpio find grep install mkdir qemu-aarch64-static rpm rpm2cpio rpmkeys sha256sum sort stat touch tr; do
	command -v "$tool" >/dev/null 2>&1 || die "missing Fedora initramfs tool: $tool"
done
for required in "$rpm_dir" "$busybox" "$lock" "$init_source"; do
	[ -e "$required" ] || die "Fedora initramfs input is absent: $required"
done
[ -d "$rpm_dir" ] || die 'RPM input is not a directory'
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"
[ "$(stat -c %s "$busybox")" = "$PIXEL9_DIAGNOSTIC_BUSYBOX_BYTES" ] ||
	die 'BusyBox byte size differs from the pin'
[ "$(sha256sum "$busybox" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_BUSYBOX_SHA256" ] ||
	die 'BusyBox SHA-256 differs from the pin'
[ "$(sha256sum "$lock" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_FEDORA_RPM_LOCK_SHA256" ] ||
	die 'Fedora RPM lock SHA-256 differs from the pin'
[ "$(sha256sum "$init_source" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_FEDORA_INIT_SHA256" ] ||
	die 'Fedora diagnostic init SHA-256 differs from the pin'

mkdir -p "$output_dir" "$root/bin" "$root/usr/share/luma"
: >"$validated"
expected_count=0
while read -r expected_sha expected_bytes expected_nevra filename extra; do
	case "$expected_sha" in ''|'#'*) continue ;; esac
	[ -z "${extra:-}" ] || die "malformed RPM lock entry: $filename"
	case "$filename" in *.rpm) ;; *) die "non-RPM filename in lock: $filename" ;; esac
	case "$filename" in */*|*..*) die "unsafe RPM filename in lock: $filename" ;; esac
	rpm_path=$rpm_dir/$filename
	[ -f "$rpm_path" ] || die "locked RPM is absent: $filename"
	[ "$(stat -c %s "$rpm_path")" = "$expected_bytes" ] || die "RPM byte size differs: $filename"
	[ "$(sha256sum "$rpm_path" | awk '{print $1}')" = "$expected_sha" ] || die "RPM SHA-256 differs: $filename"
	actual_nevra=$(rpm -qp --qf '%{NEVRA}' "$rpm_path")
	[ "$actual_nevra" = "$expected_nevra" ] || die "RPM NEVRA differs: $filename"
	rpmkeys --checksig "$rpm_path" | grep -Fq 'digests signatures OK' || die "RPM signature verification failed: $filename"
	printf '%s\t%s\t%s\t%s\n' "$expected_sha" "$expected_bytes" "$expected_nevra" "$filename" >>"$validated"
	expected_count=$((expected_count + 1))
done <"$lock"
[ "$expected_count" -gt 0 ] || die 'RPM lock contains no payloads'
[ "$expected_count" = "$PIXEL9_DIAGNOSTIC_FEDORA_RPM_COUNT" ] || die 'RPM lock count differs from the pin'
actual_count=$(find "$rpm_dir" -maxdepth 1 -type f -name '*.rpm' -printf '%f\n' | wc -l)
[ "$actual_count" = "$expected_count" ] || die 'RPM directory contains files outside or missing from the lock'

extract_rpm() {
	filename=$1
	(
		cd "$root"
		rpm2cpio "$rpm_dir/$filename" </dev/null |
			cpio --quiet --extract --make-directories --unconditional
	)
}

# Fedora's filesystem package establishes the usr-merge symlinks. Extract it
# before any library payload so cpio never has to replace a populated /lib or
# /lib64 directory with the canonical link.
filesystem_rpm=$(awk '$3 ~ /^filesystem-/ { print $4 }' "$validated")
[ -n "$filesystem_rpm" ] || die 'locked Fedora filesystem RPM is absent'
extract_rpm "$filesystem_rpm"
while IFS=$'\t' read -r _sha _bytes _nevra filename; do
	[ "$filename" = "$filesystem_rpm" ] && continue
	extract_rpm "$filename"
done <"$validated"

install -m 0755 "$busybox" "$root/bin/busybox"
install -m 0755 "$init_source" "$root/init"
install -m 0644 "$lock" "$root/usr/share/luma/pixel9-fedora44-rpms.lock"
for required in "$root/usr/bin/bash" "$root/usr/lib/ld-linux-aarch64.so.1" "$root/usr/lib64/libc.so.6" "$root/usr/lib/os-release"; do
	[ -e "$required" ] || die "extracted Fedora runtime input is absent: $required"
done

qemu-aarch64-static -L "$root" "$root/usr/bin/bash" --noprofile --norc -c '
	set -eu
	. "$1/usr/lib/os-release"
	[ "$ID" = fedora ]
	[ "$VERSION_ID" = 44 ]
	[ -n "$BASH_VERSION" ]
	printf "ID=%s VERSION_ID=%s BASH_VERSION=%s\n" "$ID" "$VERSION_ID" "$BASH_VERSION"
' _ "$root" >"$output_dir/host-aarch64-execution.txt"
grep -Fq 'ID=fedora VERSION_ID=44 BASH_VERSION=5.3.9' "$output_dir/host-aarch64-execution.txt" ||
	die 'host-side AArch64 Fedora Bash execution proof differs'
qemu-aarch64-static -L "$root" "$root/lib64/libc.so.6" >"$output_dir/host-aarch64-glibc.txt" 2>&1 || true
grep -Fq 'GNU C Library' "$output_dir/host-aarch64-glibc.txt" || die 'host-side AArch64 Fedora glibc proof differs'

find "$root" -exec touch -h -d "@$PIXEL9_DIAGNOSTIC_SOURCE_DATE_EPOCH" {} +
(
	cd "$root"
	find . -print0 | LC_ALL=C sort -z |
		cpio --null --quiet --create --format=newc --reproducible --owner=0:0
) >"$archive"

for marker in \
	'LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=4' \
	'FEDORA_BASH_EXECUTION_TOKEN=LUMA_PIXEL9_FEDORA44_AARCH64_OK' \
	'PERSISTENT_FILESYSTEMS_MOUNTED=false' \
	'PERSISTENT_BLOCK_DEVICES_WRITTEN=false' \
	'INTERACTIVE_SHELL_EXPOSED=false' \
	'COMMAND_ENDPOINT_EXPOSED=false' \
	'USB_ECM_HTTP_READY=true'; do
	cpio -i --quiet --to-stdout init <"$archive" | grep -F "$marker" >/dev/null ||
		die "Fedora diagnostic marker is absent: $marker"
done

{
	printf 'LUMA_PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_VERSION=1\n'
	printf 'SCOPE=offline-no-shell-fedora44-aarch64-execution-proof-initramfs\n'
	printf 'SOURCE_DATE_EPOCH=%s\n' "$PIXEL9_DIAGNOSTIC_SOURCE_DATE_EPOCH"
	printf 'BUSYBOX_BYTES=%s\n' "$PIXEL9_DIAGNOSTIC_BUSYBOX_BYTES"
	printf 'BUSYBOX_SHA256=%s\n' "$PIXEL9_DIAGNOSTIC_BUSYBOX_SHA256"
	printf 'FEDORA_RPM_COUNT=%s\n' "$expected_count"
	printf 'FEDORA_RPM_LOCK_SHA256=%s\n' "$(sha256sum "$lock" | awk '{print $1}')"
	printf 'INIT_SHA256=%s\n' "$(sha256sum "$init_source" | awk '{print $1}')"
	printf 'INITRAMFS_BYTES=%s\n' "$(stat -c %s "$archive")"
	printf 'INITRAMFS_SHA256=%s\n' "$(sha256sum "$archive" | awk '{print $1}')"
	printf 'REPORT_VERSION=4\n'
	printf 'REPORT_TRANSPORTS=usb-ecm-http\n'
	printf 'FEDORA_BASH_EXECUTED_ON_BUILD_HOST=true\n'
	printf 'FEDORA_GLIBC_EXECUTED_ON_BUILD_HOST=true\n'
	printf 'PERSISTENT_FILESYSTEM_MOUNTS=false\n'
	printf 'PERSISTENT_BLOCK_DEVICE_WRITES=false\n'
	printf 'INTERACTIVE_SHELL=false\n'
	printf 'COMMAND_ENDPOINT=false\n'
	printf 'PHONE_ACCESSED=false\n'
	printf 'ANDROID_BOOT_IMAGE_ASSEMBLED=false\n'
	printf 'BOOT_AUTHORIZED=false\n'
	printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$archive" "$output_dir/manifest.env" "$validated" \
	"$output_dir/host-aarch64-execution.txt" "$output_dir/host-aarch64-glibc.txt"

printf 'Pixel 9 Fedora diagnostic initramfs: %s\n' "$archive"
