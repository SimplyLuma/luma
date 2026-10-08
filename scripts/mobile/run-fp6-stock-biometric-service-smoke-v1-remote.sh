#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Run the unmodified QREL 16.95.0 Android service manager in a disposable,
# headless mount/PID namespace.  The initial "manager" gate never starts a
# biometric service and never exposes DRM/GPU devices to the namespace.

set -Eeuo pipefail
umask 077

mode=${1:-manager}
self=$(readlink -f "$0")
hold_seconds=${LUMA_STOCK_HOLD_SECONDS:-0}
challenge_probe=${LUMA_STOCK_CHALLENGE_PROBE:-}
challenge_probe_sha=${LUMA_STOCK_CHALLENGE_PROBE_SHA256:-}
enrollment_client=${LUMA_STOCK_ENROLLMENT_CLIENT:-}
enrollment_client_sha=${LUMA_STOCK_ENROLLMENT_CLIENT_SHA256:-}
persist_fingerprint=${LUMA_STOCK_PERSIST_FINGERPRINT:-0}
biometric_client_mode=${LUMA_STOCK_BIOMETRIC_CLIENT_MODE:-enroll}

expected_model='The Fairphone (Gen. 6)'
# Both kernels are accepted.  Every hash below covers stock Android userspace,
# which does not change with the kernel; the kernel-dependent hashes (boot
# image, kernel config, TEE and sensor modules) are checked by
# luma-fp6-fingerprint-backend, which already selects them per release.
expected_kernels=(7.1.2-luma-fp-cma1 7.1.2-luma-fp-ims1)
expected_servicemanager_sha=626eaac3336fcf91e3510aaf920d7fa7882733ba1648ab9a432b0532a1570c70
expected_service_sha=63d064e0ae4796608374a3ac757a9c05a5c8766a9a802cf7e79e784dda26cafb
expected_compat_servicemanager_sha=b0bbad22855c01859f02ba3c3026c31e5421f9be8b51894774dd7631167c5061
expected_compat_service_sha=e10206a26a1f9798e30091e933edd89da4231650fa4ac9598beb0efe3cf3595b
expected_gatekeeper_sha=61b2d6643ede5a859d6798ffd344b1f1ee8314757f113cd518a7bd550383b6af
expected_keymint_sha=f86da299ba4b7cc2f1a75341af14aba46272f5dc722dd80acce5b53305497d49
expected_fingerprint_service_sha=42cd628419266d75f5975e7c8a329264f30df76cd5278a96f8e00ad37478bbd2
expected_fingerprint_hal_sha=ae08b39c8c78b40a769795684afc7d342c19acf8c1b443fdc56be47b5ca8bedd
expected_mink_adapter_sha=836275fa27897614d7cfafd0f51a946ecf3a48610b8d964c15cafe6d3f4f745c
expected_dmabuf_shim_sha=0e0cb66562f6c1c0afbf969f7abb3c49d0c12c13b1704deb6fb4b07a3eee135f
expected_fdsan_compat_sha=0a4481adc907cdbba76fa208501e3bd48292d9b2f4aca6d0a129944054ae169a
expected_property_shim_sha=136cf789f167462e32e946c707a7b0e5abe1cfde840db26fb923c15e7888237e
expected_socat_sha=19c49d4cbcc1e410cda71df3f41d225b07184691ebbe7d231753f404ac0470aa
expected_listener_daemon_sha=${LUMA_FP6_LISTENER_DAEMON_SHA256:-1b59b3fbda0297b0338c18bf952c2d81c77a3ae3db56351d33cb028e30404c04}
expected_manager_access_shim_sha=bf49e5e886fcdcdcfea5665a9809d11dfef6c9c718b49da1947a2c94ea238f54

stock_system=${LUMA_STOCK_SYSTEM_ROOT:-/var/tmp/luma-stock-qrel1695-system/system}
stock_vendor=${LUMA_STOCK_VENDOR_ROOT:-/var/tmp/luma-stock-qrel1695-vendor}
stock_system_ext=${LUMA_STOCK_SYSTEM_EXT_ROOT:-/var/tmp/luma-stock-qrel1695-system_ext}
stock_product=${LUMA_STOCK_PRODUCT_ROOT:-/var/tmp/luma-stock-qrel1695-product}
stock_runtime=${LUMA_STOCK_RUNTIME_ROOT:-/var/tmp/luma-stock-qrel1695-runtime-apex}
compat_system=${LUMA_STOCK_COMPAT_SYSTEM_ROOT:-/var/tmp/luma-waydroid-system-ro/system}
compat_servicemanager=$compat_system/bin/servicemanager
compat_service=$compat_system/bin/service
adapter_dir=${LUMA_STOCK_ADAPTER_DIR:-/tmp/qcomtee-smoke-src/adapter}
listener_stage=${LUMA_FP6_ENROLLMENT_STAGE:-/tmp/luma-stock-qcomtee-stage.UnUPSc}
secure_firmware=$listener_stage/firmware
persistent_fingerprint_root=/var/lib/luma/fingerprint
manager_access_shim=${LUMA_STOCK_MANAGER_ACCESS_SHIM:-/tmp/qcomtee-smoke-src/libluma-servicemanager-access.so}

fail() {
  printf 'event=fp6_stock_biometric_smoke_v1_failed mode=%s reason=%s\n' "$mode" "$1" >&2
  exit 1
}
critical_faults() {
  grep -Ei 'page allocation failure|WARNING: CPU|hangcheck|GMU.*(timeout|fault)|GPU.*(timeout|fault|hang)|qsee.*(fault|timeout)|qcomtee.*(fault|timeout)|TEE.*(fault|timeout)|BUG:|kernel panic|Oops:|Call trace:' || true
}

process_diagnostics() {
  local pid=$1 proc_root=${2:-/proc} task host_pid
  [[ -d $proc_root/$pid ]] || return 0
  printf '%s\n' "--- process diagnostics pid=$pid ---" >&2
  sed -n '/^Name:/p;/^State:/p;/^Uid:/p;/^Gid:/p;/^Threads:/p;/^voluntary_ctxt_switches:/p;/^nonvoluntary_ctxt_switches:/p' \
    "$proc_root/$pid/status" >&2 || true
  for task in "$proc_root/$pid"/task/*; do
    printf 'tid=%s wchan=' "${task##*/}" >&2
    cat "$task/wchan" >&2 2>/dev/null || true
    printf ' syscall=' >&2
    cat "$task/syscall" >&2 2>/dev/null || true
  done
  printf '%s\n' '--- file descriptors ---' >&2
  find "$proc_root/$pid/fd" -maxdepth 1 -type l -printf '%f -> %l\n' 2>/dev/null | sort -n >&2 || true
  host_pid=
  local host_status nspid
  for host_status in /proc/[0-9]*/status; do
    nspid=$(awk '/^NSpid:/ {print $NF}' "$host_status" 2>/dev/null || true)
    if [[ $nspid == "$pid" ]] && grep -q '^Uid:[[:space:]]*1000[[:space:]]' "$host_status" 2>/dev/null &&
      [[ $(readlink "${host_status%/status}/exe" 2>/dev/null || true) == *android.hardware.security.keymint-service-qti ]]; then
      host_pid=${host_status#/proc/}
      host_pid=${host_pid%/status}
      break
    fi
  done
  if [[ $host_pid =~ ^[0-9]+$ && -x /usr/bin/eu-stack ]]; then
    printf '%s\n' "--- symbol stack host_pid=$host_pid ---" >&2
    /usr/bin/eu-stack -p "$host_pid" -n 32 -m >&2 || true
  fi
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

if [[ ${2:-} != --inside ]]; then
  [[ $(id -u) -eq 0 ]] || fail not_root
  [[ $mode == manager || $mode == gatekeeper || $mode == fingerprint ]] || fail unsupported_mode
  exec unshare --mount --pid --fork "$self" "$mode" --inside
fi

[[ $(id -u) -eq 0 ]] || fail not_root
[[ $mode == manager || $mode == gatekeeper || $mode == fingerprint ]] || fail unsupported_mode
[[ $hold_seconds =~ ^[0-9]+$ && $hold_seconds -le 600 ]] || fail hold_seconds
[[ $persist_fingerprint == 0 || $persist_fingerprint == 1 ]] || fail persist_fingerprint
[[ $biometric_client_mode == enroll || $biometric_client_mode == authenticate ]] || \
  fail biometric_client_mode
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == "$expected_model" ]] || fail model
[[ " ${expected_kernels[*]} " == *" $(uname -r) "* ]] || fail kernel
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot

# The stock trees are persistent ordinary directories after a cold boot.
# Make private read-only bind views inside this disposable mount namespace;
# never depend on a prior boot's transient host mounts.
mount --make-rprivate /
for mountpoint in "$stock_system" "$stock_vendor" "$stock_system_ext" "$stock_product" "$stock_runtime"; do
  mount --bind "$mountpoint" "$mountpoint"
  mount -o remount,bind,ro "$mountpoint"
  findmnt -rn -T "$mountpoint" -o OPTIONS | tr ',' '\n' | grep -qx ro || fail stock_mount_not_read_only
done
mount --bind "$compat_system" "$compat_system"
mount -o remount,bind,ro "$compat_system"
findmnt -rn -T "$compat_servicemanager" -o OPTIONS | tr ',' '\n' | grep -qx ro || fail compat_mount_not_read_only

[[ $(sha256sum "$stock_system/bin/servicemanager" | cut -d' ' -f1) == "$expected_servicemanager_sha" ]] || fail servicemanager_hash
[[ $(sha256sum "$stock_system/bin/service" | cut -d' ' -f1) == "$expected_service_sha" ]] || fail service_hash
[[ $(sha256sum "$compat_servicemanager" | cut -d' ' -f1) == "$expected_compat_servicemanager_sha" ]] || fail compat_servicemanager_hash
[[ $(sha256sum "$compat_service" | cut -d' ' -f1) == "$expected_compat_service_sha" ]] || fail compat_service_hash
[[ $(sha256sum "$stock_vendor/bin/hw/android.hardware.gatekeeper-service-qti" | cut -d' ' -f1) == "$expected_gatekeeper_sha" ]] || fail gatekeeper_hash
[[ $(sha256sum "$stock_vendor/bin/hw/android.hardware.security.keymint-service-qti" | cut -d' ' -f1) == "$expected_keymint_sha" ]] || fail keymint_hash
[[ $(sha256sum "$stock_vendor/bin/hw/android.hardware.biometrics.fingerprint-service" | cut -d' ' -f1) == "$expected_fingerprint_service_sha" ]] || fail fingerprint_service_hash
[[ $(sha256sum "$stock_vendor/lib64/hw/fingerprint.default.so" | cut -d' ' -f1) == "$expected_fingerprint_hal_sha" ]] || fail fingerprint_hal_hash
! pgrep -x servicemanager >/dev/null || fail servicemanager_already_running
if [[ $mode != manager ]]; then
  marker="luma-stock-gatekeeper-$(cat /proc/sys/kernel/random/uuid)"
  printf '<6>%s\n' "$marker" >/dev/kmsg
  grep -qw qcomtee /proc/modules || fail qcomtee_missing
  [[ -c /dev/tee0 ]] || fail tee_device_missing
  [[ -d $adapter_dir && ! -L $adapter_dir ]] || fail adapter_directory
  [[ $(sha256sum "$adapter_dir/libminkdescriptor.so" | cut -d' ' -f1) == "$expected_mink_adapter_sha" ]] || fail mink_adapter_hash
  [[ $(sha256sum "$adapter_dir/libdmabufheap.so" | cut -d' ' -f1) == "$expected_dmabuf_shim_sha" ]] || fail dmabuf_shim_hash
  [[ $(sha256sum "$adapter_dir/libluma-fdsan-compat.so" | cut -d' ' -f1) == "$expected_fdsan_compat_sha" ]] || fail fdsan_compat_hash
  [[ $(sha256sum "$adapter_dir/libluma-biometric-properties.so" | cut -d' ' -f1) == "$expected_property_shim_sha" ]] || fail property_shim_hash
  [[ -f $manager_access_shim && ! -L $manager_access_shim ]] || fail manager_access_shim_identity
  [[ $(sha256sum "$manager_access_shim" | cut -d' ' -f1) == "$expected_manager_access_shim_sha" ]] || fail manager_access_shim_hash
  [[ -d $secure_firmware && ! -L $secure_firmware ]] || fail secure_firmware_directory
  [[ -f $listener_stage/listener.log && ! -L $listener_stage/listener.log ]] || fail listener_log_identity
  [[ -f $listener_stage/listener.pid && ! -L $listener_stage/listener.pid ]] || fail listener_pid_identity
  listener_pid=$(cat "$listener_stage/listener.pid")
  [[ $listener_pid =~ ^[0-9]+$ && -d /proc/$listener_pid ]] || fail listener_process_missing
  [[ $(awk '/^Uid:/ {print $2}' "/proc/$listener_pid/status") == 0 ]] || fail listener_process_uid
  [[ $(sha256sum "/proc/$listener_pid/exe" | cut -d' ' -f1) == "$expected_listener_daemon_sha" ]] || fail listener_daemon_hash
  listener_epoch=$(awk '
    /event=transport_start transport=qseecomcompat/ { registrations = ""; next }
    /event=listener_registered transport=qseecomcompat/ {
      registrations = registrations $0 "\n"
    }
    END { printf "%s", registrations }
  ' "$listener_stage/listener.log")
  [[ $(grep -c 'event=listener_registered transport=qseecomcompat' <<<"$listener_epoch") -eq 3 ]] || fail listener_count
  for listener_id in 10 8192 28672; do
    grep -Eq "event=listener_registered transport=qseecomcompat id=$listener_id([[:space:]]|$)" \
      <<<"$listener_epoch" || fail "listener_${listener_id}_missing"
  done
fi

root=$(mktemp -d /var/tmp/luma-stock-bio-root.XXXXXX)
mounted=false
cleanup() {
  local status=$?
  set +e
  if [[ $mounted == true ]]; then
    umount -R "$root" 2>/dev/null || true
  fi
  [[ ! -d $root ]] || find "$root" -depth -delete 2>/dev/null || true
  return "$status"
}
trap cleanup EXIT INT TERM HUP

mount --make-rprivate /
mount -t tmpfs -o mode=0755,size=64m tmpfs "$root"
mounted=true
mkdir -p "$root"/{system,vendor,system_ext,product,apex/com.android.runtime,dev,proc,sys,data,linkerconfig,luma-manager}
bind_ro "$stock_system" "$root/system"
bind_ro "$stock_runtime" "$root/apex/com.android.runtime"
bind_ro "$stock_vendor" "$root/vendor"
bind_ro "$stock_system_ext" "$root/system_ext"
bind_ro "$stock_product" "$root/product"
touch "$root/luma-manager/libluma-servicemanager-access.so"
bind_ro "$manager_access_shim" "$root/luma-manager/libluma-servicemanager-access.so"
touch "$root/luma-manager/libluma-biometric-properties.so"
bind_ro "$adapter_dir/libluma-biometric-properties.so" \
  "$root/luma-manager/libluma-biometric-properties.so"

mount -t tmpfs -o mode=0755,size=8m tmpfs "$root/dev"
mkdir -p "$root/dev"/{socket,binderfs}
for device in null zero random urandom kmsg binder hwbinder vndbinder; do
  bind_device "/dev/$device" "$root/dev/$device"
done
mount -t proc -o nosuid,nodev,noexec proc "$root/proc"
mount --bind /sys "$root/sys"
mount -o remount,bind,ro "$root/sys"
mount -t tmpfs -o mode=0700,size=16m tmpfs "$root/data"
mount -t tmpfs -o mode=0755,size=4m tmpfs "$root/linkerconfig"

ld_library_path=/apex/com.android.runtime/lib64/bionic:/system/lib64
manager_pid=
gatekeeper_pid=
keymint_pid=
fingerprint_pid=
log_sink_pid=
manager_log_sink_pid=
cleanup_manager() {
  local status=$?
  set +e
  if [[ -n $fingerprint_pid ]] && kill -0 "$fingerprint_pid" 2>/dev/null; then
    kill -TERM "$fingerprint_pid" 2>/dev/null || true
    wait "$fingerprint_pid" 2>/dev/null || true
  fi
  if [[ -n $gatekeeper_pid ]] && kill -0 "$gatekeeper_pid" 2>/dev/null; then
    kill -TERM "$gatekeeper_pid" 2>/dev/null || true
    wait "$gatekeeper_pid" 2>/dev/null || true
  fi
  if [[ -n $keymint_pid ]] && kill -0 "$keymint_pid" 2>/dev/null; then
    kill -TERM "$keymint_pid" 2>/dev/null || true
    wait "$keymint_pid" 2>/dev/null || true
  fi
  if [[ -n $manager_pid ]] && kill -0 "$manager_pid" 2>/dev/null; then
    kill -TERM "$manager_pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$manager_pid" 2>/dev/null || break
      sleep 0.05
    done
  fi
  if [[ -n $log_sink_pid ]] && kill -0 "$log_sink_pid" 2>/dev/null; then
    kill -TERM "$log_sink_pid" 2>/dev/null || true
    wait "$log_sink_pid" 2>/dev/null || true
  fi
  if [[ -n $manager_log_sink_pid ]] && kill -0 "$manager_log_sink_pid" 2>/dev/null; then
    kill -TERM "$manager_log_sink_pid" 2>/dev/null || true
    wait "$manager_log_sink_pid" 2>/dev/null || true
  fi
  [[ -z $manager_pid ]] || wait "$manager_pid" 2>/dev/null || true
  return "$status"
}
trap 'cleanup_manager; cleanup' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

if [[ $mode != manager ]]; then
  [[ $(sha256sum /usr/bin/socat | cut -d' ' -f1) == "$expected_socat_sha" ]] || fail socat_hash
  : >"$root/data/manager-logdw.bin"
  chmod 0600 "$root/data/manager-logdw.bin"
  /usr/bin/socat -u UNIX-RECV:"$root/dev/socket/logdw",mode=0666 - \
    >"$root/data/manager-logdw.bin" &
  manager_log_sink_pid=$!
  for _ in $(seq 1 20); do
    [[ -S $root/dev/socket/logdw ]] && break
    kill -0 "$manager_log_sink_pid" 2>/dev/null || fail manager_log_sink_exited
    sleep 0.05
  done
  [[ -S $root/dev/socket/logdw ]] || fail manager_log_sink_not_ready
fi

launch=(env -i LD_LIBRARY_PATH="$ld_library_path" /usr/sbin/chroot "$root" \
  /system/bin/sh -c \
  'export LD_PRELOAD=/luma-manager/libluma-servicemanager-access.so:/luma-manager/libluma-biometric-properties.so; exec /system/bin/servicemanager')
if [[ -n ${LUMA_STOCK_TRACE_MANAGER:-} ]]; then
  [[ -x $LUMA_STOCK_TRACE && ! -L $LUMA_STOCK_TRACE ]] || fail tracer_identity
  [[ -n ${LUMA_STOCK_TRACE_SHA256:-} ]] || fail tracer_hash_missing
  [[ $(sha256sum "$LUMA_STOCK_TRACE" | cut -d' ' -f1) == "$LUMA_STOCK_TRACE_SHA256" ]] || fail tracer_hash
  launch=("$LUMA_STOCK_TRACE" "${launch[@]}")
fi
"${launch[@]}" \
  >"$root/data/servicemanager.log" 2>&1 &
manager_pid=$!
ready=false
for _ in $(seq 1 5); do
  if ! kill -0 "$manager_pid" 2>/dev/null; then
    tail -100 "$root/data/servicemanager.log" >&2
    if [[ -f $root/data/manager-logdw.bin ]]; then
      printf '%s\n' '--- service-manager Android log text ---' >&2
      strings -a "$root/data/manager-logdw.bin" | sed -n '1,200p' >&2 || true
    fi
    fail servicemanager_exited
  fi
  if timeout 2 env -i LD_LIBRARY_PATH="$ld_library_path" /usr/sbin/chroot "$root" \
    /system/bin/sh -c \
    'export LD_PRELOAD=/luma-manager/libluma-biometric-properties.so; exec /system/bin/service list' \
    >"$root/data/service-list.txt" 2>"$root/data/service-list.err"; then
    ready=true
    break
  fi
  sleep 0.1
done
if [[ $ready != true ]]; then
  sed -n '1,80p' "$root/data/servicemanager.log" >&2
  sed -n '1,80p' "$root/data/service-list.err" >&2
  fail servicemanager_not_ready
fi
service_count=$(wc -l <"$root/data/service-list.txt")
printf 'event=stock_servicemanager_ready binder=/dev/binder registered_services=%s gpu_exposed=false biometric_commands=0\n' "$service_count"

if [[ $mode != manager ]]; then
  qrel_root=$root/qrel-root
  mkdir -p "$qrel_root"/{system,vendor,system_ext,product,apex/com.android.runtime,dev,proc,sys,data,linkerconfig,luma-adapter}
  chmod 0755 "$qrel_root" "$qrel_root/apex"
  bind_ro "$stock_system" "$qrel_root/system"
  bind_ro "$stock_vendor" "$qrel_root/vendor"
  bind_ro "$stock_system_ext" "$qrel_root/system_ext"
  bind_ro "$stock_product" "$qrel_root/product"
  bind_ro "$stock_runtime" "$qrel_root/apex/com.android.runtime"
  bind_ro "$adapter_dir" "$qrel_root/luma-adapter"
  if [[ -n $challenge_probe ]]; then
    [[ $mode == fingerprint ]] || fail challenge_probe_requires_fingerprint_mode
    [[ -f $challenge_probe && ! -L $challenge_probe ]] || fail challenge_probe_identity
    [[ $challenge_probe == "$adapter_dir/libluma-fingerprint-challenge-probe.so" ]] || \
      fail challenge_probe_path
    [[ $challenge_probe_sha =~ ^[0-9a-f]{64}$ ]] || fail challenge_probe_hash_missing
    [[ $(sha256sum "$challenge_probe" | cut -d' ' -f1) == "$challenge_probe_sha" ]] || fail challenge_probe_hash
  fi
  if [[ -n $enrollment_client ]]; then
    [[ $mode == fingerprint ]] || fail enrollment_client_requires_fingerprint_mode
    [[ $persist_fingerprint == 1 ]] || fail enrollment_client_requires_persistence
    [[ -f $enrollment_client && ! -L $enrollment_client ]] || fail enrollment_client_identity
    [[ $enrollment_client == "$adapter_dir/libluma-fingerprint-enrollment-client.so" ]] || \
      fail enrollment_client_path
    [[ $enrollment_client_sha =~ ^[0-9a-f]{64}$ ]] || fail enrollment_client_hash_missing
    [[ $(sha256sum "$enrollment_client" | cut -d' ' -f1) == "$enrollment_client_sha" ]] || \
      fail enrollment_client_hash
  fi
  mount -t tmpfs -o mode=0755,size=4m tmpfs "$qrel_root/vendor/firmware_mnt"
  mkdir -p "$qrel_root/vendor/firmware_mnt/image"
  bind_ro "$secure_firmware" "$qrel_root/vendor/firmware_mnt/image"
  mount -t tmpfs -o mode=0755,size=8m tmpfs "$qrel_root/dev"
  mkdir -p "$qrel_root/dev/socket"
  chmod 0755 "$qrel_root/dev/socket"
  for device in null zero random urandom kmsg; do
    bind_device "/dev/$device" "$qrel_root/dev/$device"
  done
  # Give only the disposable stock service UID its own Binder node in this
  # private /dev.  Do not relax the host's root-only Binder device modes.
  for device in binder hwbinder vndbinder; do
    binder_major=$((16#$(stat -c %t "/dev/$device")))
    binder_minor=$((16#$(stat -c %T "/dev/$device")))
    mknod "$qrel_root/dev/$device" c "$binder_major" "$binder_minor"
    chown 1000:1000 "$qrel_root/dev/$device"
    chmod 0600 "$qrel_root/dev/$device"
  done
  if [[ $mode == fingerprint ]]; then
    bind_device /dev/focaltech_fp "$qrel_root/dev/focaltech_fp"
    chown 1000:1000 "$qrel_root/dev/focaltech_fp"
    chmod 0660 "$qrel_root/dev/focaltech_fp"
  fi
  tee_major=$((16#$(stat -c %t /dev/tee0)))
  tee_minor=$((16#$(stat -c %T /dev/tee0)))
  mknod "$qrel_root/dev/tee0" c "$tee_major" "$tee_minor"
  chown 1000:1000 "$qrel_root/dev/tee0"
  chmod 0600 "$qrel_root/dev/tee0"
  mount -t proc -o nosuid,nodev,noexec proc "$qrel_root/proc"
  mount --bind /sys "$qrel_root/sys"
  mount -o remount,bind,ro "$qrel_root/sys"
  mount -t tmpfs -o mode=0711,size=16m tmpfs "$qrel_root/data"
  install -d -m 0711 -o root -g root "$qrel_root/data/vendor"
  install -d -m 0760 -o 1000 -g 1000 \
    "$qrel_root/data/vendor/fingerprint/ft9391/Private"
  install -d -m 0711 -o root -g root "$qrel_root/data/vendor_de/0"
  install -d -m 0760 -o 1000 -g 1000 "$qrel_root/data/vendor_de/0/fpdata"
  install -d -m 0700 -o 1000 -g 1000 \
    "$qrel_root/data/vendor_de/0/fpdata/templates"
  if [[ $mode == fingerprint && $persist_fingerprint == 1 ]]; then
    install -d -m 0700 -o root -g root "$persistent_fingerprint_root"
    install -d -m 0760 -o 1000 -g 1000 "$persistent_fingerprint_root/focaltech-data"
    mount --bind "$persistent_fingerprint_root/focaltech-data" \
      "$qrel_root/data/vendor/fingerprint"
    install -d -m 0760 -o 1000 -g 1000 \
      "$qrel_root/data/vendor/fingerprint/ft9391/Private"
    install -d -m 0700 -o 1000 -g 1000 "$persistent_fingerprint_root/android-state"
    install -d -m 0760 -o 1000 -g 1000 "$persistent_fingerprint_root/fpdata"
    install -d -m 0700 -o 1000 -g 1000 \
      "$persistent_fingerprint_root/fpdata/templates"
    install -d -m 0700 -o 1000 -g 1000 "$qrel_root/data/luma-state"
    mount --bind "$persistent_fingerprint_root/android-state" \
      "$qrel_root/data/luma-state"
    mount --bind "$persistent_fingerprint_root/fpdata" \
      "$qrel_root/data/vendor_de/0/fpdata"
  fi
  mount -t tmpfs -o mode=0755,size=4m tmpfs "$qrel_root/linkerconfig"

  [[ $(sha256sum /usr/bin/socat | cut -d' ' -f1) == "$expected_socat_sha" ]] || fail socat_hash
  : >"$qrel_root/data/logdw.bin"
  chmod 0600 "$qrel_root/data/logdw.bin"
  /usr/bin/socat -u UNIX-RECV:"$qrel_root/dev/socket/logdw",mode=0666 - \
    >"$qrel_root/data/logdw.bin" &
  log_sink_pid=$!
  for _ in $(seq 1 20); do
    [[ -S $qrel_root/dev/socket/logdw ]] && break
    kill -0 "$log_sink_pid" 2>/dev/null || fail log_sink_exited
    sleep 0.05
  done
  [[ -S $qrel_root/dev/socket/logdw ]] || fail log_sink_not_ready

  qrel_ld_library_path=/luma-adapter:/apex/com.android.runtime/lib64/bionic:/vendor/lib64:/vendor/lib64/hw:/system/lib64:/system_ext/lib64:/product/lib64
  keymint_launch=(env -i LD_LIBRARY_PATH="$qrel_ld_library_path" \
    /usr/sbin/chroot --userspec=1000:1000 "$qrel_root" /system/bin/sh -c \
    'export LD_PRELOAD=/luma-adapter/libluma-biometric-properties.so; exec /vendor/bin/hw/android.hardware.security.keymint-service-qti')
  "${keymint_launch[@]}" </dev/null >"$qrel_root/data/keymint.log" 2>&1 &
  keymint_pid=$!
  keymint_ready=false
  for _ in $(seq 1 100); do
    if ! kill -0 "$keymint_pid" 2>/dev/null; then
      tail -160 "$qrel_root/data/keymint.log" >&2
      printf '%s\n' '--- stock Android log text ---' >&2
      strings -a "$qrel_root/data/logdw.bin" | sed -n '1,200p' >&2 || true
      fail keymint_exited
    fi
    if timeout 2 env -i LD_LIBRARY_PATH="$ld_library_path" /usr/sbin/chroot "$root" \
      /system/bin/sh -c \
      'export LD_PRELOAD=/luma-manager/libluma-biometric-properties.so; exec /system/bin/service list' \
      >"$root/data/service-list-keymint.txt" 2>/dev/null &&
      grep -Fq 'android.hardware.security.keymint.IKeyMintDevice/default' "$root/data/service-list-keymint.txt"; then
      keymint_ready=true
      break
    fi
    sleep 0.1
  done
  if [[ $keymint_ready != true ]]; then
    process_diagnostics "$keymint_pid" "$qrel_root/proc"
    tail -160 "$qrel_root/data/keymint.log" >&2
    printf '%s\n' '--- stock Android log text ---' >&2
    strings -a "$qrel_root/data/logdw.bin" | sed -n '1,200p' >&2 || true
    printf '%s\n' '--- binder registry after KeyMint startup ---' >&2
    sed -n '1,160p' "$root/data/service-list-keymint.txt" >&2 || true
    printf '%s\n' '--- service-manager log after KeyMint startup ---' >&2
    sed -n '1,240p' "$root/data/servicemanager.log" >&2 || true
    printf '%s\n' '--- service-manager Android log text ---' >&2
    strings -a "$root/data/manager-logdw.bin" |
      grep -Ei 'servicemanager|service manager|keymint|secureclock|sharedsecret|remotely|permission|denied|manifest|vintf|declared|addservice|registration|binder' |
      sed -n '1,240p' >&2 || true
    fail keymint_not_ready
  fi
  printf 'event=stock_keymint_ready service=android.hardware.security.keymint.IKeyMintDevice/default binary=exact-qrel-16.95.0 transport=linux-qcomtee-adapter biometric_commands=0\n'

  gatekeeper_launch=(env -i LD_LIBRARY_PATH="$qrel_ld_library_path" \
    /usr/sbin/chroot --userspec=1000:1000 "$qrel_root" /system/bin/sh -c \
    'export LD_PRELOAD=/luma-adapter/libluma-biometric-properties.so; exec /vendor/bin/hw/android.hardware.gatekeeper-service-qti')
  if [[ -n ${LUMA_STOCK_TRACE:-} ]]; then
    gatekeeper_launch=("$LUMA_STOCK_TRACE" "${gatekeeper_launch[@]}")
  fi
  "${gatekeeper_launch[@]}" \
    </dev/null >"$qrel_root/data/gatekeeper.log" 2>&1 &
  gatekeeper_pid=$!
  gatekeeper_ready=false
  for _ in $(seq 1 50); do
    if ! kill -0 "$gatekeeper_pid" 2>/dev/null; then
      tail -100 "$qrel_root/data/gatekeeper.log" >&2
      printf '%s\n' '--- stock Android log text ---' >&2
      strings -a "$qrel_root/data/logdw.bin" | sed -n '1,160p' >&2 || true
      fail gatekeeper_exited
    fi
    if timeout 2 env -i LD_LIBRARY_PATH="$ld_library_path" /usr/sbin/chroot "$root" \
      /system/bin/sh -c \
      'export LD_PRELOAD=/luma-manager/libluma-biometric-properties.so; exec /system/bin/service list' \
      >"$root/data/service-list-gatekeeper.txt" 2>/dev/null &&
      grep -Fq 'android.hardware.gatekeeper.IGatekeeper/default' "$root/data/service-list-gatekeeper.txt"; then
      gatekeeper_ready=true
      break
    fi
    sleep 0.1
  done
  if [[ $gatekeeper_ready != true ]]; then
    tail -100 "$qrel_root/data/gatekeeper.log" >&2
    printf '%s\n' '--- stock Android log text ---' >&2
    strings -a "$qrel_root/data/logdw.bin" | sed -n '1,160p' >&2 || true
    printf '%s\n' '--- service-manager log after Gatekeeper startup ---' >&2
    sed -n '1,120p' "$root/data/servicemanager.log" >&2 || true
    printf '%s\n' '--- binder registry after Gatekeeper startup ---' >&2
    sed -n '1,120p' "$root/data/service-list-gatekeeper.txt" >&2 || true
    fail gatekeeper_not_ready
  fi
  gatekeeper_dmesg=$(dmesg | sed -n "/$marker/,\$p")
  [[ -z $(critical_faults <<<"$gatekeeper_dmesg") ]] || fail critical_fault
  printf 'event=stock_gatekeeper_ready service=android.hardware.gatekeeper.IGatekeeper/default binary=exact-qrel-16.95.0 transport=linux-qcomtee-adapter biometric_commands=0 protected_state_written=false\n'
  if [[ $mode == fingerprint ]]; then
    fingerprint_launch=(env -i LD_LIBRARY_PATH="$qrel_ld_library_path" \
      /usr/sbin/chroot --userspec=1000:1000 "$qrel_root" /system/bin/sh -c \
      'export LD_PRELOAD=/luma-adapter/libluma-biometric-properties.so:/luma-adapter/libluma-fdsan-compat.so; exec /vendor/bin/hw/android.hardware.biometrics.fingerprint-service')
    "${fingerprint_launch[@]}" </dev/null >"$qrel_root/data/fingerprint.log" 2>&1 &
    fingerprint_pid=$!
    fingerprint_ready=false
    for _ in $(seq 1 100); do
      if ! kill -0 "$fingerprint_pid" 2>/dev/null; then
        tail -180 "$qrel_root/data/fingerprint.log" >&2
        printf '%s\n' '--- stock Android log text ---' >&2
        strings -a "$qrel_root/data/logdw.bin" | sed -n '1,240p' >&2 || true
        fail fingerprint_exited
      fi
      if timeout 2 env -i LD_LIBRARY_PATH="$ld_library_path" /usr/sbin/chroot "$root" \
        /system/bin/sh -c \
        'export LD_PRELOAD=/luma-manager/libluma-biometric-properties.so; exec /system/bin/service list' \
        >"$root/data/service-list-fingerprint.txt" 2>/dev/null &&
        grep -Fq 'android.hardware.biometrics.fingerprint.IFingerprint/default' "$root/data/service-list-fingerprint.txt"; then
        fingerprint_ready=true
        break
      fi
      sleep 0.1
    done
    if [[ $fingerprint_ready != true ]]; then
      tail -180 "$qrel_root/data/fingerprint.log" >&2
      printf '%s\n' '--- stock Android log text ---' >&2
      strings -a "$qrel_root/data/logdw.bin" | sed -n '1,240p' >&2 || true
      printf '%s\n' '--- binder registry after fingerprint startup ---' >&2
      sed -n '1,160p' "$root/data/service-list-fingerprint.txt" >&2 || true
      fail fingerprint_not_ready
    fi
    fingerprint_dmesg=$(dmesg | sed -n "/$marker/,\$p")
    [[ -z $(critical_faults <<<"$fingerprint_dmesg") ]] || fail critical_fault
    printf 'event=stock_fingerprint_ready service=android.hardware.biometrics.fingerprint.IFingerprint/default binary=exact-qrel-16.95.0 initialization_only=true enrollment=false authentication=false raw_images=0 templates_written=0\n'
    if [[ -n $challenge_probe ]]; then
      listener_failure_count_before=$(grep -c 'dispatch=failed' "$listener_stage/listener.log" || true)
      challenge_probe_status=0
      env -i LD_LIBRARY_PATH="$qrel_ld_library_path" \
        /usr/sbin/chroot --userspec=1000:1000 "$qrel_root" /system/bin/sh -c \
        'export LD_PRELOAD=/luma-adapter/libluma-biometric-properties.so:/luma-adapter/libluma-fingerprint-challenge-probe.so; exec /system/bin/sh' \
        </dev/null >"$qrel_root/data/challenge-probe.log" 2>&1 || challenge_probe_status=$?
      sed -n '1,80p' "$qrel_root/data/challenge-probe.log"
      if [[ $challenge_probe_status -ne 0 ]]; then
        printf '%s\n' '--- fingerprint service log ---' >&2
        sed -n '1,240p' "$qrel_root/data/fingerprint.log" >&2 || true
        printf '%s\n' '--- stock Android log text ---' >&2
        strings -a "$qrel_root/data/logdw.bin" | tail -420 >&2 || true
        printf '%s\n' '--- QSEE listener tail ---' >&2
        tail -160 "$listener_stage/listener.log" >&2 || true
        printf '%s\n' '--- challenge kernel fault window ---' >&2
        dmesg | sed -n "/$marker/,\$p" | tail -240 >&2 || true
        fail "challenge_probe_exit_${challenge_probe_status}"
      fi
      grep -Fq 'event=fp6_challenge_probe_pass' "$qrel_root/data/challenge-probe.log" || \
        fail challenge_probe_failed
      listener_failure_count_after=$(grep -c 'dispatch=failed' "$listener_stage/listener.log" || true)
      [[ $listener_failure_count_after -eq $listener_failure_count_before ]] || \
        fail challenge_probe_listener_dispatch_failed
      challenge_dmesg=$(dmesg | sed -n "/$marker/,\$p")
      [[ -z $(critical_faults <<<"$challenge_dmesg") ]] || fail critical_fault
      printf 'event=stock_fingerprint_challenge_verified persistent_state_written=false acquisition=false\n'
    fi
    if [[ -n $enrollment_client ]]; then
      listener_failure_count_before=$(grep -c 'dispatch=failed' "$listener_stage/listener.log" || true)
      enrollment_client_status=0
      saved_terminal_state=
      if [[ -t 0 ]]; then
        saved_terminal_state=$(stty -g)
        stty -echo
      fi
      env -i LD_LIBRARY_PATH="$qrel_ld_library_path" \
        /usr/sbin/chroot --userspec=1000:1000 "$qrel_root" /system/bin/sh -c \
        'export LD_PRELOAD=/luma-adapter/libluma-biometric-properties.so:/luma-adapter/libluma-fingerprint-enrollment-client.so; exec /system/bin/sh' \
        || enrollment_client_status=$?
      if [[ -n $saved_terminal_state ]]; then
        stty "$saved_terminal_state"
      fi
      if [[ $enrollment_client_status -ne 0 ]]; then
        printf '%s\n' '--- sanitized fingerprint events ---' >&2
        grep -E '^event=' "$qrel_root/data/fingerprint.log" | tail -160 >&2 || true
        printf '%s\n' '--- sanitized QSEE metadata ---' >&2
        grep -E '^event=(transport_|listener_|storage_request|gpfs_transfer|rpmb_)' \
          "$listener_stage/listener.log" | tail -180 >&2 || true
        fail "enrollment_client_exit_${enrollment_client_status}"
      fi
      listener_failure_count_after=$(grep -c 'dispatch=failed' "$listener_stage/listener.log" || true)
      [[ $listener_failure_count_after -eq $listener_failure_count_before ]] || \
        fail enrollment_client_listener_dispatch_failed
      [[ -s "$persistent_fingerprint_root/qsee-state/persist/data/ifaa_fplist" ]] || \
        fail protected_template_missing
      protected_object=$(find "$persistent_fingerprint_root/qsee-state" \
        -type f -size +65535c -perm 0600 -print -quit)
      [[ -n $protected_object && -f $protected_object && ! -L $protected_object ]] || \
        fail protected_match_on_chip_template_missing
      enrollment_dmesg=$(dmesg | sed -n "/$marker/,\$p")
      [[ -z $(critical_faults <<<"$enrollment_dmesg") ]] || fail critical_fault
      if [[ $biometric_client_mode == enroll ]]; then
        credential_file=$persistent_fingerprint_root/android-state/credential.handle
        [[ -f $credential_file && ! -L $credential_file ]] || fail credential_handle_missing
        [[ $(stat -c %s "$credential_file") -eq 544 ]] || fail credential_handle_size
        [[ $(stat -c %a "$credential_file") == 600 ]] || fail credential_handle_mode
        # Android's system service owns this opaque Gatekeeper handle as UID
        # 1000. Keep the same service-only ownership for bounded verification.
        chown 1000:1000 "$credential_file"
        chmod 0600 "$credential_file"
        printf 'event=stock_fingerprint_enrollment_verified credential_handle=service_only template=match_on_chip ree_mirror_required=false raw_images=0\n'
      else
        printf 'event=stock_fingerprint_authentication_verified template=match_on_chip ree_mirror_required=false raw_images=0 hat_logged=0\n'
      fi
    fi
    if (( hold_seconds > 0 )); then
      printf 'event=stock_fingerprint_hold seconds=%s enrollment=false authentication=false\n' "$hold_seconds"
      sleep "$hold_seconds"
    fi
    kill -TERM "$fingerprint_pid" 2>/dev/null || true
    wait "$fingerprint_pid" 2>/dev/null || true
    fingerprint_pid=
  fi
  kill -TERM "$gatekeeper_pid" 2>/dev/null || true
  wait "$gatekeeper_pid" 2>/dev/null || true
  gatekeeper_pid=
  kill -TERM "$keymint_pid" 2>/dev/null || true
  wait "$keymint_pid" 2>/dev/null || true
  keymint_pid=
fi

cleanup_manager
manager_pid=
trap cleanup EXIT INT TERM HUP

printf 'event=fp6_stock_biometric_smoke_v1_passed mode=%s registry=exact-qrel-16.95.0 stock_services=exact-qrel-16.95.0 gpu_exposed=false biometric_commands=0 persistent_state_written=false\n' "$mode"
