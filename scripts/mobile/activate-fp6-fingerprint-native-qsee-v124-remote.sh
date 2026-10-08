#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Bring up one coherent legacy-QSEE topology: qseecomtee owns the signed
# focal64 application and the same transport owns its three stock listeners.
# This script loads no biometric client and issues no biometric command.

set -Eeuo pipefail
umask 077

stage=/tmp/luma-fp-native-qsee-v124
state=/var/lib/luma/fingerprint/qsee-state
firmware_path=/sys/module/firmware_class/parameters/path
expected_firmware_path=/lib/firmware/postmarketos
expected_kernel=7.1.2-luma-fp-cma1
expected_module_sha=953cf2afecb41b59beec1b507c95590190608240bc483767a58bf97828c8205e
expected_supplicant_sha=d979286aef1fecb23dd250192c6a062863ef999f38927b2e2ae329627af8154c
expected_loader_sha=57955bc42c50ab5fccd27fa7b27d9c3a1e9717cb2e213fc31c8c820f81335900
expected_bundle_sha=104792353c21f86efd7eb9a5773acf033431bc7293b18062e817e898b2973df9
supplicant_pid=
loader_pid=
success=false

fail() { printf 'event=fp6_native_qsee_v124_failed reason=%s\n' "$1" >&2; exit 1; }
cleanup() {
  local status=$?
  set +e
  if [[ $success != true ]]; then
    [[ -z $loader_pid ]] || kill -TERM "$loader_pid" 2>/dev/null || true
    [[ -z $supplicant_pid ]] || kill -TERM "$supplicant_pid" 2>/dev/null || true
    [[ $(cat "$firmware_path" 2>/dev/null) != "$stage/firmware" ]] ||
      printf '%s' "$expected_firmware_path" >"$firmware_path" 2>/dev/null || true
    grep -qw qseecomtee /proc/modules && rmmod qseecomtee 2>/dev/null || true
  fi
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] || fail model
! grep -qw qcomtee /proc/modules || fail qcomtee_loaded
! grep -qw qseecomtee /proc/modules || fail qseecomtee_loaded
! pgrep -x qsee-supplicant >/dev/null || fail supplicant_running
! pgrep -x qsee-app-loader >/dev/null || fail loader_running
[[ -c /dev/focaltech_fp ]] || fail focal_device
[[ -c /dev/bsg/0:0:0:49476 ]] || fail rpmb_device
[[ $(cat "$firmware_path") == "$expected_firmware_path" ]] || fail firmware_path
[[ -d $stage && ! -L $stage ]] || fail stage
[[ $(find "$stage" -type f | wc -l) -eq 13 ]] || fail file_count
[[ $(find "$stage" -type l | wc -l) -eq 0 ]] || fail symlink
[[ $(sha256sum "$stage/qseecomtee.ko" | cut -d' ' -f1) == "$expected_module_sha" ]] || fail module_hash
[[ $(sha256sum "$stage/qsee-supplicant" | cut -d' ' -f1) == "$expected_supplicant_sha" ]] || fail supplicant_hash
[[ $(sha256sum "$stage/qsee-app-loader" | cut -d' ' -f1) == "$expected_loader_sha" ]] || fail loader_hash
bundle_sha=$(cd "$stage/firmware" && sha256sum focal64.b0{0,1,2,3,4,5,6,7,8} focal64.mdt | sha256sum | cut -d' ' -f1)
[[ $bundle_sha == "$expected_bundle_sha" ]] || fail bundle_hash
[[ $(modinfo -F vermagic "$stage/qseecomtee.ko" | awk '{print $1}') == "$expected_kernel" ]] || fail vermagic

install -d -o root -g root -m 0700 "$state"
chmod 0600 "$stage/qseecomtee.ko"
chmod 0700 "$stage/qsee-supplicant" "$stage/qsee-app-loader"
printf '%s' "$stage/firmware" >"$firmware_path"
insmod "$stage/qseecomtee.ko" luma_adopt_resident_focal=0
grep -qw qseecomtee /proc/modules || fail module_load
[[ $(cat /sys/module/qseecomtee/parameters/luma_adopt_resident_focal) == N ]] || fail adoption_enabled
[[ -c /dev/tee0 && -c /dev/teepriv0 ]] || fail tee_nodes

"$stage/qsee-supplicant" --state-dir "$state" >"$stage/supplicant.log" 2>&1 &
supplicant_pid=$!
printf '%s\n' "$supplicant_pid" >"$stage/supplicant.pid"
for _ in {1..100}; do
  kill -0 "$supplicant_pid" 2>/dev/null || fail supplicant_exit
  grep -Eq 'event=(transport_error|notify_error|rpmb_rejected)' "$stage/supplicant.log" && fail supplicant_error
  if grep -Eq 'event=listener_registered.* id=10([[:space:]]|$)' "$stage/supplicant.log" &&
     grep -Eq 'event=listener_registered.* id=8192([[:space:]]|$)' "$stage/supplicant.log" &&
     grep -Eq 'event=listener_registered.* id=28672([[:space:]]|$)' "$stage/supplicant.log"; then
    [[ $(grep -c 'event=listener_registered' "$stage/supplicant.log") -eq 3 ]] || fail listener_count
    break
  fi
  sleep 0.1
done
[[ $(grep -c 'event=listener_registered' "$stage/supplicant.log") -eq 3 ]] || fail listener_timeout

"$stage/qsee-app-loader" focal64 >"$stage/loader.log" 2>&1 &
loader_pid=$!
printf '%s\n' "$loader_pid" >"$stage/loader.pid"
for _ in {1..150}; do
  kill -0 "$loader_pid" 2>/dev/null || fail loader_exit
  if grep -Eq 'event=application_loaded app=focal64([[:space:]]|$)' "$stage/loader.log"; then
    grep -q 'operation=load-session result=0' "$stage/loader.log" || fail load_result
    break
  fi
  grep -Eq 'event=(loader_error|notify_error)' "$stage/loader.log" && fail loader_error
  sleep 0.1
done
grep -Eq 'event=application_loaded app=focal64([[:space:]]|$)' "$stage/loader.log" || fail load_timeout

success=true
trap - EXIT INT TERM HUP
printf 'event=fp6_native_qsee_v124_active app=focal64 ownership=qseecomtee listeners=10,8192,28672 protected_state=true raw_images=0 biometric_commands=0\n'
