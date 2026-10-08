#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# One-shot, fail-closed QSEE APP_REGION_NOTIFICATION diagnostic for FP6 v12.

set -Eeuo pipefail

stage=/tmp/luma-qsee-region-probe-v1
expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2
expected_boot_size=27512832
expected_boot_sha=ad781b6f747412b8e7a0e06137e61f96434f00b5f6d1fad776e87dcd47d96425
expected_config_sha=a403b3cb247d3f2ce358f8debcc3b93d3c1949813488da8e12ac81540cee519c
expected_qsee_module_sha=126074584721781d038312eb2d0129cd9d6044ee1b54523999a69824d54b7f5e
expected_probe_module_sha=2148b7bc79c7806d09c6806a28e07bac374e33d137eeca08f4cc94f48268d839
qsee_loaded=false
probe_loaded=false
cleaned=false

fail() {
	printf 'event=qsee_region_probe_failed reason=%s\n' "$1" >&2
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
		if [[ $probe_loaded == true ]] && grep -qw luma_qsee_region_probe /proc/modules; then
			rmmod luma_qsee_region_probe || cleanup_status=1
		fi
		if [[ $qsee_loaded == true ]] && grep -qw qseecomtee /proc/modules; then
			rmmod qseecomtee || cleanup_status=1
		fi
		if grep -Eqw 'luma_qsee_region_probe|qseecomtee' /proc/modules; then
			printf 'event=qsee_region_cleanup_error reason=module_still_loaded\n' >&2
			cleanup_status=1
		fi
		if [[ -d $stage && ! -L $stage && $stage == /tmp/luma-qsee-region-probe-v1 ]]; then
			find "$stage" -depth -delete || cleanup_status=1
		else
			printf 'event=qsee_region_cleanup_error reason=stage_identity\n' >&2
			cleanup_status=1
		fi
		cleaned=true
	fi
	printf 'event=qsee_region_cleanup probe_loaded=%s qsee_loaded=%s stage_present=%s\n' \
		"$(grep -qw luma_qsee_region_probe /proc/modules && printf true || printf false)" \
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
[[ $(sha256sum "$stage/qseecomtee.ko" | cut -d' ' -f1) == "$expected_qsee_module_sha" ]] || fail qsee_module_hash
[[ $(sha256sum "$stage/luma_qsee_region_probe.ko" | cut -d' ' -f1) == "$expected_probe_module_sha" ]] || fail probe_module_hash
! grep -Eqw 'luma_qsee_region_probe|qseecomtee' /proc/modules || fail module_already_loaded
! pgrep -x qsee-supplicant >/dev/null || fail supplicant_running
! pgrep -x qsee-app-loader >/dev/null || fail loader_running
[[ $(systemctl is-system-running) == running ]] || fail system_not_running
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units
dmesg_before=$(dmesg)
[[ -z $(critical_faults <<<"$dmesg_before") ]] || fail preexisting_critical_fault
baseline_dmesg_lines=$(wc -l <<<"$dmesg_before")

insmod "$stage/qseecomtee.ko"
qsee_loaded=true
grep -qw qseecomtee /proc/modules || fail qsee_module_did_not_load
[[ -c /dev/tee0 && -c /dev/teepriv0 ]] || fail tee_nodes

set +e
insmod "$stage/luma_qsee_region_probe.ko"
probe_status=$?
set -e
if ((probe_status == 0)); then
	probe_loaded=true
fi

new_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
printf '%s\n' '--- QSEE app-region evidence ---'
grep -Ei 'dedicated .* heap|dedicated QSEECOM|SHM Bridge|luma_qsee_region|qseecom|QSEE' <<<"$new_dmesg" || true
printf '%s\n' '--- end QSEE app-region evidence ---'
((probe_status == 0)) || fail "probe_insmod_status_${probe_status}"
grep -Fq 'luma_qsee_region: app region accepted' <<<"$new_dmesg" || fail secure_acceptance_absent
[[ -z $(critical_faults <<<"$new_dmesg") ]] || fail critical_kernel_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running_after_probe
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units_after_probe

rmmod luma_qsee_region_probe
probe_loaded=false
rmmod qseecomtee
qsee_loaded=false
find "$stage" -depth -delete
[[ ! -e $stage ]] || fail stage_cleanup
cleaned=true
trap - EXIT INT TERM HUP
printf 'event=qsee_region_probe_complete secure_acceptance=true biometric_commands=0 ta_loads=0\n'
