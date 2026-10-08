#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Hardware-free construction gate. This starts the composed Android container
# without modem, audio, GPU, input, eUICC, loop, or block devices, captures the
# exact boot evidence, and always tears it down. It never changes Luma service
# ownership.

set -Eeuo pipefail
umask 077

name=luma-stock-android-c1
lxc_path=/var/lib/lxc
state=/var/lib/luma/stock-android-container1
manifest=$state/manifest.json
log_root=$state/state/logs
run_id=$(date -u +%Y%m%dT%H%M%SZ)
run_dir=$log_root/$run_id
container_started=false
result=fail
kernel_follow_pid=

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

stop_kernel_capture() {
  if [[ -n ${kernel_follow_pid:-} ]]; then
    kill "$kernel_follow_pid" 2>/dev/null || true
    wait "$kernel_follow_pid" 2>/dev/null || true
    kernel_follow_pid=
  fi
  chmod 0600 "$run_dir/kernel-new.log"
}

check_luma_owners() {
  local service
  for service in ModemManager luma-fp6-cellular-link luma-fp6-imsd; do
    systemctl is-active --quiet "$service" || return 1
  done
}

cleanup() {
  local status
  status=$(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || printf UNKNOWN)
  if [[ $status == RUNNING || $status == FROZEN ]]; then
    lxc-stop -P "$lxc_path" -n "$name" -k >/dev/null 2>&1 || true
  fi
  if [[ -d $run_dir ]]; then
    stop_kernel_capture || true
    systemctl is-active ModemManager luma-fp6-cellular-link luma-fp6-imsd \
      >"$run_dir/luma-owners-after.log" 2>&1 || true
    chmod 0600 "$run_dir/luma-owners-after.log"
    printf 'result=%s\ncontainer_started=%s\n' "$result" "$container_started" \
      >"$run_dir/result.env"
    chmod 0600 "$run_dir/result.env"
  fi
}
trap cleanup EXIT

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
model=$(tr -d '\0' </sys/firmware/devicetree/base/model)
[[ $model == 'The Fairphone (Gen. 6)' ]] || die 'device identity mismatch'
[[ $(uname -r) == 7.1.2-luma-fp-ims1 ]] || die 'ims1-compatible release is not running'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not running slot b'
zcat /proc/config.gz | grep -Fx 'CONFIG_ANDROID_BINDERFS=y' >/dev/null || die 'BinderFS is not enabled'
[[ -f $manifest && ! -L $manifest ]] || die 'container manifest absent or linked'
[[ -f $lxc_path/$name/config && ! -L $lxc_path/$name/config ]] || die 'LXC config absent or linked'
python3 - "$manifest" "$lxc_path/$name/config" <<'PY'
import hashlib
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    manifest = json.load(stream)
with open(sys.argv[2], "rb") as stream:
    actual = hashlib.sha256(stream.read()).hexdigest()
assert manifest["version"] == 1
assert manifest["name"] == "luma-stock-android-c1"
assert manifest["kernel_release"] == "7.1.2-luma-fp-ims1"
assert manifest["config_sha256"] == actual
assert manifest["started"] is False
for key in (
    "modem_exposed",
    "audio_exposed",
    "gpu_exposed",
    "input_exposed",
    "loop_or_block_exposed",
):
    assert manifest[key] is False
assert sorted(manifest["private_binder_devices"]) == ["binder", "hwbinder", "vndbinder"]
PY
[[ $(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || printf STOPPED) == STOPPED ]] ||
  die 'container is already active'
check_luma_owners || die 'a Luma modem owner is unexpectedly inactive'
for process in qcrilNrd imsdaemon ims-dataservice-daemon ims_rtp_daemon; do
  ! pgrep -f "(^|/)$process([[:space:]]|$)" >/dev/null ||
    die "stock process already running: $process"
done

install -d -m 0700 "$run_dir"
# Android init can emit enough shutdown output to wrap the kernel ring in a
# few seconds. Capture only new records continuously so the initiating fault
# cannot be lost before teardown.
stdbuf -oL dmesg --follow-new --raw >"$run_dir/kernel-new.log" &
kernel_follow_pid=$!
systemctl is-active ModemManager luma-fp6-cellular-link luma-fp6-imsd \
  >"$run_dir/luma-owners-before.log"
sha256sum "$manifest" "$lxc_path/$name/config" >"$run_dir/input-hashes.log"
chmod 0600 "$run_dir/luma-owners-before.log" "$run_dir/input-hashes.log"

# Android first-stage init normally enters second stage with a zero umask.
# C1 launches second stage directly, while this evidence harness otherwise
# keeps umask 077 for its root-only logs.  Constrain the standard Android
# umask to the lxc-start child so /dev/__properties__ receives its requested
# 0711 directory and read-only public property-file modes; without this,
# non-root framework services cannot observe servicemanager readiness.
(
  umask 000
  exec lxc-start -P "$lxc_path" -n "$name" -d -l TRACE -o "$run_dir/lxc.log"
)
container_started=true
for _ in {1..20}; do
  [[ $(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || true) == RUNNING ]] && break
  sleep 1
done
[[ $(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || true) == RUNNING ]] ||
  die 'container did not remain running'

boot_completed=false
for _ in {1..90}; do
  status=$(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || true)
  [[ $status == RUNNING ]] || die "container stopped during boot: $status"
  value=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /system/bin/getprop sys.boot_completed 2>/dev/null || true)
  if [[ $value == 1 ]]; then
    boot_completed=true
    break
  fi
  sleep 2
done

init_pid=$(lxc-info -P "$lxc_path" -n "$name" -pH)
[[ $init_pid =~ ^[0-9]+$ ]] || die 'container init PID unavailable'
readlink "/proc/$init_pid/root/dev/binder" >"$run_dir/binder-link.log"
readlink "/proc/$init_pid/root/dev/hwbinder" >>"$run_dir/binder-link.log"
readlink "/proc/$init_pid/root/dev/vndbinder" >>"$run_dir/binder-link.log"
find "/proc/$init_pid/root/dev/binderfs" -maxdepth 1 -printf '%f %m %D:%i\n' \
  | sort >"$run_dir/binderfs.log"
sha256sum "/proc/$init_pid/root/system/bin/servicemanager" \
  "/proc/$init_pid/root/system_ext/bin/hwservicemanager" \
  "/proc/$init_pid/root/vendor/bin/vndservicemanager" >"$run_dir/manager-hashes.log"
lxc-attach -P "$lxc_path" -n "$name" --clear-env -- /system/bin/getprop \
  >"$run_dir/getprop.log" 2>&1 || true
lxc-attach -P "$lxc_path" -n "$name" --clear-env -- /system/bin/ps -A -o PID,UID,NAME \
  >"$run_dir/processes.log" 2>&1 || true
lxc-attach -P "$lxc_path" -n "$name" --clear-env -- /system/bin/service list \
  >"$run_dir/services.log" 2>&1 || true
lxc-attach -P "$lxc_path" -n "$name" --clear-env -- /system/bin/lshal \
  >"$run_dir/hwservices.log" 2>&1 || true
chmod 0600 "$run_dir"/*.log

if grep -Eq '(^|[[:space:]/])(qcrilNrd|imsdaemon|ims-dataservice-daemon|ims_rtp_daemon|audioserver|surfaceflinger)([[:space:]]|$)' \
  "$run_dir/processes.log"; then
  die 'a C1-blocked hardware process is running'
fi
find "/proc/$init_pid/root/dev" -xdev -type b -print -quit | grep -q . &&
  die 'container exposes a block device'
check_luma_owners || die 'a Luma modem owner changed during C1'
stop_kernel_capture
if grep -Eiq 'kernel panic|Oops:|(^|[[:space:]<])BUG:|hangcheck|GMU.*(timeout|[[:space:]:=_-]fault)|qsee.*(panic|fatal|abort|timeout|[[:space:]:=_-]fault)|TEE.*(panic|fatal|abort|timeout|[[:space:]:=_-]fault)|I/O error' \
  "$run_dir/kernel-new.log"; then
  die 'kernel/TEE/GPU/GMU fault observed'
fi
[[ $boot_completed == true ]] || die 'Android did not reach sys.boot_completed=1'

result=pass
printf 'C1_HARDWARE_FREE_BOOT=true\n'
printf 'ANDROID_BOOT_COMPLETED=true\n'
printf 'LUMA_MODEM_OWNERS_UNCHANGED=true\n'
printf 'BLOCKED_HARDWARE_PROCESSES=0\n'
printf 'EVIDENCE_DIR=%s\n' "$run_dir"
