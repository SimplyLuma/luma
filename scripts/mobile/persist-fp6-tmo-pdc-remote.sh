#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Persist the already-accepted TMO software PDC configuration after a guarded
# blank-NV reconstruction has made the DSDS platform configuration durable.

set -Eeuo pipefail
umask 077

writer=/tmp/rmtfs-fp6-modemst-writer
qmicli=/tmp/qmicli-pdc-typed
software=/tmp/mcfg_sw_tmo_commercial_qrel1695.mbn
remoteproc=/sys/class/remoteproc/remoteproc1
zero_sha=e5b844cc57f57094ea4585e235f36c78c1cd222262bb89d53c94dcb4d6b3e55d
fsg_sha=a776c844c220fa278479f63b3dd0ea6ebdd12bc5267af02592dd895dd76494ab
software_id=88f589369ff3a066511dd1b21ed42cf3496686a3
att_id=444954dcfbd6d5bf196cd8fecf6770ff3c6b34fc
writer_pid=

event() { printf 'event=luma_fp6_tmo_pdc_persistence %s\n' "$*"; }
fail() { event state=failed reason="$1" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d' ' -f1; }
compact_ids() {
  sed -n 's/^[[:space:]]*ID:[[:space:]]*//p' | tr -d ':' |
    tr '[:upper:]' '[:lower:]'
}
active_id() {
  awk '
    /^[[:space:]]*Status:[[:space:]]*Active/ { active=1; next }
    active && /^[[:space:]]*ID:/ {
      sub(/^[^:]*:[[:space:]]*/, "")
      print
      exit
    }
  ' | tr -d ':' | tr '[:upper:]' '[:lower:]'
}
wait_modem() {
  for _ in $(seq 1 60); do
    "$qmicli" -d qrtr://0 --device-open-qmi --dms-get-operating-mode >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}
stop_modem() {
  if [[ $(cat "$remoteproc/state") == running ]]; then
    printf stop >"$remoteproc/state"
  fi
  for _ in $(seq 1 30); do
    [[ $(cat "$remoteproc/state") == offline ]] && return 0
    sleep 0.2
  done
  fail modem_stop
}
cleanup() {
  local status=$?
  set +e
  [[ $(cat "$remoteproc/state" 2>/dev/null) != running ]] || printf stop >"$remoteproc/state" 2>/dev/null
  if [[ -n $writer_pid ]] && kill -0 "$writer_pid" 2>/dev/null; then
    kill -TERM "$writer_pid" 2>/dev/null
    wait "$writer_pid" 2>/dev/null
  fi
  systemctl stop tqftpserv.service 2>/dev/null
  systemctl start rmtfs.service tqftpserv.service 2>/dev/null
  [[ $(cat "$remoteproc/state" 2>/dev/null) != offline ]] || printf start >"$remoteproc/state" 2>/dev/null
  systemctl start ModemManager.service luma-fp6-cellular-link.service 2>/dev/null
  find /run/luma-fp6-modemst-writer.log -delete 2>/dev/null
  find "$writer" "$qmicli" "$software" -delete 2>/dev/null
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] || fail model
grep -qw androidboot.slot_suffix=_b /proc/cmdline || fail slot
[[ $(uname -r) == 7.1.2-luma-fp-ims1 ]] || fail kernel
[[ $(hash "$writer") == c9781b0d25e0bedb5e6ff4e96dd553f0ec443f9d65c64b4d3032ef9fa3245280 ]] || fail writer_hash
[[ $(hash "$qmicli") == 1b4adcd3d949e8d0f68065ba0022bbc35fe4462717deaaeae0ec244557f76b9c ]] || fail qmicli_hash
[[ $(hash "$software") == 817e5f33a18a176a14859184de88c60921318864dea994a845133bc841e3edda ]] || fail software_hash
[[ $(hash /dev/disk/by-partlabel/modemst1) != "$zero_sha" ]] || fail modemst1_blank
[[ $(hash /dev/disk/by-partlabel/modemst2) != "$zero_sha" ]] || fail modemst2_blank
[[ $(hash /dev/disk/by-partlabel/fsg) == "$fsg_sha" ]] || fail fsg_hash
[[ $("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=platform | grep -c 'Status:      Active') == 1 ]] || fail platform_precondition
software_before=$("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=software)
mapfile -t software_ids < <(compact_ids <<<"$software_before")
for id in "${software_ids[@]}"; do
  [[ $id == "$att_id" || $id == "$software_id" ]] || fail software_precondition
done
unset software_before software_ids id

systemctl stop luma-fp6-cellular-link.service ModemManager.service tqftpserv.service rmtfs.service
stop_modem
: >/run/luma-fp6-modemst-writer.log
chmod 0600 /run/luma-fp6-modemst-writer.log
"$writer" -r -P -s -W >/run/luma-fp6-modemst-writer.log 2>&1 &
writer_pid=$!
sleep 1
kill -0 "$writer_pid" 2>/dev/null || fail writer_start
systemctl start tqftpserv.service
wait_modem || fail modem_start_timeout
software_live=$("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=software)
if ! compact_ids <<<"$software_live" | grep -qx "$software_id"; then
  "$qmicli" -d qrtr://0 --device-open-qmi --pdc-load-config="software,$software" >/dev/null
fi

# This modem accepts the activation immediately but omits the terminal PDC
# indication. Bound the client wait and verify the authoritative list instead.
set +e
timeout --signal=TERM --kill-after=5 20 \
  "$qmicli" -d qrtr://0 --device-open-qmi \
  --pdc-activate-config="software,$software_id" >/dev/null 2>&1
activation_status=$?
set -e
case $activation_status in
  # Milos may ignore TERM while waiting for the terminal indication.  Permit
  # timeout's forced-kill status only because the PDC list is checked next.
  0|124|137|143) ;;
  *) fail software_activation_transport ;;
esac
software_live=$("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=software)
[[ $(active_id <<<"$software_live") == "$software_id" ]] || fail software_not_active

# AT&T remains a boot-time RAM selection. Remove only its now-inactive cache
# copy before closing the bounded writer, leaving T-Mobile as the sole durable
# software baseline.
if compact_ids <<<"$software_live" | grep -qx "$att_id"; then
  set +e
  timeout --signal=TERM --kill-after=5 20 \
    "$qmicli" -d qrtr://0 --device-open-qmi \
      --pdc-delete-config="software,$att_id" >/dev/null 2>&1
  set -e
  software_live=$("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=software)
  ! compact_ids <<<"$software_live" | grep -qx "$att_id" || fail att_not_deleted
  [[ $(active_id <<<"$software_live") == "$software_id" ]] || fail tmo_lost_after_delete
fi
unset software_live

systemctl stop tqftpserv.service
stop_modem
  kill -TERM "$writer_pid"
  wait "$writer_pid"
  writer_pid=
  sync
  blockdev --flushbufs /dev/disk/by-partlabel/modemst1
  blockdev --flushbufs /dev/disk/by-partlabel/modemst2
systemctl start rmtfs.service tqftpserv.service
wait_modem || fail normal_modem_start_timeout
[[ $("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=platform | grep -c 'Status:      Active') == 1 ]] || fail platform_not_persistent
[[ $("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=software | grep -c 'Status:      Active') == 1 ]] || fail software_not_persistent
systemctl start ModemManager.service luma-fp6-cellular-link.service
modem_state=
for _ in $(seq 1 60); do
  modem_state=$(mmcli -m 0 --output-keyvalue 2>/dev/null |
    awk -F: '/modem.generic.state[[:space:]]*:/{gsub(/[[:space:]]/,"",$2); print $2}')
  [[ -n $modem_state ]] && break
  sleep 2
done
[[ -n $modem_state ]] || fail modemmanager_missing
[[ $modem_state != failed ]] || fail modemmanager_failed

trap - EXIT INT TERM HUP
find /run/luma-fp6-modemst-writer.log -delete
find "$writer" "$qmicli" "$software" -delete
event state=accepted platform=DSDS-LA-Milos software=Commercial-TMO identifiers_logged=0
