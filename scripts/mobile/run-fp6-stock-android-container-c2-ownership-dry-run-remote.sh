#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# C2 proves that Luma can hand modem and audio ownership to a future stock
# Android container and restore the native owners without starting Android.
# It sends no message, places no call, and exposes no hardware to a container.

set -Eeuo pipefail
umask 077

expected_kernel=7.1.2-luma-fp-ims1
expected_mm_evr=1.25.95-0.2.luma2.fc44
state=/var/lib/luma/stock-android-container1/state
run_id=$(date -u +%Y%m%dT%H%M%SZ)
start_epoch=$(date +%s)
run_dir=$state/logs/c2-ownership-$run_id
user_name=luma
user_id=$(id -u "$user_name")
user_runtime=/run/user/$user_id
system_services=(luma-fp6-imsd luma-fp6-cellular-link ModemManager)
audio_system_services=(alsa-state)
audio_user_sockets=(pipewire-pulse.socket pipewire.socket)
audio_user_services=(wireplumber.service pipewire-pulse.service pipewire.service)
declare -A was_active=()
restored=false
result=fail
failure=none

die() { failure=$1; printf 'error: %s\n' "$1" >&2; exit 1; }

user_systemctl() {
  runuser -u "$user_name" -- env \
    XDG_RUNTIME_DIR="$user_runtime" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$user_runtime/bus" \
    systemctl --user "$@"
}

service_was_live() {
  local state_value
  state_value=$(systemctl is-active "$1" 2>/dev/null || true)
  [[ $state_value == active || $state_value == activating ]]
}

user_service_was_live() {
  local state_value
  state_value=$(user_systemctl is-active "$1" 2>/dev/null || true)
  [[ $state_value == active || $state_value == activating ]]
}

restore_owners() {
  local service
  [[ $restored == false ]] || return 0
  set +e
  for service in "${audio_system_services[@]}"; do
    [[ ${was_active[system:$service]:-false} == true ]] && systemctl start "$service"
  done
  # Restore socket activation before its services, matching the normal user
  # session topology without relying on a service start to recreate a socket.
  for service in "${audio_user_sockets[@]}"; do
    [[ ${was_active[user:$service]:-false} == true ]] && user_systemctl start "$service"
  done
  for service in "${audio_user_services[@]}"; do
    [[ ${was_active[user:$service]:-false} == true ]] && user_systemctl start "$service"
  done
  [[ ${was_active[system:ModemManager]:-false} == true ]] && systemctl start ModemManager
  if [[ ${was_active[system:luma-fp6-cellular-link]:-false} == true ]]; then
    systemctl start luma-fp6-cellular-link
  fi
  if [[ ${was_active[system:luma-fp6-imsd]:-false} == true ]]; then
    systemctl start luma-fp6-imsd
  fi
  restored=true
  set -e
}

cleanup() {
  local status=$?
  if [[ $status -ne 0 && $failure == none ]]; then
    failure=unexpected_command_failure
  fi
  restore_owners
  if [[ -d $run_dir ]]; then
    {
      printf 'result=%s\n' "$result"
      printf 'failure=%s\n' "$failure"
      printf 'owners_restored=%s\n' "$restored"
    } >"$run_dir/result.env"
    chmod 0600 "$run_dir"/* 2>/dev/null || true
  fi
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || die must_run_as_root
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] ||
  die device_identity_mismatch
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die slot_mismatch
[[ $(uname -r) == "$expected_kernel" ]] || die kernel_mismatch
[[ $(rpm -q --qf '%{EVR}' ModemManager) == "$expected_mm_evr" ]] ||
  die modemmanager_version_mismatch
[[ $(lxc-info -P /var/lib/lxc -n luma-stock-android-c1 -sH 2>/dev/null || printf STOPPED) == STOPPED ]] ||
  die android_container_already_running
for process in qcrilNrd imsdaemon ims-dataservice-daemon ims_rtp_daemon; do
  ! pgrep -f "(^|/)${process}([[:space:]]|$)" >/dev/null || die stock_android_process_already_running
done

install -d -o root -g root -m 0700 "$run_dir"
printf 'kernel=%s\nslot=b\nmodemmanager_evr=%s\nandroid_started=false\n' \
  "$expected_kernel" "$expected_mm_evr" >"$run_dir/identity.env"
systemctl show "${system_services[@]}" "${audio_system_services[@]}" \
  -p Id -p ActiveState -p SubState -p MainPID -p InvocationID -p NRestarts \
  --no-pager >"$run_dir/system-services.before.log"
user_systemctl show "${audio_user_sockets[@]}" "${audio_user_services[@]}" \
  -p Id -p ActiveState -p SubState -p MainPID -p InvocationID -p NRestarts \
  --no-pager >"$run_dir/user-audio.before.log"
fuser -v /dev/snd/* >"$run_dir/audio-fds.before.log" 2>&1 || true

for service in "${system_services[@]}" "${audio_system_services[@]}"; do
  if service_was_live "$service"; then
    was_active[system:$service]=true
  else
    was_active[system:$service]=false
  fi
done
for service in "${audio_user_sockets[@]}" "${audio_user_services[@]}"; do
  if user_service_was_live "$service"; then
    was_active[user:$service]=true
  else
    was_active[user:$service]=false
  fi
done
[[ ${was_active[system:ModemManager]} == true ]] || die modemmanager_not_active
[[ ${was_active[system:luma-fp6-cellular-link]} == true ]] || die cellular_link_not_active

# Stop top-down so clients release their transports before ModemManager and
# ALSA state ownership disappear. Explicitly disconnect the native data
# context first: leaving it active across a ModemManager restart can produce a
# bearer that reports connected while the carrier silently drops its packets.
systemctl stop luma-fp6-imsd
systemctl stop luma-fp6-cellular-link
mmcli -m any --simple-disconnect >"$run_dir/modem-disconnect.log" 2>&1 ||
  die modem_disconnect_failed
systemctl stop ModemManager
# Stop activation sockets first. Stop units individually because systemd may
# report a canceled job while it resolves the PipeWire socket/service cycle;
# the following state and FD checks are the authoritative acceptance gate.
for service in "${audio_user_sockets[@]}"; do
  user_systemctl stop "$service" || true
done
for service in "${audio_user_services[@]}"; do
  user_systemctl stop "$service" || true
done
systemctl stop "${audio_system_services[@]}"

for service in "${system_services[@]}" "${audio_system_services[@]}"; do
  systemctl is-active --quiet "$service" && die system_owner_remained_active
done
for service in "${audio_user_sockets[@]}" "${audio_user_services[@]}"; do
  user_systemctl is-active --quiet "$service" && die user_audio_owner_remained_active
done
! pgrep -x ModemManager >/dev/null || die modemmanager_process_remained
! pgrep -x imsd >/dev/null || die luma_ims_process_remained
if fuser /dev/snd/* >/dev/null 2>&1; then
  fuser -v /dev/snd/* >"$run_dir/audio-fds.exclusive-failure.log" 2>&1 || true
  die audio_fd_remained
fi
printf 'exclusive_modem_owners=0\nexclusive_audio_fds=0\nandroid_started=false\n' \
  >"$run_dir/exclusive-window.env"
sleep 3

restore_owners

for _ in $(seq 1 120); do
  if systemctl is-active --quiet ModemManager &&
     mmcli -L 2>/dev/null | grep -q '/Modem/'; then
    break
  fi
  sleep 1
done
systemctl is-active --quiet ModemManager || die modemmanager_restore_failed
[[ $(mmcli -L 2>/dev/null | grep -c '/Modem/' || true) -eq 1 ]] ||
  die modem_count_after_restore
for _ in $(seq 1 120); do
  modem_state=$(mmcli -m any --output-keyvalue 2>/dev/null |
    awk -F: '/modem.generic.state[[:space:]]*:/{gsub(/[[:space:]]/,"",$2); print $2}')
  registration=$(mmcli -m any --output-keyvalue 2>/dev/null |
    awk -F: '/modem.3gpp.registration-state[[:space:]]*:/{gsub(/[[:space:]]/,"",$2); print $2}')
  if [[ $modem_state == registered || $modem_state == connected ]] &&
     [[ $registration == home || $registration == roaming ]]; then
    break
  fi
  sleep 1
done
[[ $modem_state == registered || $modem_state == connected ]] || die modem_not_restored
[[ $registration == home || $registration == roaming ]] || die registration_not_restored

for _ in $(seq 1 120); do
  data_interface=$(ip -4 route show default 2>/dev/null |
    awk '$1=="default" && $4=="dev" && $5 ~ /^qmapmux[0-9]+[.][0-9]+$/ {print $5; exit}')
  [[ -n $data_interface ]] && break
  sleep 1
done
[[ -n ${data_interface:-} ]] || die cellular_route_not_restored
cellular_data_smoke=false
for _ in $(seq 1 12); do
  if curl -4 --interface "$data_interface" --fail --silent --show-error \
    --output /dev/null --max-time 5 https://example.com/; then
    cellular_data_smoke=true
    break
  fi
  sleep 2
done
[[ $cellular_data_smoke == true ]] || die cellular_data_smoke_failed
mmcli -m any --messaging-status --output-keyvalue 2>/dev/null |
  grep -q '^modem.messaging.supported-storages' || die sms_interface_not_restored

for service in "${audio_system_services[@]}"; do
  [[ ${was_active[system:$service]} == false ]] ||
    systemctl is-active --quiet "$service" || die system_audio_restore_failed
done
for service in "${audio_user_sockets[@]}" "${audio_user_services[@]}"; do
  [[ ${was_active[user:$service]} == false ]] ||
    user_systemctl is-active --quiet "$service" || die user_audio_restore_failed
done

systemctl show "${system_services[@]}" "${audio_system_services[@]}" \
  -p Id -p ActiveState -p SubState -p MainPID -p InvocationID -p NRestarts \
  --no-pager >"$run_dir/system-services.after.log"
user_systemctl show "${audio_user_sockets[@]}" "${audio_user_services[@]}" \
  -p Id -p ActiveState -p SubState -p MainPID -p InvocationID -p NRestarts \
  --no-pager >"$run_dir/user-audio.after.log"
fuser -v /dev/snd/* >"$run_dir/audio-fds.after.log" 2>&1 || true
journalctl -k --since "@$start_epoch" --no-pager |
  grep -Ei 'Kernel panic|Oops:|BUG: unable to handle|hangcheck detected gpu lockup|GMU.*(timeout|fault)|QSEE.*(fault|panic)|TEE.*(fault|panic)' \
  >"$run_dir/faults.log" || true
[[ ! -s $run_dir/faults.log ]] || die critical_fault_after_restore

printf 'modem_restored=true\nregistration_restored=true\ncellular_data_smoke=true\nsms_interface_smoke=true\naudio_restored=true\nandroid_started=false\n' \
  >"$run_dir/acceptance.env"
result=pass
failure=none

printf 'C2_OWNERSHIP_DRY_RUN=true\n'
printf 'exclusive_modem_owners=0\n'
printf 'exclusive_audio_fds=0\n'
printf 'modem_restored=true\n'
printf 'registration_restored=true\n'
printf 'cellular_data_smoke=true\n'
printf 'sms_interface_smoke=true\n'
printf 'audio_restored=true\n'
printf 'android_started=false\n'
printf 'evidence_dir=%s\n' "$run_dir"
