#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Stop the bounded enrollment listeners while retaining only their root-only
# protected QSEE state for subsequent credential verification.

set -Eeuo pipefail
umask 077

stage=/tmp/luma-fp-listeners-enroll-v92
state=/var/lib/luma/fingerprint/qsee-state
expected_supplicant_sha=dd355adb58b1775752cc2ff1172b5e4ec2f5914429a9d0efbe7e8bd871e6085e

fail() { printf 'event=fp6_enrollment_listeners_v92_deactivation_failed reason=%s\n' "$1" >&2; exit 1; }

[[ $(id -u) -eq 0 ]] || fail not_root
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
printf 'event=fp6_enrollment_listeners_v92_deactivated protected_state_retained=true state_files=%s\n' "$state_files"
