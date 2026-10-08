#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Activate the exact QREL 16.95.0 AT&T VoLTE software configuration in the
# normal read-only rmtfs runtime.  This gate is deliberately volatile: it does
# not persist modemst and can be evaluated before any durable carrier change.

set -Eeuo pipefail
umask 077

qmicli=/tmp/qmicli-pdc-typed
software=/tmp/mcfg_sw_att_volte_qrel1695.mbn
platform_id=00b4217c22737e3fc64ddea978a4218c4282769e
software_id=444954dcfbd6d5bf196cd8fecf6770ff3c6b34fc
qmicli_sha=1b4adcd3d949e8d0f68065ba0022bbc35fe4462717deaaeae0ec244557f76b9c
software_sha=32e7f3d9059e63323f70cf113b1ecc040dc7e9c82238a1d5bde2b21ca2eb7d6e

event() { printf 'event=luma_fp6_att_pdc_live %s\n' "$*"; }
fail() { event "state=failed reason=$1" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d' ' -f1; }
compact_id() { tr -d ':' | tr '[:upper:]' '[:lower:]'; }

cleanup() {
  local result=$?
  set +e
  systemctl start ModemManager.service luma-fp6-cellular-link.service
  find "$qmicli" "$software" -delete 2>/dev/null
  return "$result"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] || fail model
grep -qw androidboot.slot_suffix=_b /proc/cmdline || fail slot
[[ $(uname -r) == 7.1.2-luma-fp-ims1 ]] || fail kernel
[[ $(hash "$qmicli") == "$qmicli_sha" ]] || fail qmicli_hash
[[ $(hash "$software") == "$software_sha" ]] || fail software_hash
systemctl is-active --quiet rmtfs.service || fail rmtfs_inactive
[[ $(tr '\0' '\n' </proc/$(systemctl show -p MainPID --value rmtfs.service)/cmdline | paste -sd' ' -) == '/usr/bin/rmtfs -r -P -s' ]] ||
  fail rmtfs_not_read_only

platform=$($qmicli -d qrtr://0 --device-open-qmi --pdc-list-configs=platform)
[[ $(grep -c '^Configuration ' <<<"$platform") == 1 ]] || fail platform_count
grep -q 'Status:[[:space:]]*Active' <<<"$platform" || fail platform_inactive
grep -A8 -i "$(sed 's/../&:/g;s/:$//' <<<"$platform_id")" <<<"$platform" >/dev/null ||
  fail platform_identity
unset platform

systemctl stop luma-fp6-cellular-link.service ModemManager.service
$qmicli -d qrtr://0 --device-open-qmi --pdc-load-config="software,$software" >/dev/null

set +e
timeout --signal=TERM --kill-after=5 20 \
  "$qmicli" -d qrtr://0 --device-open-qmi \
  --pdc-activate-config="software,$software_id" >/dev/null 2>&1
activation_status=$?
set -e
case $activation_status in 0|124|137|143) ;; *) fail activation_transport ;; esac

configs=$($qmicli -d qrtr://0 --device-open-qmi --pdc-list-configs=software)
[[ $(grep -c '^Configuration ' <<<"$configs") -ge 1 ]] || fail software_count
active_id=$(awk '
  /^[[:space:]]*Status:[[:space:]]*Active/ { active=1; next }
  active && /^[[:space:]]*ID:/ { sub(/^[^:]*:[[:space:]]*/, ""); print; exit }
' <<<"$configs" | compact_id)
[[ $active_id == "$software_id" ]] || fail att_not_active
unset configs active_id

systemctl start ModemManager.service luma-fp6-cellular-link.service

trap - EXIT INT TERM HUP
find "$qmicli" "$software" -delete
event 'state=accepted software=VoLTE-ATT persistence=volatile identifiers_logged=0'
