#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -Eeuo pipefail
umask 077

stage=${LUMA_FP6_ENROLLMENT_STAGE:-/tmp/luma-fingerprint-enrollment-v125}
phase=/sys/bus/platform/devices/qcomtee/luma_focal_phase
firmware_path=/sys/module/firmware_class/parameters/path
expected_firmware_path=/lib/firmware/postmarketos
expected_qcomtee_sha=${LUMA_FP6_QCOMTEE_SHA256:-80a5fcaa2416563f5a88796cc841bfeea0f5bf284cf992e3b52ba7f5d09c17e0}
expected_listener_daemon_sha=${LUMA_FP6_LISTENER_DAEMON_SHA256:-1b59b3fbda0297b0338c18bf952c2d81c77a3ae3db56351d33cb028e30404c04}
expected_helper_sha=9b41c1368561a71450de764cec2b7bf1caf4925eed84025292f21582a35baccf
expected_focal_sha=3d90a639fc07d43afd04f31c6ebe313824cb5714356f6ef9de51777cb0a6cded
expected_installed_focal_sha=be860585db2c68ca80b3ee9e5e4929413c0d7207f9524ba489f974c46e26d051

fail() { printf 'event=fp6_enrollment_v115_deactivation_failed reason=%s\n' "$1" >&2; exit 1; }

[[ $(id -u) -eq 0 ]] || fail not_root
[[ -d $stage && ! -L $stage ]] || fail stage_identity
case $(cat "$phase") in
  complete|failed) ;;
  *) fail phase ;;
esac
[[ $(cat "$firmware_path") == "$stage/firmware" ]] || fail firmware_path
grep -qw qcomtee /proc/modules || fail qcomtee_missing
grep -qw focaltech_fp /proc/modules || fail focal_missing
[[ $(sha256sum "$stage/qcomtee-enrollment.ko" | cut -d' ' -f1) == "$expected_qcomtee_sha" ]] || fail qcomtee_hash
[[ $(sha256sum "$stage/luma-qcomtee-listener-supplicant" | cut -d' ' -f1) == "$expected_listener_daemon_sha" ]] || fail listener_daemon_hash
[[ $(sha256sum "$stage/fp6-focal-driver-stage" | cut -d' ' -f1) == "$expected_helper_sha" ]] || fail helper_hash
[[ $(sha256sum "$stage/focaltech_fp.ko" | cut -d' ' -f1) == "$expected_focal_sha" ]] || fail focal_hash
[[ -c /dev/luma-fp6-fingerprint ]] || fail enrollment_device_missing
[[ -r $stage/listener.pid ]] || fail listener_pid_missing
listener_pid=$(cat "$stage/listener.pid")
case $listener_pid in ''|*[!0-9]*) fail listener_pid_invalid ;; esac
kill -0 "$listener_pid" 2>/dev/null || fail listener_daemon_missing

if [[ $(cat "$phase") == complete ]]; then
  printf '%s\n' abort >"$phase" 2>/dev/null || [[ $(cat "$phase") == failed ]]
fi
kill -TERM "$listener_pid" 2>/dev/null || true
for _ in $(seq 1 20); do
  ! kill -0 "$listener_pid" 2>/dev/null && break
  sleep 0.1
done
if kill -0 "$listener_pid" 2>/dev/null; then
  kill -KILL "$listener_pid"
fi
"$stage/fp6-focal-driver-stage" cleanup
rmmod qcomtee
printf '%s' "$expected_firmware_path" >"$firmware_path"
rmmod focaltech_fp
modprobe focaltech_fp

installed_focal=$(modinfo -n focaltech_fp)
[[ $(sha256sum "$installed_focal" | cut -d' ' -f1) == "$expected_installed_focal_sha" ]] || fail installed_focal_hash
[[ -c /dev/focaltech_fp ]] || fail restored_device_missing
[[ ! -e /dev/luma-fp6-fingerprint ]] || fail enrollment_device_retained
[[ ! -e /dev/luma-fp6-qsee-listener ]] || fail listener_device_retained
[[ $(cat "$firmware_path") == "$expected_firmware_path" ]] || fail firmware_restore
! grep -qw qcomtee /proc/modules || fail qcomtee_retained

find "$stage" -depth -delete
[[ ! -e $stage ]] || fail stage_retained
printf 'event=fp6_enrollment_v125_deactivated normal_focal_restored=true listener_stopped=true partition_written=false rebooted=false\n'
