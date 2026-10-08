#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Read-only QSEE application identity probe for the accepted FP6 v11 boot.
# The lookup utility opens only /dev/teeN; it has no privileged loader path.

set -Eeuo pipefail

stage=/tmp/luma-qsee-lookup-v1
expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2
expected_boot_size=27512832
expected_boot_sha=cb9658d017462e23583ef89aee05d9e1a740c37aabef8f8229f10b9f6743edae
expected_config_sha=a403b3cb247d3f2ce358f8debcc3b93d3c1949813488da8e12ac81540cee519c
expected_module_sha=126074584721781d038312eb2d0129cd9d6044ee1b54523999a69824d54b7f5e
expected_probe_sha=8b5cdc47b47cef840c41b0b98cdd4b691fe5a34a5475a2c4e9b284230139bce4
module_loaded=false
cleaned=false

fail() {
	printf 'event=qsee_lookup_gate_failed reason=%s\n' "$1" >&2
	exit 1
}

critical_faults() {
	grep -Ei \
		'page allocation failure|allocation failed|WARNING: CPU|hangcheck|GMU.*(timeout|fault)|GPU.*(timeout|fault|hang)|qsee.*(fault|timeout)|TEE.*(fault|timeout)|BUG:|kernel panic|Oops:|Call trace:' \
		|| true
}

cleanup() {
	local status=$?
	local cleanup_status=0
	set +e
	if [[ $cleaned != true ]]; then
		if [[ $module_loaded == true ]] && grep -qw qseecomtee /proc/modules; then
			rmmod qseecomtee || cleanup_status=1
		fi
		if grep -qw qseecomtee /proc/modules; then
			printf 'event=qsee_lookup_cleanup_error reason=module_still_loaded\n' >&2
			cleanup_status=1
		fi
		if [[ -d $stage && ! -L $stage && $stage == /tmp/luma-qsee-lookup-v1 ]]; then
			find "$stage" -depth -delete || cleanup_status=1
		else
			printf 'event=qsee_lookup_cleanup_error reason=stage_identity\n' >&2
			cleanup_status=1
		fi
		cleaned=true
	fi
	printf 'event=qsee_lookup_cleanup module_loaded=%s stage_present=%s\n' \
		"$(grep -qw qseecomtee /proc/modules && printf true || printf false)" \
		"$([[ -e $stage ]] && printf true || printf false)"
	if ((cleanup_status != 0 && status == 0)); then
		return "$cleanup_status"
	fi
	return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ -d $stage && ! -L $stage ]] || fail stage_identity
[[ $(find "$stage" -maxdepth 1 -type f | wc -l) -eq 3 ]] || fail staged_file_count
[[ $(find "$stage" -type l | wc -l) -eq 0 ]] || fail staged_symlink
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == "$expected_model" ]] || fail model
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
[[ $(head -c "$expected_boot_size" /dev/disk/by-partlabel/boot_b | sha256sum | cut -d' ' -f1) == "$expected_boot_sha" ]] || fail boot_hash
runtime_config=$(gzip -dc /proc/config.gz)
[[ $(sha256sum <<<"$runtime_config" | cut -d' ' -f1) == "$expected_config_sha" ]] || fail runtime_config_hash
[[ $(sha256sum "$stage/qseecomtee.ko" | cut -d' ' -f1) == "$expected_module_sha" ]] || fail module_hash
[[ $(sha256sum "$stage/qsee-app-lookup" | cut -d' ' -f1) == "$expected_probe_sha" ]] || fail probe_hash
[[ $(sha256sum "$stage/run-fp6-qsee-lookup-remote.sh" | cut -d' ' -f1) == \
	$(sha256sum "$0" | cut -d' ' -f1) ]] || fail runner_identity
! grep -qw qseecomtee /proc/modules || fail module_already_loaded
[[ $(systemctl is-system-running) == running ]] || fail system_not_running
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units
dmesg_before=$(dmesg)
[[ -z $(critical_faults <<<"$dmesg_before") ]] || fail preexisting_critical_fault
baseline_dmesg_lines=$(wc -l <<<"$dmesg_before")

insmod "$stage/qseecomtee.ko"
module_loaded=true
grep -qw qseecomtee /proc/modules || fail module_did_not_load
[[ -c /dev/tee0 && -c /dev/teepriv0 ]] || fail tee_nodes
[[ $(stat -c '%a:%U:%G' /dev/tee0) == 600:root:root ]] || fail tee_node_permissions

for application in cmnlib64 cmnlib focal64; do
	set +e
	"$stage/qsee-app-lookup" "$application"
	lookup_status=$?
	set -e
	case $lookup_status in
		0|3) ;;
		*) fail "lookup_${application}_status_${lookup_status}" ;;
	esac
done

new_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
[[ -z $(critical_faults <<<"$new_dmesg") ]] || fail critical_kernel_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running_after_probe
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units_after_probe

rmmod qseecomtee
module_loaded=false
find "$stage" -depth -delete
[[ ! -e $stage ]] || fail stage_cleanup
cleaned=true
trap - EXIT INT TERM HUP
printf 'event=qsee_lookup_complete applications=cmnlib64,cmnlib,focal64 biometric_commands=0 loader_endpoint_used=false\n'
