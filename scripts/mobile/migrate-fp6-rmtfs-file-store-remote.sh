#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Migrate an FP6 with boot-cleared modemst partitions to upstream rmtfs's
# persistent file backend. Factory partitions are copied read-only; every
# modem write is confined to a root-only directory on Luma's userdata.

set -Eeuo pipefail
umask 077

qmicli=/usr/libexec/luma-fp6-qmicli-pdc
rmtfs=/usr/bin/rmtfs
platform=/usr/lib/firmware/luma/fp6-carrier-pdc/dsds-la-milos.mbn
tmo=/tmp/luma-rmtfs-migration-tmo.mbn
remoteproc=/sys/class/remoteproc/remoteproc1
store=/var/lib/luma/rmtfs
stage=/var/lib/luma/rmtfs.new
dropin=/etc/systemd/system/rmtfs.service.d/50-luma-file-store.conf
log=/run/luma-fp6-rmtfs-migration.log
zero_sha=e5b844cc57f57094ea4585e235f36c78c1cd222262bb89d53c94dcb4d6b3e55d
fsg_sha=a776c844c220fa278479f63b3dd0ea6ebdd12bc5267af02592dd895dd76494ab
fsc_sha=fa43239bcee7b97ca62f007cc68487560a39e19f74f3dde7486db3f98df8e471
study_sha=95c6994eb09f842b01832a098c4f0252fac3c144ee0fb772dfc5082daeae83e5
platform_id=00b4217c22737e3fc64ddea978a4218c4282769e
tmo_id=88f589369ff3a066511dd1b21ed42cf3496686a3
att_id=444954dcfbd6d5bf196cd8fecf6770ff3c6b34fc
rmtfs_pid=
modem_running=true
promoted=false

event() { printf 'event=luma_fp6_rmtfs_file_store %s\n' "$*"; }
fail() { event "state=failed reason=$1 identifiers_logged=0" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }
compact_ids() {
  sed -n 's/^[[:space:]]*ID:[[:space:]]*//p' | tr -d ':' |
    tr '[:upper:]' '[:lower:]'
}
selected_id() {
  awk '
    /^[[:space:]]*Status:[[:space:]]*(Pending|Active)/ { selected=1; next }
    selected && /^[[:space:]]*ID:/ {
      sub(/^[^:]*:[[:space:]]*/, "")
      print
      exit
    }
  ' | tr -d ':' | tr '[:upper:]' '[:lower:]'
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
  for _ in $(seq 1 90); do
    "$qmicli" -d qrtr://0 --device-open-qmi --dms-get-operating-mode \
      >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}
stop_modem() {
  [[ $(cat "$remoteproc/name") == modem ]] || fail remoteproc_identity
  if [[ $(cat "$remoteproc/state") == running ]]; then
    printf stop >"$remoteproc/state"
  fi
  for _ in $(seq 1 50); do
    [[ $(cat "$remoteproc/state") == offline ]] && { modem_running=false; return 0; }
    sleep 0.2
  done
  fail modem_stop
}
start_modem() {
  [[ $(cat "$remoteproc/name") == modem ]] || fail remoteproc_identity
  case $(cat "$remoteproc/state") in
    offline) printf start >"$remoteproc/state" ;;
    running) ;;
    *) fail modem_start_state ;;
  esac
  modem_running=true
  wait_modem || fail modem_start_timeout
}
stop_private_rmtfs() {
  if [[ -n $rmtfs_pid ]] && kill -0 "$rmtfs_pid" 2>/dev/null; then
    kill -TERM "$rmtfs_pid"
    wait "$rmtfs_pid"
  fi
  rmtfs_pid=
}
start_private_rmtfs() {
  : >"$log"
  chmod 0600 "$log"
  "$rmtfs" -o "$stage" -s >"$log" 2>&1 &
  rmtfs_pid=$!
  sleep 1
  kill -0 "$rmtfs_pid" 2>/dev/null || fail private_rmtfs_start
}
bounded_activate() {
  local type=$1 id=$2
  set +e
  timeout --signal=TERM --kill-after=5 20 \
    "$qmicli" -d qrtr://0 --device-open-qmi \
      --pdc-activate-config="$type,$id" >/dev/null 2>&1
  set -e
}
cleanup() {
  local status=$?
  set +e
  if [[ $modem_running == true ]]; then
    printf stop >"$remoteproc/state" 2>/dev/null
    sleep 1
    modem_running=false
  fi
  stop_private_rmtfs
  systemctl stop tqftpserv.service 2>/dev/null
  if [[ $promoted == false ]]; then
    rm -rf --one-file-system "$stage"
  fi
  systemctl daemon-reload 2>/dev/null
  systemctl start rmtfs.service tqftpserv.service 2>/dev/null
  if [[ $(cat "$remoteproc/state" 2>/dev/null) == offline ]]; then
    printf start >"$remoteproc/state" 2>/dev/null
  fi
  systemctl start --no-block luma-fp6-carrier-pdc.service \
    luma-fp6-primary-gw-session.service ModemManager.service \
    luma-fp6-cellular-link.service luma-fp6-imsd.service 2>/dev/null
  rm -f "$log" "$tmo"
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] || fail model
grep -qw androidboot.slot_suffix=_b /proc/cmdline || fail slot
[[ $(uname -r) == 7.1.2-luma-fp-ims1 ]] || fail kernel
[[ $(hash "$qmicli") == 1b4adcd3d949e8d0f68065ba0022bbc35fe4462717deaaeae0ec244557f76b9c ]] || fail qmicli_hash
[[ $(hash "$rmtfs") == 31d266711b5fae801162cd31bd2d14331515a6005aa6e5128a2f4bdbcd3bb5d5 ]] || fail rmtfs_hash
[[ $(hash "$platform") == 281cf305a5bcd98b709a0446f9733d1859cb423a791b2af17ab9776ffeaf0400 ]] || fail platform_hash
[[ $(hash "$tmo") == 817e5f33a18a176a14859184de88c60921318864dea994a845133bc841e3edda ]] || fail tmo_hash
[[ $(hash /dev/disk/by-partlabel/modemst1) == "$zero_sha" ]] || fail modemst1_not_blank
[[ $(hash /dev/disk/by-partlabel/modemst2) == "$zero_sha" ]] || fail modemst2_not_blank
[[ $(hash /dev/disk/by-partlabel/fsg) == "$fsg_sha" ]] || fail fsg_hash
[[ $(hash /dev/disk/by-partlabel/fsc) == "$fsc_sha" ]] || fail fsc_hash
[[ $(hash /dev/disk/by-partlabel/study) == "$study_sha" ]] || fail study_hash
[[ ! -e $store && ! -e $stage ]] || fail store_exists
[[ ! -e $dropin ]] || fail dropin_exists

systemctl stop luma-fp6-imsd.service luma-fp6-cellular-link.service \
  ModemManager.service luma-fp6-primary-gw-session.service \
  luma-fp6-carrier-pdc.service tqftpserv.service rmtfs.service
stop_modem

install -d -m 0700 "$stage"
dd if=/dev/disk/by-partlabel/modemst1 of="$stage/modem_fs1" bs=1M status=none
dd if=/dev/disk/by-partlabel/modemst2 of="$stage/modem_fs2" bs=1M status=none
dd if=/dev/disk/by-partlabel/fsc of="$stage/modem_fsc" bs=128K status=none
dd if=/dev/disk/by-partlabel/fsg of="$stage/modem_fsg" bs=1M status=none
dd if=/dev/disk/by-partlabel/study of="$stage/modem_study" bs=1M status=none
: >"$stage/modem_tunning"
chmod 0600 "$stage"/*
restorecon -RF "$stage" 2>/dev/null || true

start_private_rmtfs
systemctl start tqftpserv.service
start_modem

platform_live=$("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=platform)
platform_count=$(grep -c '^Configuration ' <<<"$platform_live" || true)
[[ $platform_count -le 1 ]] || fail platform_count
if [[ $platform_count -eq 0 ]]; then
  "$qmicli" -d qrtr://0 --device-open-qmi \
    --pdc-load-config="platform,$platform" >/dev/null || fail platform_load
fi
bounded_activate platform "$platform_id"
platform_live=$("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=platform)
[[ $(selected_id <<<"$platform_live") == "$platform_id" ]] || fail platform_acceptance

software_live=$("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=software)
mapfile -t software_ids < <(compact_ids <<<"$software_live")
for id in "${software_ids[@]}"; do
  [[ $id == "$att_id" || $id == "$tmo_id" ]] || fail unexpected_software
done
if ! printf '%s\n' "${software_ids[@]}" | grep -qx "$tmo_id"; then
  "$qmicli" -d qrtr://0 --device-open-qmi \
    --pdc-load-config="software,$tmo" >/dev/null || fail tmo_load
fi
bounded_activate software "$tmo_id"
software_live=$("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=software)
[[ $(selected_id <<<"$software_live") == "$tmo_id" ]] || fail tmo_acceptance
if compact_ids <<<"$software_live" | grep -qx "$att_id"; then
  set +e
  timeout --signal=TERM --kill-after=5 20 \
    "$qmicli" -d qrtr://0 --device-open-qmi \
      --pdc-delete-config="software,$att_id" >/dev/null 2>&1
  set -e
  software_live=$("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=software)
  ! compact_ids <<<"$software_live" | grep -qx "$att_id" || fail att_not_deleted
fi
unset platform_live platform_count software_live software_ids id

systemctl stop tqftpserv.service
stop_modem
stop_private_rmtfs
sync -f "$stage"

# Reopen the same files and require the modem to report both configurations as
# Active before making the service override persistent.
start_private_rmtfs
systemctl start tqftpserv.service
start_modem
[[ $(active_id < <("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=platform)) == "$platform_id" ]] || fail platform_reopen
[[ $(active_id < <("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=software)) == "$tmo_id" ]] || fail tmo_reopen
systemctl stop tqftpserv.service
stop_modem
stop_private_rmtfs
sync -f "$stage"

mv "$stage" "$store"
promoted=true
install -d -m 0755 "$(dirname "$dropin")"
cat >"$dropin" <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/rmtfs -o /var/lib/luma/rmtfs -s
EOF
chmod 0644 "$dropin"
restorecon -RF /var/lib/luma "$dropin" 2>/dev/null || true
systemctl daemon-reload
systemctl start rmtfs.service tqftpserv.service
start_modem
[[ $(active_id < <("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=platform)) == "$platform_id" ]] || fail platform_final
[[ $(active_id < <("$qmicli" -d qrtr://0 --device-open-qmi --pdc-list-configs=software)) == "$tmo_id" ]] || fail tmo_final

systemctl start luma-fp6-carrier-pdc.service
systemctl start luma-fp6-primary-gw-session.service
systemctl start ModemManager.service luma-fp6-cellular-link.service luma-fp6-imsd.service

trap - EXIT INT TERM HUP
rm -f "$log" "$tmo"
event 'state=accepted backend=file platform=DSDS-LA-Milos software=Commercial-TMO physical_partitions_written=0 identifiers_logged=0'
