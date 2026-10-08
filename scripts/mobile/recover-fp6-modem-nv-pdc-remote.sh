#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Bounded recovery for an FP6 whose modemst partitions are blank. The modem is
# the only writer, through the hash-locked recovery rmtfs. This script restores
# the already-accepted DSDS-LA-Milos platform, then returns to the normal
# read-only rmtfs service before restarting the modem. Carrier software is a
# separate persistence gate after the platform survives that restart.

set -Eeuo pipefail
umask 077

recovery=/tmp/rmtfs-fp6-nv-recovery
qmicli=/tmp/qmicli-pdc-typed
platform=/tmp/mcfg_hw_dsds_milos.mbn
remoteproc=/sys/class/remoteproc/remoteproc1
zero_sha=e5b844cc57f57094ea4585e235f36c78c1cd222262bb89d53c94dcb4d6b3e55d
fsg_sha=a776c844c220fa278479f63b3dd0ea6ebdd12bc5267af02592dd895dd76494ab
platform_id=00b4217c22737e3fc64ddea978a4218c4282769e
recovery_pid=
modem_running=true
normal_services=false

event() { printf 'event=luma_fp6_modem_recovery %s\n' "$*"; }
fail() { event state=failed reason="$1" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d' ' -f1; }
wait_modem() {
  for _ in $(seq 1 60); do
    "$qmicli" -d qrtr://0 --device-open-qmi --dms-get-operating-mode >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}
stop_modem() {
  [[ $(cat "$remoteproc/name") == modem ]] || fail remoteproc_identity
  if [[ $(cat "$remoteproc/state") == running ]]; then
    printf stop >"$remoteproc/state"
  fi
  for _ in $(seq 1 30); do
    [[ $(cat "$remoteproc/state") == offline ]] && { modem_running=false; return 0; }
    sleep 0.2
  done
  fail modem_stop
}
start_modem() {
  case $(cat "$remoteproc/state") in
    offline) printf start >"$remoteproc/state" ;;
    running) ;;
    *) fail modem_start_precondition ;;
  esac
  modem_running=true
  wait_modem || fail modem_start_timeout
}
cleanup() {
  local status=$?
  set +e
  if [[ $status -ne 0 && -r /run/luma-fp6-modem-recovery.log ]]; then
    sed -n '1,120p' /run/luma-fp6-modem-recovery.log >&2
  fi
  if [[ $modem_running == true ]]; then
    printf stop >"$remoteproc/state" 2>/dev/null
    sleep 1
    modem_running=false
  fi
  if [[ -n $recovery_pid ]] && kill -0 "$recovery_pid" 2>/dev/null; then
    kill -TERM "$recovery_pid" 2>/dev/null
    wait "$recovery_pid" 2>/dev/null
  fi
  systemctl stop tqftpserv.service 2>/dev/null
  systemctl start rmtfs.service tqftpserv.service 2>/dev/null
  normal_services=true
  if [[ $(cat "$remoteproc/state" 2>/dev/null) == offline ]]; then
    printf start >"$remoteproc/state" 2>/dev/null
    modem_running=true
  fi
  systemctl start ModemManager.service luma-fp6-cellular-link.service 2>/dev/null
  find /run/luma-fp6-modem-recovery.log -delete 2>/dev/null
  find "$recovery" "$qmicli" "$platform" -delete 2>/dev/null
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] || fail model
grep -qw androidboot.slot_suffix=_b /proc/cmdline || fail slot
case $(uname -r) in
  7.1.2-luma-fp-cma1|7.1.2-luma-fp-ims1) ;;
  *) fail kernel ;;
esac
[[ $(hash "$recovery") == 6c1d2211f68bcb3a2bca01f170c5b5b60d522c3aa1bfe52741f3431c161268dc ]] || fail recovery_hash
[[ $(hash "$qmicli") == 1b4adcd3d949e8d0f68065ba0022bbc35fe4462717deaaeae0ec244557f76b9c ]] || fail qmicli_hash
[[ $(hash "$platform") == 281cf305a5bcd98b709a0446f9733d1859cb423a791b2af17ab9776ffeaf0400 ]] || fail platform_hash
[[ $(blockdev --getsize64 /dev/disk/by-partlabel/modemst1) == 10485760 ]] || fail modemst1_size
[[ $(blockdev --getsize64 /dev/disk/by-partlabel/modemst2) == 10485760 ]] || fail modemst2_size
[[ $(hash /dev/disk/by-partlabel/modemst1) == "$zero_sha" ]] || fail modemst1_not_blank
[[ $(hash /dev/disk/by-partlabel/modemst2) == "$zero_sha" ]] || fail modemst2_not_blank
[[ $(hash /dev/disk/by-partlabel/fsg) == "$fsg_sha" ]] || fail fsg_hash
[[ $(cat "$remoteproc/name") == modem && $(cat "$remoteproc/state") == running ]] || fail modem_state

systemctl stop luma-fp6-cellular-link.service ModemManager.service
systemctl stop tqftpserv.service rmtfs.service
stop_modem
: >/run/luma-fp6-modem-recovery.log
chmod 0600 /run/luma-fp6-modem-recovery.log
"$recovery" -r -P -s -R >/run/luma-fp6-modem-recovery.log 2>&1 &
recovery_pid=$!
sleep 1
kill -0 "$recovery_pid" 2>/dev/null || fail recovery_start
systemctl start tqftpserv.service
start_modem

[[ $("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=platform | grep -c '^Total configurations: 0$') == 1 ]] || fail platform_precondition
[[ $("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=software | grep -c '^Total configurations: 0$') == 1 ]] || fail software_precondition
"$qmicli" -d qrtr://0 --device-open-qmi --pdc-load-config="platform,$platform" >/dev/null

# Milos accepts PDC activation immediately but does not reliably deliver the
# terminal indication. Bound both waits and verify the authoritative PDC list
# before continuing; otherwise a successful recovery can hang indefinitely.
set +e
timeout --signal=TERM --kill-after=5 20 \
  "$qmicli" -d qrtr://0 --device-open-qmi \
  --pdc-activate-config="platform,$platform_id" >/dev/null 2>&1
platform_activation_status=$?
set -e
case $platform_activation_status in
  # Milos may keep the indication wait alive past TERM.  A forced timeout is
  # acceptable only because the authoritative PDC list is checked next.
  0|124|137|143) ;;
  *) fail platform_activation_transport ;;
esac
"$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=platform |
  grep -Eq 'Status:[[:space:]]+(Pending|Active)' || fail platform_not_accepted

# Stop the modem while its accepted state is still backed by the bounded
# writable daemon. Only after it is offline do we restore normal read-only
# rmtfs, preventing shutdown-time modem writes.
stop_modem
  kill -TERM "$recovery_pid"
  wait "$recovery_pid"
  recovery_pid=
  # The raw modemst writes may otherwise remain visible only through the block
  # cache and disappear at the next boot. Flush both exact recovery targets
  # while the modem is still offline, then re-read them for the hash gate.
  sync
  blockdev --flushbufs /dev/disk/by-partlabel/modemst1
  blockdev --flushbufs /dev/disk/by-partlabel/modemst2
[[ $(hash /dev/disk/by-partlabel/modemst1) != "$zero_sha" ]] || fail modemst1_not_persisted
[[ $(hash /dev/disk/by-partlabel/modemst2) != "$zero_sha" ]] || fail modemst2_not_persisted
systemctl stop tqftpserv.service
systemctl start rmtfs.service tqftpserv.service
normal_services=true
start_modem

"$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=platform | grep -q 'Status:      Active' || fail platform_not_active
"$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=software |
  grep -q '^Total configurations: 0$' || fail software_not_empty
systemctl start ModemManager.service luma-fp6-cellular-link.service
modem_state=
for _ in $(seq 1 60); do
  if [[ $(mmcli -L 2>/dev/null | grep -c '/Modem/' || true) -eq 1 ]]; then
    modem_state=$(mmcli -m 0 --output-keyvalue 2>/dev/null |
      awk -F: '/modem.generic.state[[:space:]]*:/{gsub(/[[:space:]]/,"",$2); print $2}')
    [[ -n $modem_state ]] && break
  fi
  sleep 2
done
[[ -n $modem_state ]] || fail modemmanager_missing
[[ $modem_state != failed ]] || fail modemmanager_failed

trap - EXIT INT TERM HUP
find /run/luma-fp6-modem-recovery.log -delete
find "$recovery" "$qmicli" "$platform" -delete
event state=accepted platform=DSDS-LA-Milos software=none identifiers_logged=0
