#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Load the exact unmodified QCOMTEE object driver without opening it, verify
# its physical node topology and fault window, then remove it. No secure app,
# listener, container, modem, audio, partition, or boot state is touched.

set -Eeuo pipefail
umask 077

module=${1:?usage: run-fp6-stock-android-qcomtee-boot-gate-remote.sh QCOMTEE_MODULE}
expected_kernel=7.1.2-luma-fp-ims1
expected_module_sha=54b336ee9c420173ef2419c5bd2d1d4192c72c485d3a2e3ba7f85c32e2019ab7
run_dir=/var/lib/luma/stock-android-container1/state/logs/qcomtee-boot-$(date -u +%Y%m%dT%H%M%SZ)
kernel_follow_pid=
module_loaded=false
result=fail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
check_luma_owners() {
  local service
  for service in ModemManager luma-fp6-cellular-link luma-fp6-imsd; do
    systemctl is-active --quiet "$service" || return 1
  done
}
cleanup() {
  local status=$?
  set +e
  if [[ $module_loaded == true ]] && grep -qw qcomtee /proc/modules; then
    rmmod qcomtee 2>/dev/null || true
  fi
  if [[ -n $kernel_follow_pid ]]; then
    kill "$kernel_follow_pid" 2>/dev/null || true
    wait "$kernel_follow_pid" 2>/dev/null || true
  fi
  if [[ -d $run_dir ]]; then
    systemctl is-active ModemManager luma-fp6-cellular-link luma-fp6-imsd \
      >"$run_dir/luma-owners-after.log" 2>&1 || true
    printf 'result=%s\nmodule_loaded=%s\n' "$result" "$module_loaded" \
      >"$run_dir/result.env"
    chmod 0600 "$run_dir"/* 2>/dev/null || true
  fi
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] ||
  die 'device identity mismatch'
[[ $(uname -r) == "$expected_kernel" ]] || die 'unexpected kernel release'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not on slot b'
check_luma_owners || die 'a Luma modem owner is unexpectedly inactive'
[[ -f $module && ! -L $module ]] || die 'module is absent or linked'
[[ $(sha256sum "$module" | cut -d' ' -f1) == "$expected_module_sha" ]] ||
  die 'module hash mismatch'
[[ $(modinfo -F name "$module") == qcomtee ]] || die 'module identity mismatch'
[[ $(modinfo -F vermagic "$module" | awk '{print $1}') == "$expected_kernel" ]] ||
  die 'module vermagic mismatch'
! grep -qw qcomtee /proc/modules || die 'qcomtee is already loaded'
! grep -qw qseecomtee /proc/modules || die 'qseecomtee is already loaded'
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 0 ]] ||
  die 'preexisting TEE nodes found'

install -d -o root -g root -m 0700 "$run_dir"
sha256sum "$module" >"$run_dir/input-hashes.log"
systemctl is-active ModemManager luma-fp6-cellular-link luma-fp6-imsd \
  >"$run_dir/luma-owners-before.log"
stdbuf -oL dmesg --follow-new --raw >"$run_dir/kernel-new.log" &
kernel_follow_pid=$!

insmod "$module"
module_loaded=true
[[ -c /dev/tee0 ]] || die 'QCOMTEE client node was not created'
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 1 ]] ||
  die 'unexpected QCOMTEE node topology'
[[ ! -e /dev/teepriv0 ]] || die 'QCOMTEE unexpectedly exposed a privileged node'
printf 'module=qcomtee\nclient=/dev/tee0\nprivileged_node=false\n' >"$run_dir/topology.log"
sleep 2

rmmod qcomtee
module_loaded=false
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 0 ]] ||
  die 'TEE node remained after module removal'
kill "$kernel_follow_pid" 2>/dev/null || true
wait "$kernel_follow_pid" 2>/dev/null || true
kernel_follow_pid=

if grep -Eiq 'kernel panic|Oops:|(^|[[:space:]<])BUG:|hangcheck|GMU.*(timeout|[[:space:]:=_-]fault)|qsee.*(panic|fatal|abort|timeout|[[:space:]:=_-]fault)|TEE.*(panic|fatal|abort|timeout|[[:space:]:=_-]fault)|I/O error' \
  "$run_dir/kernel-new.log"; then
  die 'kernel/TEE/GPU/GMU fault observed'
fi
check_luma_owners || die 'a Luma modem owner did not survive the gate'
result=pass
printf 'QCOMTEE_BOOT_GATE=true\n'
printf 'QCOMTEE_CLIENT=/dev/tee0\n'
printf 'SECURE_COMMANDS_SENT=false\n'
printf 'EVIDENCE_DIR=%s\n' "$run_dir"
