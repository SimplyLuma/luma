#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -Eeuo pipefail
umask 077

stage=/tmp/luma-fp-listeners-enroll-v95
state=/var/lib/luma/fingerprint/qsee-state
expected_supplicant_sha=d979286aef1fecb23dd250192c6a062863ef999f38927b2e2ae329627af8154c

fail() { printf 'event=fp6_rpmb_listeners_v95_deactivation_failed reason=%s\n' "$1" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail not_root
# Active Gatekeeper/KeyMint/Focal objects can still call listener 8192.  Release
# their qcomtee module first; otherwise secure firmware rejects deregistration
# and Linux correctly leaves the listener allocated rather than use-after-free.
! grep -qw qcomtee /proc/modules || fail enrollment_transport_must_be_released_first
[[ -d $stage && ! -L $stage ]] || fail stage
[[ -d $state && ! -L $state && $(stat -c %a "$state") == 700 ]] || fail state
[[ $(sha256sum "$stage/qsee-supplicant" | cut -d' ' -f1) == "$expected_supplicant_sha" ]] || fail supplicant_hash
[[ -f $stage/supplicant.pid && ! -L $stage/supplicant.pid ]] || fail pid_file
supplicant_pid=$(cat "$stage/supplicant.pid")
[[ $supplicant_pid =~ ^[1-9][0-9]*$ ]] || fail pid
kill -0 "$supplicant_pid" 2>/dev/null || fail supplicant_missing
[[ $(readlink -f "/proc/$supplicant_pid/exe") == "$stage/qsee-supplicant" ]] || fail process_identity

kill -TERM "$supplicant_pid"
for _ in {1..50}; do
	kill -0 "$supplicant_pid" 2>/dev/null || break
	sleep 0.1
done
! kill -0 "$supplicant_pid" 2>/dev/null || fail supplicant_did_not_stop
grep -qw qseecomtee /proc/modules || fail module_missing
rmmod qseecomtee
! grep -qw qseecomtee /proc/modules || fail module_retained

state_files=$(find "$state" -type f | wc -l)
find "$stage" -depth -delete
[[ ! -e $stage ]] || fail stage_retained
printf 'event=fp6_rpmb_listeners_v95_deactivated protected_state_retained=true state_files=%s\n' "$state_files"
