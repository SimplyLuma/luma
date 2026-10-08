#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Bounded physical acceptance runner for the FP6 QSEE listener-v2 kernel.
# This script is intended to be streamed to a root shell on the phone. It
# assumes the exact authorized payloads have already been staged and never
# issues a biometric, enrollment, authentication, or raw-image command.

set -Eeuo pipefail

stage=/tmp/luma-fingerprint-identity-v2
firmware_path=/sys/module/firmware_class/parameters/path
expected_old_firmware_path=/lib/firmware/postmarketos
expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2
expected_supplicant_sha=dd355adb58b1775752cc2ff1172b5e4ec2f5914429a9d0efbe7e8bd871e6085e
expected_loader_sha=4892bb2319a0362d898a7191ca77ff5d493325d2d1314e663aed6368f3d14ca1
expected_bundle_sha=104792353c21f86efd7eb9a5773acf033431bc7293b18062e817e898b2973df9
expected_focal_module_sha=e9ca41da358fa6bf0be128ed5ae7c386ea0496d2aa953c04274a3ac41c549987
expected_qsee_module_sha=030959db4f3c21ddeef76863594e7c5d184bfe484119fd9e483b662bd19cfcd5

supplicant_pid=
loader_pid=
old_firmware_path=
cleaned=false

log_file() {
  local label=$1
  local path=$2
  if [[ -f $path ]]; then
    printf '%s\n' "--- ${label} ---"
    sed -n '1,240p' "$path"
    printf '%s\n' "--- end ${label} ---"
  fi
}

stop_process() {
  local name=$1
  local pid=$2
  [[ -n $pid ]] || return 0
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid"
    for _ in {1..50}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
  fi
  if kill -0 "$pid" 2>/dev/null; then
    printf 'event=cleanup_error process=%s reason=did_not_stop_after_sigterm\n' "$name" >&2
    return 1
  fi
  wait "$pid" 2>/dev/null || true
}

cleanup() {
  local status=$?
  set +e
  if [[ $cleaned != true ]]; then
    stop_process qsee-app-loader "$loader_pid"
    stop_process qsee-supplicant "$supplicant_pid"
    log_file qsee-app-loader "$stage/loader.log"
    log_file qsee-supplicant "$stage/supplicant.log"
    if [[ -n $old_firmware_path && -w $firmware_path ]]; then
      printf '%s' "$old_firmware_path" >"$firmware_path"
    fi
    if [[ -d $stage && $stage == /tmp/luma-fingerprint-identity-v2 ]]; then
      find "$stage" -depth -delete
    fi
    cleaned=true
  fi
  if [[ -n $old_firmware_path && -r $firmware_path ]]; then
    printf 'event=cleanup firmware_path=%s stage_present=%s\n' \
      "$(cat "$firmware_path")" "$([[ -e $stage ]] && printf true || printf false)"
  fi
  return "$status"
}
trap cleanup EXIT INT TERM HUP

fail() {
  printf 'event=identity_v2_failed reason=%s\n' "$1" >&2
  exit 1
}

[[ $(id -u) -eq 0 ]] || fail not_root
[[ -d $stage && ! -L $stage ]] || fail stage_identity
[[ $(find "$stage" -type f | wc -l) -eq 12 ]] || fail staged_file_count
[[ $(find "$stage" -type l | wc -l) -eq 0 ]] || fail staged_symlink
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == "$expected_model" ]] || fail model
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
[[ $(sha256sum "$stage/qsee-supplicant" | cut -d' ' -f1) == "$expected_supplicant_sha" ]] || fail supplicant_hash
[[ $(sha256sum "$stage/qsee-app-loader" | cut -d' ' -f1) == "$expected_loader_sha" ]] || fail loader_hash
[[ $(sha256sum /lib/modules/7.1.2/extra/luma-fingerprint/focaltech_fp.ko | cut -d' ' -f1) == "$expected_focal_module_sha" ]] || fail focal_module_hash
[[ $(sha256sum /lib/modules/7.1.2/extra/luma-fingerprint/qseecomtee.ko | cut -d' ' -f1) == "$expected_qsee_module_sha" ]] || fail qsee_module_hash
[[ -c /dev/focaltech_fp && -c /dev/tee0 && -c /dev/teepriv0 ]] || fail device_nodes
[[ $(stat -c '%a:%U:%G' /dev/focaltech_fp) == 600:root:root ]] || fail focal_node_permissions
[[ $(stat -c '%a:%U:%G' /dev/tee0) == 600:root:root ]] || fail tee_node_permissions
[[ $(stat -c '%a:%U:%G' /dev/teepriv0) == 600:root:root ]] || fail teepriv_node_permissions
[[ $(find "$stage/firmware" -maxdepth 1 -type f -name 'focal64.*' | wc -l) -eq 10 ]] || fail trustlet_file_count

bundle_sha=$(
  cd "$stage/firmware"
  sha256sum \
    focal64.b00 focal64.b01 focal64.b02 focal64.b03 focal64.b04 \
    focal64.b05 focal64.b06 focal64.b07 focal64.b08 focal64.mdt |
    sha256sum | cut -d' ' -f1
)
[[ $bundle_sha == "$expected_bundle_sha" ]] || fail trustlet_bundle_hash
! pgrep -x qsee-supplicant >/dev/null || fail supplicant_already_running
! pgrep -x qsee-app-loader >/dev/null || fail loader_already_running

old_firmware_path=$(cat "$firmware_path")
[[ $old_firmware_path == "$expected_old_firmware_path" ]] || fail firmware_path_precondition
baseline_dmesg_lines=$(dmesg | wc -l)
printf '%s' "$stage/firmware" >"$firmware_path"
[[ $(cat "$firmware_path") == "$stage/firmware" ]] || fail firmware_path_stage

umask 077
"$stage/qsee-supplicant" --state-dir "$stage/state" >"$stage/supplicant.log" 2>&1 &
supplicant_pid=$!

listeners_ready=false
for _ in {1..100}; do
  kill -0 "$supplicant_pid" 2>/dev/null || fail supplicant_exited
  if grep -Eq 'event=(transport_error|notify_error)' "$stage/supplicant.log"; then
    fail supplicant_error
  fi
  while IFS= read -r listener_id; do
    case $listener_id in
      10|28672) ;;
      *) fail unexpected_listener ;;
    esac
  done < <(sed -n 's/.*event=listener_registered.* id=\([0-9][0-9]*\).*/\1/p' "$stage/supplicant.log")
  if grep -Eq 'event=listener_registered.* id=10([[:space:]]|$)' "$stage/supplicant.log" &&
     grep -Eq 'event=listener_registered.* id=28672([[:space:]]|$)' "$stage/supplicant.log"; then
    [[ $(grep -c 'event=listener_registered' "$stage/supplicant.log") -eq 2 ]] || fail listener_count
    listeners_ready=true
    break
  fi
  sleep 0.1
done
[[ $listeners_ready == true ]] || fail listener_timeout

"$stage/qsee-app-loader" focal64 >"$stage/loader.log" 2>&1 &
loader_pid=$!

application_loaded=false
for _ in {1..150}; do
  kill -0 "$loader_pid" 2>/dev/null || fail loader_exited
  if grep -Eq 'event=(loader_error|notify_error)' "$stage/loader.log"; then
    fail loader_error
  fi
  grep -q 'event=application_attached' "$stage/loader.log" && fail unexpected_preloaded_application
  if grep -Eq 'event=application_loaded app=focal64([[:space:]]|$)' "$stage/loader.log"; then
    [[ $(grep -c 'event=application_loaded' "$stage/loader.log") -eq 1 ]] || fail application_identity_count
    application_loaded=true
    break
  fi
  sleep 0.1
done
[[ $application_loaded == true ]] || fail application_load_timeout

# Keep the named session resident briefly, without opening a biometric client
# or issuing any command to the trusted application.
sleep 2
kill -0 "$loader_pid" 2>/dev/null || fail loader_did_not_remain_resident

new_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
if grep -Eiq '(hangcheck|GMU.*(timeout|fault)|GPU.*(timeout|fault|hang)|qsee.*(fault|timeout)|TEE.*(fault|timeout)|BUG:|kernel panic|Oops:|Call trace:)' <<<"$new_dmesg"; then
  printf '%s\n' "$new_dmesg" >&2
  fail critical_kernel_fault
fi

printf 'event=identity_v2_accepted listeners=10,28672 app=focal64 acquisition=loaded state_files=%s\n' \
  "$(find "$stage/state" -type f | wc -l)"

stop_process qsee-app-loader "$loader_pid"
loader_pid=
stop_process qsee-supplicant "$supplicant_pid"
supplicant_pid=
log_file qsee-app-loader "$stage/loader.log"
log_file qsee-supplicant "$stage/supplicant.log"
printf '%s' "$old_firmware_path" >"$firmware_path"
[[ $(cat "$firmware_path") == "$old_firmware_path" ]] || fail firmware_path_restore
find "$stage" -depth -delete
[[ ! -e $stage ]] || fail stage_cleanup
cleaned=true
trap - EXIT INT TERM HUP

printf 'event=identity_v2_cleanup_complete firmware_path=%s stage_present=false\n' "$old_firmware_path"
