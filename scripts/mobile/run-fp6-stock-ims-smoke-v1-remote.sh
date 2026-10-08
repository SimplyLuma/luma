#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Start the unmodified QREL 16.95.0 Qualcomm IMS daemon in a disposable,
# headless Android namespace.  The gate is intentionally observational: it
# exposes Binder and QRTR, but no GPU, TEE, partition, eUICC, or block devices.
# Luma's IMS services are restored with a fresh registration on every exit.

set -Eeuo pipefail
umask 077

self=$(readlink -f "$0")
property_shim=${LUMA_STOCK_IMS_PROPERTY_SHIM:-/tmp/luma-stock-ims/libluma-ims-properties.so}
manager_shim=${LUMA_STOCK_IMS_MANAGER_SHIM:-/tmp/luma-stock-ims/libluma-ims-servicemanager-access.so}
hwmanager_shim=${LUMA_STOCK_IMS_HWMANAGER_SHIM:-/tmp/luma-stock-ims/libluma-ims-hwservicemanager-access.so}
socket_launcher=${LUMA_STOCK_IMS_SOCKET_LAUNCHER:-/tmp/luma-stock-ims/luma-android-ims-socket-launcher}
hold_seconds=${LUMA_STOCK_IMS_HOLD_SECONDS:-20}

expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2-luma-fp-ims1
expected_imsd_sha=0c87d60c7627e693e0e92ec182a83277e697bd86f194ebe2f8194c26f41979bb
expected_servicemanager_sha=626eaac3336fcf91e3510aaf920d7fa7882733ba1648ab9a432b0532a1570c70
expected_service_sha=63d064e0ae4796608374a3ac757a9c05a5c8766a9a802cf7e79e784dda26cafb
expected_hwservicemanager_sha=c9b8ced988965bdb53dbe2e98b3f3d6b30da09c5376606c9353377c820e5ba1f
expected_lshal_sha=3e6dcac9e25365120c4334d5680a77554c19ea70c6b1a0db6b121c70805c1854
expected_imsdaemon_sha=9662a56d18083827236941f33c58b0adf27a64309dc6133de11565d60ed61f1d
expected_property_shim_sha=e76c2c4eeb5a37d406038247b1cf818ece97b637314f84d9a62bbf511c8b4751
expected_manager_shim_sha=611d771ee5afb268c32b056bf722d7dabc5433628ff991abc8d0ed3b137abe39
expected_hwmanager_shim_sha=d3cd4616f8906b6093a2e210042b561760a807b4f025b1ba7870d0e154f57fbf
expected_socat_sha=19c49d4cbcc1e410cda71df3f41d225b07184691ebbe7d231753f404ac0470aa
expected_socket_launcher_sha=929f94535c0568e48b346d522f584dbc30df7e5088e200ffd210642a57eab0eb
expected_dcm_bridge_sha=cc0656f0a31faf1456c296699a577dbf2a47eef6a39b0514c7fa9fa66e26d4ee
dcm_bridge=/tmp/luma-fp6-imsdcm-bridge-v10

stock_system=/var/tmp/luma-stock-qrel1695-system/system
stock_vendor=/var/tmp/luma-stock-qrel1695-vendor
stock_system_ext=/var/tmp/luma-stock-qrel1695-system_ext
stock_product=/var/tmp/luma-stock-qrel1695-product
stock_runtime=/var/tmp/luma-stock-qrel1695-runtime-apex

fail() {
  printf 'event=fp6_stock_ims_smoke_v1_failed reason=%s\n' "$1" >&2
  exit 1
}

critical_faults() {
  grep -Ei 'hangcheck|GMU.*(timeout|fault)|GPU.*(timeout|fault|hang)|qsee.*(fault|timeout)|TEE.*(fault|timeout)|BUG:|kernel panic|Oops:|Call trace:' || true
}

sanitize_runtime() {
  sed -E \
    -e 's#(sip:|sips:|tel:)[^ >;]+#<identity-redacted>#g' \
    -e 's/[0-9a-fA-F]{0,4}(:[0-9a-fA-F]{0,4}){2,}/<ipv6-redacted>/g' \
    -e 's/([0-9]{1,3}\.){3}[0-9]{1,3}/<ipv4-redacted>/g' \
    -e 's/[0-9]{7,}/<number-redacted>/g'
}

process_diagnostics() {
  local pid=$1 task
  [[ -d /proc/$pid ]] || return 0
  printf '%s\n' '--- stock IMS process diagnostics ---' >&2
  sed -n '/^Name:/p;/^State:/p;/^Uid:/p;/^Gid:/p;/^Threads:/p' \
    "/proc/$pid/status" >&2 || true
  for task in "/proc/$pid"/task/*; do
    printf 'tid=%s wchan=' "${task##*/}" >&2
    cat "$task/wchan" >&2 2>/dev/null || true
    printf '\n' >&2
  done
  if [[ -x /usr/bin/eu-stack ]]; then
    printf '%s\n' '--- stock IMS symbol stack ---' >&2
    /usr/bin/eu-stack -p "$pid" -n 32 -m >&2 || true
  fi
  printf '%s\n' '--- stock IMS file-descriptor classes ---' >&2
  find "/proc/$pid/fd" -maxdepth 1 -type l -printf '%l\n' 2>/dev/null |
    sed -E 's/socket:\[[0-9]+\]/socket:[redacted]/g; s/anon_inode:\[[^]]+\]/anon_inode:[class]/g' |
    sort | uniq -c >&2 || true
}

bind_ro() {
  local source=$1 target=$2
  mount --bind "$source" "$target"
  mount -o remount,bind,ro "$target"
}

bind_device() {
  local source=$1 target=$2
  [[ -e $source ]] || fail "device_missing_$(basename "$source")"
  : >"$target"
  mount --bind "$source" "$target"
}

if [[ ${1:-} != --inside ]]; then
  [[ $(id -u) -eq 0 ]] || fail not_root
  exec unshare --mount "$self" --inside
fi

[[ $(id -u) -eq 0 ]] || fail not_root
[[ $hold_seconds =~ ^[0-9]+$ && $hold_seconds -le 90 ]] || fail hold_seconds
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == "$expected_model" ]] || fail model
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(sha256sum /usr/sbin/imsd | cut -d' ' -f1) == "$expected_imsd_sha" ]] || fail imsd_hash
[[ $(sha256sum "$stock_system/bin/servicemanager" | cut -d' ' -f1) == "$expected_servicemanager_sha" ]] || fail servicemanager_hash
[[ $(sha256sum "$stock_system/bin/service" | cut -d' ' -f1) == "$expected_service_sha" ]] || fail service_hash
[[ $(sha256sum "$stock_system_ext/bin/hwservicemanager" | cut -d' ' -f1) == "$expected_hwservicemanager_sha" ]] || fail hwservicemanager_hash
[[ $(sha256sum "$stock_system/bin/lshal" | cut -d' ' -f1) == "$expected_lshal_sha" ]] || fail lshal_hash
[[ $(sha256sum "$stock_vendor/bin/imsdaemon" | cut -d' ' -f1) == "$expected_imsdaemon_sha" ]] || fail imsdaemon_hash
[[ -f $property_shim && ! -L $property_shim ]] || fail property_shim_identity
[[ -f $manager_shim && ! -L $manager_shim ]] || fail manager_shim_identity
[[ -f $hwmanager_shim && ! -L $hwmanager_shim ]] || fail hwmanager_shim_identity
[[ $(sha256sum "$property_shim" | cut -d' ' -f1) == "$expected_property_shim_sha" ]] || fail property_shim_hash
[[ $(sha256sum "$manager_shim" | cut -d' ' -f1) == "$expected_manager_shim_sha" ]] || fail manager_shim_hash
[[ $(sha256sum "$hwmanager_shim" | cut -d' ' -f1) == "$expected_hwmanager_shim_sha" ]] || fail hwmanager_shim_hash
[[ $(sha256sum /usr/bin/socat | cut -d' ' -f1) == "$expected_socat_sha" ]] || fail socat_hash
[[ -x $socket_launcher && ! -L $socket_launcher ]] || fail socket_launcher_identity
[[ $(sha256sum "$socket_launcher" | cut -d' ' -f1) == "$expected_socket_launcher_sha" ]] || fail socket_launcher_hash
[[ -f $dcm_bridge && ! -L $dcm_bridge ]] || fail dcm_bridge_identity
[[ $(sha256sum "$dcm_bridge" | cut -d' ' -f1) == "$expected_dcm_bridge_sha" ]] || fail dcm_bridge_hash
! pgrep -x servicemanager >/dev/null || fail servicemanager_already_running
! pgrep -x imsdaemon >/dev/null || fail imsdaemon_already_running
systemctl is-active --quiet luma-fp6-imsd.service || fail luma_imsd_not_active
systemctl is-active --quiet luma-imsdcm-live.service || fail luma_imsdcm_not_active
systemctl is-active --quiet luma-fp6-cellular-link.service || fail cellular_link_not_active

marker="luma-stock-ims-$(cat /proc/sys/kernel/random/uuid)"
printf '<6>%s\n' "$marker" >/dev/kmsg
started_cutover=$(date +%s)

root=$(mktemp -d /var/tmp/luma-stock-ims-root.XXXXXX)
chmod 0755 "$root"
mounted=false
manager_pid=
hwmanager_pid=
imsdaemon_pid=
log_sink_pid=
luma_stopped=false
dropin=/run/systemd/system/luma-fp6-imsd.service.d/90-luma-stock-smoke.conf

restore_luma() {
  local restore_status=0
  set +e
  if [[ $luma_stopped == true ]]; then
    if ! mmcli -L 2>/dev/null | grep -q '/Modem/'; then
      systemctl restart ModemManager.service
      for _ in $(seq 1 120); do
        mmcli -L 2>/dev/null | grep -q '/Modem/' && break
        sleep 0.25
      done
    fi
    mmcli -L 2>/dev/null | grep -q '/Modem/' || restore_status=1
    systemctl restart luma-fp6-cellular-link.service
    for _ in $(seq 1 160); do
      systemctl is-active --quiet luma-fp6-cellular-link.service && break
      sleep 0.25
    done
    if ! systemctl is-active --quiet luma-imsdcm-live.service; then
      systemd-run --quiet --unit=luma-imsdcm-live.service --property=Type=exec \
        --property=User=root --property=Restart=no "$dcm_bridge" 3600 ||
        restore_status=1
      for _ in $(seq 1 40); do
        systemctl is-active --quiet luma-imsdcm-live.service && break
        sleep 0.1
      done
    fi
    systemctl is-active --quiet luma-imsdcm-live.service || restore_status=1
    install -d -m 0755 "${dropin%/*}"
    printf '[Service]\nEnvironment=RESUME=0\n' >"$dropin"
    systemctl daemon-reload
    systemctl start luma-imsdcm-live.service
    systemctl restart luma-fp6-imsd.service
    for _ in $(seq 1 160); do
      journalctl -u luma-fp6-imsd.service --since "@$started_cutover" --no-pager 2>/dev/null |
        grep -Fq 'REGISTERED (fresh)' && break
      sleep 0.25
    done
    journalctl -u luma-fp6-imsd.service --since "@$started_cutover" --no-pager 2>/dev/null |
      grep -Fq 'REGISTERED (fresh)' || restore_status=1
    rm -f "$dropin"
    rmdir "${dropin%/*}" 2>/dev/null || true
    systemctl daemon-reload
  fi
  return "$restore_status"
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM HUP
  set +e
  [[ -z $imsdaemon_pid ]] || kill -TERM "$imsdaemon_pid" 2>/dev/null || true
  [[ -z $imsdaemon_pid ]] || wait "$imsdaemon_pid" 2>/dev/null || true
  [[ -z $hwmanager_pid ]] || kill -TERM "$hwmanager_pid" 2>/dev/null || true
  [[ -z $hwmanager_pid ]] || wait "$hwmanager_pid" 2>/dev/null || true
  [[ -z $manager_pid ]] || kill -TERM "$manager_pid" 2>/dev/null || true
  [[ -z $manager_pid ]] || wait "$manager_pid" 2>/dev/null || true
  [[ -z $log_sink_pid ]] || kill -TERM "$log_sink_pid" 2>/dev/null || true
  [[ -z $log_sink_pid ]] || wait "$log_sink_pid" 2>/dev/null || true
  [[ $mounted != true ]] || umount -R "$root" 2>/dev/null || true
  [[ ! -d $root ]] || find "$root" -depth -delete 2>/dev/null || true
  if ! restore_luma; then
    printf 'event=fp6_stock_ims_restore_failed\n' >&2
    status=1
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM HUP

mount --make-rprivate /
mount -t tmpfs -o mode=0755,size=96m tmpfs "$root"
mounted=true
mkdir -p "$root"/{system,vendor,system_ext,product,apex/com.android.runtime,dev,proc,sys,data,linkerconfig,luma-adapter}
chmod 0755 "$root"/{system,vendor,system_ext,product,apex,apex/com.android.runtime,dev,proc,sys,data,linkerconfig,luma-adapter}
bind_ro "$stock_system" "$root/system"
bind_ro "$stock_vendor" "$root/vendor"
bind_ro "$stock_system_ext" "$root/system_ext"
bind_ro "$stock_product" "$root/product"
bind_ro "$stock_runtime" "$root/apex/com.android.runtime"
touch "$root/luma-adapter/libluma-ims-properties.so" "$root/luma-adapter/libluma-ims-servicemanager-access.so" \
  "$root/luma-adapter/libluma-ims-hwservicemanager-access.so"
bind_ro "$property_shim" "$root/luma-adapter/libluma-ims-properties.so"
bind_ro "$manager_shim" "$root/luma-adapter/libluma-ims-servicemanager-access.so"
bind_ro "$hwmanager_shim" "$root/luma-adapter/libluma-ims-hwservicemanager-access.so"

mount -t tmpfs -o mode=0755,size=8m tmpfs "$root/dev"
mkdir -p "$root/dev/socket"
for device in null zero random urandom kmsg; do
  bind_device "/dev/$device" "$root/dev/$device"
done
for device in binder hwbinder vndbinder; do
  binder_major=$((16#$(stat -c %t "/dev/$device")))
  binder_minor=$((16#$(stat -c %T "/dev/$device")))
  mknod "$root/dev/$device" c "$binder_major" "$binder_minor"
  chown 1001:1001 "$root/dev/$device"
  chmod 0660 "$root/dev/$device"
done
mount -t proc -o nosuid,nodev,noexec proc "$root/proc"
mount --bind /sys "$root/sys"
mount -o remount,bind,ro "$root/sys"
mount -t tmpfs -o mode=0711,size=32m tmpfs "$root/data"
install -d -m 0770 -o 1001 -g 1001 "$root/data/vendor/imslogs"
mount -t tmpfs -o mode=0755,size=4m tmpfs "$root/linkerconfig"

: >"$root/data/logdw.bin"
/usr/bin/socat -u UNIX-RECV:"$root/dev/socket/logdw",mode=0666 - >"$root/data/logdw.bin" &
log_sink_pid=$!
for _ in $(seq 1 20); do
  [[ -S $root/dev/socket/logdw ]] && break
  sleep 0.05
done
[[ -S $root/dev/socket/logdw ]] || fail log_sink_not_ready

ld_library_path=/luma-adapter:/apex/com.android.runtime/lib64/bionic:/vendor/lib64:/vendor/lib64/hw:/system/lib64:/system_ext/lib64:/product/lib64
env -i LD_LIBRARY_PATH="$ld_library_path" /usr/sbin/chroot "$root" /system/bin/sh -c \
  'export LD_PRELOAD=/luma-adapter/libluma-ims-servicemanager-access.so:/luma-adapter/libluma-ims-properties.so; exec /system/bin/servicemanager' \
  >"$root/data/servicemanager.log" 2>&1 &
manager_pid=$!

manager_ready=false
for _ in $(seq 1 30); do
  kill -0 "$manager_pid" 2>/dev/null || fail servicemanager_exited
  if timeout 2 env -i LD_LIBRARY_PATH="$ld_library_path" /usr/sbin/chroot "$root" /system/bin/sh -c \
    'export LD_PRELOAD=/luma-adapter/libluma-ims-properties.so; exec /system/bin/service list' \
    >"$root/data/service-list.txt" 2>"$root/data/service-list.err"; then
    manager_ready=true
    break
  fi
  sleep 0.1
done
if [[ $manager_ready != true ]]; then
  sed -n '1,120p' "$root/data/servicemanager.log" >&2 || true
  sed -n '1,80p' "$root/data/service-list.err" >&2 || true
  fail servicemanager_not_ready
fi

env -i LD_LIBRARY_PATH="$ld_library_path" /usr/sbin/chroot "$root" /system/bin/sh -c \
  'export LD_PRELOAD=/luma-adapter/libluma-ims-hwservicemanager-access.so:/luma-adapter/libluma-ims-properties.so; exec /system_ext/bin/hwservicemanager' \
  >"$root/data/hwservicemanager.log" 2>&1 &
hwmanager_pid=$!
for _ in $(seq 1 30); do
  kill -0 "$hwmanager_pid" 2>/dev/null || break
  sleep 0.1
done
if ! kill -0 "$hwmanager_pid" 2>/dev/null; then
  sed -n '1,120p' "$root/data/hwservicemanager.log" | sanitize_runtime >&2 || true
  fail hwservicemanager_exited
fi
hidl_probe_status=0
timeout 4 env -i LD_LIBRARY_PATH="$ld_library_path" /usr/sbin/chroot "$root" /system/bin/sh -c \
  'export LD_PRELOAD=/luma-adapter/libluma-ims-properties.so; exec /system/bin/lshal --neat --types b' \
  >"$root/data/lshal.txt" 2>"$root/data/lshal.err" || hidl_probe_status=$?
grep -Fq 'android.hidl.manager@1.0::IServiceManager/default' "$root/data/lshal.txt" || {
  printf 'event=luma_hidl_client_probe exit=%s manager_listed=false\n' "$hidl_probe_status" >&2
  sed -n '1,100p' "$root/data/lshal.err" | sanitize_runtime >&2 || true
  sed -n '1,160p' "$root/data/hwservicemanager.log" | sanitize_runtime >&2 || true
  dmesg | sed -n "/$marker/,\$p" | grep -Ei 'binder|hwbinder|transaction' |
    sanitize_runtime | tail -n 80 >&2 || true
  fail hidl_client_probe
}
kill -0 "$hwmanager_pid" 2>/dev/null || fail hwservicemanager_probe_exit
printf 'event=stock_ims_servicemanagers_ready aidl=true hidl=true hidl_client_probe=passed probe_exit=%s gpu=false tee=false persistent_data=false\n' "$hidl_probe_status"

# Only the two processes which compete with the stock daemon are paused.  The
# cellular bearer and ModemManager remain online throughout the bounded gate.
systemctl stop luma-fp6-imsd.service luma-imsdcm-live.service
luma_stopped=true
! systemctl is-active --quiet luma-fp6-imsd.service || fail luma_imsd_stop
! systemctl is-active --quiet luma-imsdcm-live.service || fail luma_imsdcm_stop
systemctl is-active --quiet luma-fp6-cellular-link.service || fail cellular_link_lost

"$socket_launcher" "$root" </dev/null >"$root/data/imsdaemon.log" 2>&1 &
imsdaemon_pid=$!
for _ in $(seq 1 30); do
  [[ -S $root/dev/socket/ims_datad ]] && break
  sleep 0.05
done
[[ -S $root/dev/socket/ims_datad ]] || fail ims_datad_socket_missing
chown 1000:1001 "$root/dev/socket/ims_datad"
chmod 0660 "$root/dev/socket/ims_datad"

factory_ready=false
for _ in $(seq 1 120); do
  kill -0 "$imsdaemon_pid" 2>/dev/null || break
  timeout 2 env -i LD_LIBRARY_PATH="$ld_library_path" /usr/sbin/chroot "$root" /system/bin/sh -c \
    'export LD_PRELOAD=/luma-adapter/libluma-ims-properties.so; exec /system/bin/service list' \
    >"$root/data/service-list-ims.txt" 2>/dev/null || true
  if grep -Fq 'vendor.qti.ims.factoryaidlservice.IImsFactory/default' "$root/data/service-list-ims.txt"; then
    factory_ready=true
    break
  fi
  sleep 0.1
done

sleep "$hold_seconds"
process_alive=false
kill -0 "$imsdaemon_pid" 2>/dev/null && process_alive=true
aidl_manager_alive=false
kill -0 "$manager_pid" 2>/dev/null && aidl_manager_alive=true
hidl_manager_alive=false
kill -0 "$hwmanager_pid" 2>/dev/null && hidl_manager_alive=true
imsdaemon_exit=running
if [[ $process_alive != true ]]; then
  set +e
  wait "$imsdaemon_pid"
  imsdaemon_exit=$?
  set -e
  imsdaemon_pid=
fi
log_text=$(strings -a "$root/data/logdw.bin" 2>/dev/null || true)
direct_text=$(cat "$root/data/imsdaemon.log" 2>/dev/null || true)
local_log_text=$(find "$root/data/vendor/imslogs" -type f -maxdepth 2 -exec strings -a {} + 2>/dev/null || true)
local_log_files=$(find "$root/data/vendor/imslogs" -type f -maxdepth 2 2>/dev/null | wc -l)
local_log_bytes=$(find "$root/data/vendor/imslogs" -type f -maxdepth 2 -printf '%s\n' 2>/dev/null | awk '{sum += $1} END {print sum + 0}')
factory_log_count=$(grep -Eic 'register.*ims.*factory|imsfactory.*register|ImsFactoryImpl start' <<<"$log_text" || true)
dcm_success_count=$(grep -Eic 'qmi_dcm_register_service|register dcm service' <<<"$log_text" || true)
qmi_error_count=$(grep -Eic 'qmi.*(fail|error)|failed to register dcm' <<<"$log_text" || true)
socket_error_count=$(grep -Eic 'ims_datad.*(fail|error)|control socket.*(fail|error)' <<<"$log_text $direct_text" || true)
hidl_register_status=$(grep -oE 'registerAsService\(\)=-[0-9]+' <<<"$direct_text" | head -n 1 | cut -d= -f2 || true)
[[ $hidl_register_status =~ ^-[0-9]+$ ]] || hidl_register_status=none
printf 'event=stock_ims_observation process_alive=%s exit=%s aidl_manager_alive=%s hidl_manager_alive=%s factory_registered=%s factory_log_events=%s dcm_events=%s qmi_errors=%s socket_errors=%s\n' \
  "$process_alive" "$imsdaemon_exit" "$aidl_manager_alive" "$hidl_manager_alive" "$factory_ready" "$factory_log_count" "$dcm_success_count" "$qmi_error_count" "$socket_error_count"
printf 'event=stock_ims_hidl_registration status=%s\n' "$hidl_register_status"
printf 'event=stock_ims_local_logs files=%s bytes=%s\n' "$local_log_files" "$local_log_bytes"

fault_window=$(dmesg | sed -n "/$marker/,\$p")
[[ -z $(critical_faults <<<"$fault_window") ]] || fail critical_fault
if [[ $process_alive != true ]]; then
  printf '%s\n' '--- focused stock IMS initialization sequence ---' >&2
  {
    printf '%s\n' "$direct_text"
    printf '%s\n' "$local_log_text"
  } | grep -Ei 'ImsServiceMain|ImsMain:|ImsFactoryImpl start|ImsServiceInit|Couldn.t Initialise|Loading .*service|Started ===|initializeServer|Server fd|socketFeature|Error|failed|fatal|exit' |
    sanitize_runtime | tail -n 120 >&2 || true
  printf '%s\n' '--- final stock IMS local records ---' >&2
  printf '%s\n' "$local_log_text" | sanitize_runtime | tail -n 100 >&2 || true
  printf '%s\n' '--- final stock IMS decoded records ---' >&2
  printf '%s\n' "$log_text" | sanitize_runtime | tail -n 160 >&2 || true
  printf '%s\n' '--- final stock service-manager records ---' >&2
  {
    cat "$root/data/servicemanager.log" 2>/dev/null || true
    cat "$root/data/hwservicemanager.log" 2>/dev/null || true
  } | sanitize_runtime | tail -n 100 >&2 || true
  printf '%s\n' '--- focused stock HIDL registry records ---' >&2
  printf '%s\n' "$log_text" |
    grep -Ei 'hwservicemanager|No match for interface|Missing permissions|not in manifest|transport|add IBase|add IService|register.*service' |
    sanitize_runtime | tail -n 120 >&2 || true
  fail imsdaemon_exited
fi
if [[ $factory_ready != true ]]; then
  process_diagnostics "$imsdaemon_pid"
  printf '%s\n' "--- focused stock IMS wait sequence log_bytes=$(stat -c %s "$root/data/logdw.bin" 2>/dev/null || echo 0) ---" >&2
  {
    printf '%s\n' "$direct_text"
    printf '%s\n' "$log_text"
    printf '%s\n' "$local_log_text"
  } | grep -Ei 'ImsServiceMain|ImsMain:|ImsFactoryImpl start|ImsServiceInit|Couldn.t Initialise|Loading .*service|Started ===|initializeServer|Server fd|socketFeature|Error|failed|fatal|exit' |
    sanitize_runtime | tail -n 120 >&2 || true
  fail factory_not_registered
fi

printf 'event=fp6_stock_ims_smoke_v1_pass stock_binary=exact-qrel-16.95.0 writes=tmpfs-only partitions=untouched\n'
