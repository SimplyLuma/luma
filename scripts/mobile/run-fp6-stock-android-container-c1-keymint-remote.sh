#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Bounded secure-hardware construction gate for the stock QREL Android
# container.  The host owns the QSEE supplicant and RPMB listener; the
# container receives only the object-based QCOMTEE /dev/tee0, never the
# separate QSEECOM client/privileged nodes, modem, audio,
# GPU, input, block, or RPMB devices. The gate stops at exact KeyMint/Keystore
# publication and always restores the host runtime.

set -Eeuo pipefail
umask 077

name=luma-stock-android-c1
lxc_path=/var/lib/lxc
state=/var/lib/luma/stock-android-container1
config=$lxc_path/$name/config
manifest=$state/manifest.json
stage=/var/tmp/luma-stock-qrel1695-keymint-gate1
firmware_path=/sys/module/firmware_class/parameters/path
protected_state=/var/lib/luma/fingerprint/qsee-state
adapter_dir=/var/tmp/luma-fingerprint-overlay-v1/adapter
expected_kernel=7.1.2-luma-fp-ims1
expected_manifest_sha=65a317645db47fb0dfa7b2b8f3ee9b923fa03a543ece43deb158c2e0c14ebfd8
expected_config_sha=db477c4d10c1511d1444c91a093fe8c4cca3a506a263f5177b33c26b23c1ae6f
expected_module_sha=2438afb551a6c53b497ab588ab9bfefd4722a635c9ad3e4ff93e51108f2acf39
expected_qcomtee_module_sha=54b336ee9c420173ef2419c5bd2d1d4192c72c485d3a2e3ba7f85c32e2019ab7
expected_supplicant_sha=d979286aef1fecb23dd250192c6a062863ef999f38927b2e2ae329627af8154c
expected_keymaster_sha=8acb7eec3333ba720fb7fb95ad6832567b561d41e0125386451e475ec9899198
expected_mink_adapter_sha=836275fa27897614d7cfafd0f51a946ecf3a48610b8d964c15cafe6d3f4f745c
expected_dmabuf_adapter_sha=0e0cb66562f6c1c0afbf969f7abb3c49d0c12c13b1704deb6fb4b07a3eee135f
run_id=$(date -u +%Y%m%dT%H%M%SZ)
run_dir=$state/state/logs/keymint-$run_id
config_backup=$run_dir/config.before
supplicant_pid=
kernel_follow_pid=
container_started=false
qcomtee_module_loaded=false
qseecom_module_loaded=false
firmware_changed=false
tee_mode=
result=fail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Retained only as the exact reproduction harness for the failed split-
# transport checkpoint.  Listener 8192 can remain pinned during its teardown,
# so this path must never be run again.  The replacement keeps the complete
# stock listener graph inside C1.
die 'retired split-listener gate; use run-fp6-stock-android-container-c1-stock-qseecomd-remote.sh after a clean reboot'

check_luma_owners() {
  local service
  for service in ModemManager luma-fp6-cellular-link luma-fp6-imsd; do
    systemctl is-active --quiet "$service" || return 1
  done
}

cleanup() {
  local status=$?
  set +e
  if [[ $container_started == true ]]; then
    lxc-stop -P "$lxc_path" -n "$name" -k >/dev/null 2>&1 || true
  fi
  if [[ -f $config_backup ]]; then
    install -o root -g root -m 0600 "$config_backup" "$config"
  fi
  if [[ -n $supplicant_pid ]]; then
    kill -TERM "$supplicant_pid" 2>/dev/null || true
    wait "$supplicant_pid" 2>/dev/null || true
  fi
  if [[ -n $tee_mode && -c /dev/tee0 ]]; then
    chmod "$tee_mode" /dev/tee0 2>/dev/null || true
  fi
  if [[ $qseecom_module_loaded == true ]] && grep -qw qseecomtee /proc/modules; then
    rmmod qseecomtee 2>/dev/null || true
  fi
  if [[ $qcomtee_module_loaded == true ]] && grep -qw qcomtee /proc/modules; then
    rmmod qcomtee 2>/dev/null || true
  fi
  if [[ $firmware_changed == true ]]; then
    printf '%s' "$old_firmware_path" >"$firmware_path" 2>/dev/null || true
  fi
  if [[ -n $kernel_follow_pid ]]; then
    kill "$kernel_follow_pid" 2>/dev/null || true
    wait "$kernel_follow_pid" 2>/dev/null || true
  fi
  if [[ -d $run_dir ]]; then
    systemctl is-active ModemManager luma-fp6-cellular-link luma-fp6-imsd \
      >"$run_dir/luma-owners-after.log" 2>&1 || true
    printf 'result=%s\ncontainer_started=%s\nqcomtee_module_loaded=%s\nqseecom_module_loaded=%s\n' \
      "$result" "$container_started" "$qcomtee_module_loaded" \
      "$qseecom_module_loaded" >"$run_dir/result.env"
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
[[ $(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || printf STOPPED) == STOPPED ]] ||
  die 'container is already active'
check_luma_owners || die 'a Luma modem owner is unexpectedly inactive'
! grep -qw qseecomtee /proc/modules || die 'qseecomtee is already loaded'
! grep -qw qcomtee /proc/modules || die 'qcomtee is already loaded'
! pgrep -x qsee-supplicant >/dev/null || die 'qsee-supplicant is already running'
[[ -c /dev/bsg/0:0:0:49476 ]] || die 'RPMB BSG device is absent'
[[ -f $manifest && ! -L $manifest ]] || die 'manifest is absent or linked'
[[ -f $config && ! -L $config ]] || die 'LXC config is absent or linked'
[[ $(sha256sum "$manifest" | cut -d' ' -f1) == "$expected_manifest_sha" ]] ||
  die 'manifest hash mismatch'
[[ $(sha256sum "$config" | cut -d' ' -f1) == "$expected_config_sha" ]] ||
  die 'LXC config hash mismatch'
[[ -d $stage && ! -L $stage ]] || die 'stage is absent or linked'
[[ $(find "$stage" -type f | wc -l) -eq 4 ]] || die 'unexpected staged file count'
[[ $(find "$stage" -type l | wc -l) -eq 0 ]] || die 'staged symlink found'
[[ $(sha256sum "$stage/qseecomtee.ko" | cut -d' ' -f1) == "$expected_module_sha" ]] ||
  die 'qseecomtee hash mismatch'
[[ $(sha256sum "$stage/qcomtee.ko" | cut -d' ' -f1) == "$expected_qcomtee_module_sha" ]] ||
  die 'qcomtee hash mismatch'
[[ $(sha256sum "$stage/qsee-supplicant" | cut -d' ' -f1) == "$expected_supplicant_sha" ]] ||
  die 'qsee-supplicant hash mismatch'
[[ $(sha256sum "$stage/firmware/keymaster64.elf" | cut -d' ' -f1) == "$expected_keymaster_sha" ]] ||
  die 'keymaster firmware hash mismatch'
[[ -d $adapter_dir && ! -L $adapter_dir ]] || die 'accepted adapter directory is absent or linked'
[[ -f $adapter_dir/libminkdescriptor.so && ! -L $adapter_dir/libminkdescriptor.so ]] ||
  die 'Mink adapter is absent or linked'
[[ -f $adapter_dir/libdmabufheap.so && ! -L $adapter_dir/libdmabufheap.so ]] ||
  die 'DMA-buffer adapter is absent or linked'
[[ $(sha256sum "$adapter_dir/libminkdescriptor.so" | cut -d' ' -f1) == "$expected_mink_adapter_sha" ]] ||
  die 'Mink adapter hash mismatch'
[[ $(sha256sum "$adapter_dir/libdmabufheap.so" | cut -d' ' -f1) == "$expected_dmabuf_adapter_sha" ]] ||
  die 'DMA-buffer adapter hash mismatch'
[[ $(modinfo -F name "$stage/qseecomtee.ko") == qseecomtee ]] || die 'module identity mismatch'
[[ $(modinfo -F vermagic "$stage/qseecomtee.ko" | awk '{print $1}') == "$expected_kernel" ]] ||
  die 'module vermagic mismatch'
[[ $(modinfo -F name "$stage/qcomtee.ko") == qcomtee ]] || die 'QCOMTEE module identity mismatch'
[[ $(modinfo -F vermagic "$stage/qcomtee.ko" | awk '{print $1}') == "$expected_kernel" ]] ||
  die 'QCOMTEE module vermagic mismatch'
[[ -z $(modinfo -F depends "$stage/qcomtee.ko") ]] || die 'QCOMTEE module dependency mismatch'

install -d -o root -g root -m 0700 "$run_dir" "$protected_state"
cp --preserve=mode,ownership,timestamps "$config" "$config_backup"
sha256sum "$manifest" "$config" "$stage/qcomtee.ko" "$stage/qseecomtee.ko" \
  "$stage/qsee-supplicant" "$stage/firmware/keymaster64.elf" \
  "$adapter_dir/libminkdescriptor.so" "$adapter_dir/libdmabufheap.so" \
  >"$run_dir/input-hashes.log"
systemctl is-active ModemManager luma-fp6-cellular-link luma-fp6-imsd \
  >"$run_dir/luma-owners-before.log"
stdbuf -oL dmesg --follow-new --raw >"$run_dir/kernel-new.log" &
kernel_follow_pid=$!

old_firmware_path=$(cat "$firmware_path")
printf '%s' "$stage/firmware" >"$firmware_path"
firmware_changed=true
# Load the object driver first so its unprivileged client is deterministically
# /dev/tee0, matching the accepted Mink adapter. QSEECOM then receives tee1
# and teepriv0 (the privileged index is allocated independently) and continues
# to service host-owned listeners independently.
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 0 ]] ||
  die 'preexisting TEE nodes found'
insmod "$stage/qcomtee.ko"
qcomtee_module_loaded=true
[[ -c /dev/tee0 ]] || die 'QCOMTEE client node was not created'
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 1 ]] ||
  die 'unexpected QCOMTEE node topology'
insmod "$stage/qseecomtee.ko"
qseecom_module_loaded=true
[[ -c /dev/tee1 && -c /dev/teepriv0 ]] ||
  die 'QSEECOM listener nodes were not created'
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 3 ]] ||
  die 'unexpected combined TEE node topology'
tee_mode=$(stat -c %a /dev/tee0)
# The container has no Android ueventd ownership transition for this host-created
# node.  World access is limited by its private device cgroup plus the single
# bind below, and is restored before module removal.
chmod 0666 /dev/tee0

"$stage/qsee-supplicant" --state-dir "$protected_state" \
  >"$run_dir/qsee-supplicant.log" 2>&1 &
supplicant_pid=$!
for _ in {1..100}; do
  kill -0 "$supplicant_pid" 2>/dev/null || die 'qsee-supplicant exited'
  grep -Eq 'event=(transport_error|notify_error|rpmb_rejected)' \
    "$run_dir/qsee-supplicant.log" && die 'QSEE listener error'
  listener_ids=$(sed -n 's/.*event=listener_registered.* id=\([0-9][0-9]*\).*/\1/p' \
    "$run_dir/qsee-supplicant.log" | sort -n | tr '\n' ',' | sed 's/,$//')
  [[ $listener_ids == 10,8192,28672 ]] && break
  sleep 0.1
done
[[ ${listener_ids:-} == 10,8192,28672 ]] || die 'exact listener set was not registered'

tee_major=$(stat -c '%t' /dev/tee0)
tee_minor=$(stat -c '%T' /dev/tee0)
tee_major=$((16#$tee_major))
tee_minor=$((16#$tee_minor))
cat >>"$config" <<EOF
lxc.cgroup2.devices.allow = c $tee_major:$tee_minor rwm
lxc.mount.entry = /dev/tee0 dev/tee0 none bind,create=file 0 0
lxc.mount.entry = $adapter_dir/libminkdescriptor.so vendor/lib64/libminkdescriptor.so none bind,ro,create=file 0 0
lxc.mount.entry = $adapter_dir/libdmabufheap.so system/lib64/libdmabufheap.so none bind,ro,create=file 0 0
EOF

(
  umask 000
  exec lxc-start -P "$lxc_path" -n "$name" -d -l TRACE -o "$run_dir/lxc.log"
)
container_started=true

# Android's stock KeyMint utility deliberately waits for this property before
# opening the QSEE client environment.  The listener processes live on the
# Fedora host in this design, so Android init cannot publish the fact itself.
# Mirror only the readiness fact that was already proven above; do not use the
# property to bypass listener identity or registration checks.
listener_property_set=false
for _ in {1..100}; do
  [[ $(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || true) == RUNNING ]] ||
    die 'container stopped before listener readiness publication'
  if lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
       /system/bin/setprop vendor.sys.listeners.registered true >/dev/null 2>&1 &&
     [[ $(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
       /system/bin/getprop vendor.sys.listeners.registered 2>/dev/null || true) == true ]]; then
    listener_property_set=true
    printf 'listeners=10,8192,28672\nproperty=vendor.sys.listeners.registered\nvalue=true\n' \
      >"$run_dir/listener-readiness.log"
    break
  fi
  sleep 0.1
done
[[ $listener_property_set == true ]] ||
  die 'stock listener readiness property could not be truthfully published'

for _ in {1..200}; do
  [[ $(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || true) == RUNNING ]] ||
    die 'container stopped before KeyMint publication'
  services=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /system/bin/service list 2>/dev/null || true)
  if grep -Fq 'android.hardware.security.keymint.IKeyMintDevice/default' <<<"$services" &&
     grep -Fq 'android.hardware.security.sharedsecret.ISharedSecret/default' <<<"$services" &&
     grep -Fq 'android.hardware.security.secureclock.ISecureClock/default' <<<"$services" &&
     grep -Fq 'android.hardware.security.keymint.IRemotelyProvisionedComponent/default' <<<"$services" &&
     grep -Fq 'android.security.maintenance' <<<"$services"; then
    printf '%s\n' "$services" >"$run_dir/services.log"
    break
  fi
  sleep 0.1
done
[[ -s $run_dir/services.log ]] || die 'KeyMint/Keystore publication timed out'

init_pid=$(lxc-info -P "$lxc_path" -n "$name" -pH)
[[ $init_pid =~ ^[0-9]+$ ]] || die 'container init PID unavailable'
find "/proc/$init_pid/root/dev" -xdev -type b -print -quit | grep -q . &&
  die 'container exposes a block device'
[[ -c /proc/$init_pid/root/dev/tee0 ]] || die 'container TEE client node is absent'
[[ ! -e /proc/$init_pid/root/dev/tee1 ]] || die 'container exposes QSEECOM client node'
[[ ! -e /proc/$init_pid/root/dev/teepriv0 && ! -e /proc/$init_pid/root/dev/teepriv1 ]] ||
  die 'container exposes privileged TEE node'
[[ ! -e /proc/$init_pid/root/dev/bsg/0:0:0:49476 ]] || die 'container exposes RPMB device'
lxc-attach -P "$lxc_path" -n "$name" --clear-env -- /system/bin/ps -A -o PID,UID,NAME \
  >"$run_dir/processes.log" 2>&1 || true
check_luma_owners || die 'a Luma modem owner changed during KeyMint gate'

lxc-stop -P "$lxc_path" -n "$name" -k
container_started=false
kill -TERM "$supplicant_pid"
wait "$supplicant_pid" || true
supplicant_pid=
chmod "$tee_mode" /dev/tee0
tee_mode=
rmmod qseecomtee
qseecom_module_loaded=false
rmmod qcomtee
qcomtee_module_loaded=false
printf '%s' "$old_firmware_path" >"$firmware_path"
firmware_changed=false
kill "$kernel_follow_pid" 2>/dev/null || true
wait "$kernel_follow_pid" 2>/dev/null || true
kernel_follow_pid=
install -o root -g root -m 0600 "$config_backup" "$config"

if grep -Eiq 'kernel panic|Oops:|(^|[[:space:]<])BUG:|hangcheck|GMU.*(timeout|[[:space:]:=_-]fault)|qsee.*(panic|fatal|abort|timeout|[[:space:]:=_-]fault)|TEE.*(panic|fatal|abort|timeout|[[:space:]:=_-]fault)|I/O error' \
  "$run_dir/kernel-new.log"; then
  die 'kernel/TEE/GPU/GMU fault observed'
fi
check_luma_owners || die 'a Luma modem owner did not survive teardown'
result=pass
printf 'C1_KEYMINT_GATE=true\n'
printf 'PRIVATE_TEE_CLIENT_ONLY=true\n'
printf 'PRIVILEGED_TEE_NODE_EXPOSED=false\n'
printf 'MODEM_AUDIO_GPU_EXPOSED=false\n'
printf 'LUMA_MODEM_OWNERS_UNCHANGED=true\n'
printf 'EVIDENCE_DIR=%s\n' "$run_dir"
