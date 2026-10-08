#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Arm before a separately authorized Pixel 9 RAM boot. This host-only observer
# never boots, flashes, reboots, opens an ADB shell, or writes to the phone. It
# captures the immutable HTTP report as soon as the diagnostic ECM address is
# assigned, then records bounded return to stock.

set -euo pipefail
umask 077

candidate_sha=${1:?usage: observe-pixel9-diagnostic.sh CANDIDATE_SHA256 EVIDENCE_DIR}
evidence_dir=${2:?usage: observe-pixel9-diagnostic.sh CANDIDATE_SHA256 EVIDENCE_DIR}
fastboot_bin=${FASTBOOT:-fastboot}
adb_bin=${ADB:-adb}
curl_bin=${CURL:-curl}
timeout_seconds=${LUMA_PIXEL9_OBSERVER_TIMEOUT_SECONDS:-300}
report_version=${LUMA_PIXEL9_OBSERVER_REPORT_VERSION:-2}
diagnostic_serial=${LUMA_PIXEL9_OBSERVER_USB_SERIAL:-luma-pixel9-volatile-v2}
host_address=192.168.77.2
report_url=http://192.168.77.1/luma-pixel9-report.txt

die() {
	printf 'error: %s\n' "$*" >&2
	exit 1
}

case "$candidate_sha" in
	*[!0-9a-f]*|'') die 'candidate SHA-256 is absent or malformed' ;;
esac
[ "${#candidate_sha}" -eq 64 ] || die 'candidate SHA-256 is not 64 hexadecimal characters'
case "$report_version" in
	2|3|4) ;;
	*) die 'observer report version must be 2, 3, or 4' ;;
esac
case "$diagnostic_serial" in
	*[!0-9A-Za-z._-]*|'') die 'observer USB serial is malformed' ;;
esac
case "$timeout_seconds" in
	*[!0-9]*|'') die 'observer timeout must be an integer' ;;
esac
[ "$timeout_seconds" -ge 30 ] || die 'observer timeout must be at least 30 seconds'
[ ! -e "$evidence_dir" ] || die "refusing to replace observer evidence: $evidence_dir"

for tool in awk chmod date find grep head ifconfig ioreg mkdir mv rm sed shasum sleep tr wc "$fastboot_bin" "$adb_bin" "$curl_bin"; do
	command -v "$tool" >/dev/null 2>&1 || die "missing observer tool: $tool"
done

fastboot_rows=$($fastboot_bin devices 2>/dev/null | awk 'NF { count++ } END { print count + 0 }')
[ "$fastboot_rows" -eq 1 ] || die 'observer must be armed while exactly one phone is in fastboot'
fastboot_serial=$($fastboot_bin devices 2>/dev/null | awk 'NF { print $1; exit }')
[ -n "$fastboot_serial" ] || die 'fastboot serial is absent'

mkdir -p "$evidence_dir"
report_tmp=$evidence_dir/report.tmp
report_path=$evidence_dir/luma-pixel9-report.txt
observer_path=$evidence_dir/observer.env
start_epoch=$(date +%s)
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
deadline=$((start_epoch + timeout_seconds))

diagnostic_usb_observed=false
diagnostic_usb_first_at=absent
acm_observed=false
acm_nodes=absent
ecm_observed=false
ecm_interface=absent
ecm_ipv4_observed=false
report_received=false
report_received_at=absent
report_sha=absent
report_bytes=0
stock_adb_returned=false
stock_adb_returned_at=absent
stock_adb_row=absent

printf 'Pixel 9 passive observer armed for %s at %s\n' "$candidate_sha" "$started_at"

while [ "$(date +%s)" -le "$deadline" ]; do
	now=$(date -u +%Y-%m-%dT%H:%M:%SZ)

	if [ "$diagnostic_usb_observed" = false ] &&
		ioreg -p IOUSB -c IOUSBHostDevice -l -w 0 2>/dev/null |
		grep -Fq "$diagnostic_serial"; then
		diagnostic_usb_observed=true
		diagnostic_usb_first_at=$now
		printf 'Diagnostic USB product observed at %s\n' "$now"
	fi

	current_acm=$(find /dev -maxdepth 1 \( -name 'cu.usbmodem*' -o -name 'tty.usbmodem*' \) -print 2>/dev/null |
		tr '\n' ',' | sed 's/,$//')
	if [ -n "$current_acm" ]; then
		acm_observed=true
		acm_nodes=$current_acm
	fi

	current_ecm=absent
	for interface in $(ifconfig -l 2>/dev/null); do
		if ifconfig "$interface" 2>/dev/null | grep -Fq 'ether 02:4c:55:4d:41:02'; then
			current_ecm=$interface
			break
		fi
	done
	if [ "$current_ecm" != absent ]; then
		ecm_observed=true
		ecm_interface=$current_ecm
		if ifconfig "$current_ecm" 2>/dev/null | grep -Fq "inet $host_address "; then
			ecm_ipv4_observed=true
			if [ "$report_received" = false ]; then
				if "$curl_bin" --interface "$host_address" --connect-timeout 1 --max-time 2 \
					--fail --silent --show-error --output "$report_tmp" "$report_url" 2>/dev/null &&
					grep -Fqx "LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=$report_version" "$report_tmp" &&
					grep -Fqx 'REPORT_END=true' "$report_tmp"; then
					mv "$report_tmp" "$report_path"
					report_received=true
					report_received_at=$now
					report_sha=$(shasum -a 256 "$report_path" | awk '{print $1}')
					report_bytes=$(wc -c <"$report_path" | tr -d '[:space:]')
					printf 'Diagnostic HTTP report captured at %s: %s bytes, SHA-256 %s\n' \
						"$now" "$report_bytes" "$report_sha"
				fi
			fi
		fi
	fi

	stock_row=$($adb_bin devices -l 2>/dev/null | sed -n '2p')
	if [ "$diagnostic_usb_observed" = true ] && printf '%s\n' "$stock_row" | grep -Fq 'device:tokay'; then
		stock_adb_returned=true
		stock_adb_returned_at=$now
		stock_adb_row=$(printf '%s' "$stock_row" | tr '[:space:]' '_')
		printf 'Stock Android ADB transport returned at %s\n' "$now"
		break
	fi

	sleep 0.25
done

rm -f -- "$report_tmp"
finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
elapsed_seconds=$(($(date +%s) - start_epoch))
{
	printf 'LUMA_PIXEL9_PASSIVE_OBSERVER_VERSION=1\n'
	printf 'SCOPE=host-only-read-only-diagnostic-observation\n'
	printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
	printf 'EXPECTED_REPORT_VERSION=%s\n' "$report_version"
	printf 'EXPECTED_USB_SERIAL=%s\n' "$diagnostic_serial"
	printf 'FASTBOOT_SERIAL_AT_ARM=%s\n' "$fastboot_serial"
	printf 'STARTED_AT=%s\n' "$started_at"
	printf 'FINISHED_AT=%s\n' "$finished_at"
	printf 'ELAPSED_SECONDS=%s\n' "$elapsed_seconds"
	printf 'DIAGNOSTIC_USB_PRODUCT_OBSERVED=%s\n' "$diagnostic_usb_observed"
	printf 'DIAGNOSTIC_USB_FIRST_OBSERVED_AT=%s\n' "$diagnostic_usb_first_at"
	printf 'USB_ACM_SERIAL_NODES_OBSERVED=%s\n' "$acm_observed"
	printf 'USB_ACM_SERIAL_NODES=%s\n' "$acm_nodes"
	printf 'USB_ECM_INTERFACE_OBSERVED=%s\n' "$ecm_observed"
	printf 'USB_ECM_INTERFACE=%s\n' "$ecm_interface"
	printf 'USB_ECM_HOST_IPV4_OBSERVED=%s\n' "$ecm_ipv4_observed"
	printf 'USB_ECM_HTTP_REPORT_RECEIVED=%s\n' "$report_received"
	printf 'REPORT_RECEIVED_AT=%s\n' "$report_received_at"
	printf 'REPORT_BYTES=%s\n' "$report_bytes"
	printf 'REPORT_SHA256=%s\n' "$report_sha"
	printf 'RETURN_TO_STOCK_ANDROID_CONFIRMED=%s\n' "$stock_adb_returned"
	printf 'STOCK_ADB_RETURNED_AT=%s\n' "$stock_adb_returned_at"
	printf 'STOCK_ADB_ROW=%s\n' "$stock_adb_row"
	printf 'PHONE_MUTATION_COMMANDS_ISSUED=false\n'
	printf 'BOOT_COMMAND_ISSUED=false\n'
	printf 'FLASH_AUTHORIZED=false\n'
} >"$observer_path"
chmod 0600 "$observer_path"

printf 'Pixel 9 passive observer evidence: %s\n' "$evidence_dir"
[ "$report_received" = true ] && [ "$stock_adb_returned" = true ]
