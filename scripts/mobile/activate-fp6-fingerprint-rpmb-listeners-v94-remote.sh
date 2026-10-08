#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -Eeuo pipefail
umask 077

stage=/tmp/luma-fp-listeners-enroll-v94
state=/var/lib/luma/fingerprint/qsee-state
expected_kernel=7.1.2-luma-fp-cma1
expected_module_sha=1f74052412d9fc34eb581cb7aac0df80d949ca2c6da9bd6e9e70dc2fa68415df
expected_supplicant_sha=7d256a141a91892ca936531ce24509a2d7c5a2e4c6f98284fb8b041beaff0d4c
success=false
supplicant_pid=

fail() { printf 'event=fp6_rpmb_listeners_v94_failed reason=%s\n' "$1" >&2; exit 1; }
cleanup() {
	local status=$?
	set +e
	if [[ $success != true ]]; then
		[[ -z $supplicant_pid ]] || kill -TERM "$supplicant_pid" 2>/dev/null || true
		[[ -z $supplicant_pid ]] || wait "$supplicant_pid" 2>/dev/null || true
		grep -qw qseecomtee /proc/modules && rmmod qseecomtee 2>/dev/null || true
	fi
	return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
grep -qw qcomtee /proc/modules || fail qcomtee_missing
[[ -c /dev/luma-fp6-fingerprint ]] || fail enrollment_device_missing
! grep -qw qseecomtee /proc/modules || fail qseecomtee_already_loaded
! pgrep -x qsee-supplicant >/dev/null || fail supplicant_already_running
[[ -c /dev/bsg/0:0:0:49476 ]] || fail rpmb_bsg_missing
[[ -d $stage && ! -L $stage ]] || fail stage
[[ $(find "$stage" -type f | wc -l) -eq 2 ]] || fail staged_file_count
[[ $(find "$stage" -type l | wc -l) -eq 0 ]] || fail staged_symlink
[[ $(sha256sum "$stage/qseecomtee.ko" | cut -d' ' -f1) == "$expected_module_sha" ]] || fail module_hash
[[ $(sha256sum "$stage/qsee-supplicant" | cut -d' ' -f1) == "$expected_supplicant_sha" ]] || fail supplicant_hash
[[ $(modinfo -F vermagic "$stage/qseecomtee.ko" | awk '{print $1}') == "$expected_kernel" ]] || fail module_vermagic

install -d -o root -g root -m 0700 "$state"
chown root:root "$stage/qseecomtee.ko" "$stage/qsee-supplicant"
chmod 0600 "$stage/qseecomtee.ko"
chmod 0700 "$stage/qsee-supplicant"
insmod "$stage/qseecomtee.ko"
grep -qw qseecomtee /proc/modules || fail module_load
[[ -c /dev/tee0 && -c /dev/teepriv0 ]] || fail tee_nodes

"$stage/qsee-supplicant" --state-dir "$state" >"$stage/supplicant.log" 2>&1 &
supplicant_pid=$!
printf '%s\n' "$supplicant_pid" >"$stage/supplicant.pid"
for _ in {1..100}; do
	kill -0 "$supplicant_pid" 2>/dev/null || fail supplicant_exit
	grep -Eq 'event=(transport_error|notify_error|rpmb_rejected)' "$stage/supplicant.log" && fail supplicant_error
	while IFS= read -r listener_id; do
		case $listener_id in 10|8192|28672) ;; *) fail unexpected_listener ;; esac
	done < <(sed -n 's/.*event=listener_registered.* id=\([0-9][0-9]*\).*/\1/p' "$stage/supplicant.log")
	if grep -Eq 'event=listener_registered.* id=10([[:space:]]|$)' "$stage/supplicant.log" &&
	   grep -Eq 'event=listener_registered.* id=8192([[:space:]]|$)' "$stage/supplicant.log" &&
	   grep -Eq 'event=listener_registered.* id=28672([[:space:]]|$)' "$stage/supplicant.log"; then
		[[ $(grep -c 'event=listener_registered' "$stage/supplicant.log") -eq 3 ]] || fail listener_count
		success=true
		trap - EXIT INT TERM HUP
		printf 'event=fp6_rpmb_listeners_v94_active listeners=10,8192,28672 state_mode=700 raw_images=0\n'
		exit 0
	fi
	sleep 0.1
done
fail listener_timeout
