#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only preflight for the stock-QREL Android telephony container. This
# script never starts Android, stops services, opens modem/audio devices, or
# modifies mounts. A failing item is a construction gate, not permission to
# weaken the check.

set -euo pipefail
umask 077

expected_model='The Fairphone (Gen. 6)'
expected_kernel_current='7.1.2-luma-fp-ims1'

stock_system=/var/tmp/luma-stock-qrel1695-system
stock_vendor=/var/tmp/luma-stock-qrel1695-vendor
stock_system_ext=/var/tmp/luma-stock-qrel1695-system_ext
stock_product=/var/tmp/luma-stock-qrel1695-product
stock_odm=/var/tmp/luma-stock-qrel1695-odm
stock_runtime=/var/tmp/luma-stock-qrel1695-runtime-apex
stock_apex_runtime=/var/tmp/luma-stock-qrel1695-apex-runtime1
stock_odm_image=/var/tmp/luma-stock-qrel1695-images/odm_a.img
manager_boundary=/var/tmp/luma-stock-qrel1695-manager-boundary1
expected_odm_sha=3d31463d172b485f866e25340887e36b96986bb5c2255bff9a6b7777277bb831
expected_manager_sha=69d4beaf4fb0cdd93ec9efd86ecc2fa3a06786024c6b6e475dc87434318e8552
expected_hwmanager_sha=03728b6e2d1b5de38aa281b4485767c677dd7eb5a623adfb326ca41cac5f8f91
expected_manager_manifest_sha=22ff2b4887d7d17ba8a1f174ba43bc044382ae06f5a376bee588664f2bfde627

declare -a failures=()

pass() {
  printf 'gate=%s result=pass detail=%s\n' "$1" "$2"
}

fail() {
  printf 'gate=%s result=fail detail=%s\n' "$1" "$2"
  failures+=("$1")
}

if [[ $(id -u) -eq 0 ]]; then
  pass root root
else
  fail root not_root
fi

model=$(tr -d '\0' </sys/firmware/devicetree/base/model 2>/dev/null || true)
if [[ $model == "$expected_model" ]]; then
  pass identity fairphone_gen6
else
  fail identity model_mismatch
fi

if [[ $(uname -r) == "$expected_kernel_current" ]]; then
  pass kernel accepted_ims1_release_identity
else
  fail kernel version_mismatch
fi

if grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline; then
  pass slot b
else
  fail slot not_b
fi

if command -v lxc-start >/dev/null 2>&1; then
  pass lxc "$(lxc-start --version 2>/dev/null || printf unknown)"
else
  fail lxc missing
fi

for namespace in cgroup ipc mnt net pid time user uts; do
  if [[ -e /proc/self/ns/$namespace ]]; then
    pass "namespace_$namespace" present
  else
    fail "namespace_$namespace" absent
  fi
done

kernel_config=$(zcat /proc/config.gz 2>/dev/null || true)
if grep -qx 'CONFIG_ANDROID_BINDER_IPC=y' <<<"$kernel_config"; then
  pass binder_ipc enabled
else
  fail binder_ipc disabled
fi

if grep -qx 'CONFIG_ANDROID_BINDERFS=y' <<<"$kernel_config"; then
  pass binderfs_kernel enabled
else
  fail binderfs_kernel disabled
fi

if grep -qw binder /proc/filesystems &&
    ! findmnt -rn -t binder -o TARGET 2>/dev/null | grep -qx '/dev/binderfs'; then
  pass binderfs_runtime filesystem_registered_private_mount_deferred
else
  fail binderfs_runtime filesystem_unregistered_or_global_mount_present
fi

if [[ -r /sys/fs/selinux/enforce ]]; then
  fail binder_security host_selinux_active_requires_separate_policy_review
elif [[ -f $manager_boundary/servicemanager && \
        -f $manager_boundary/hwservicemanager && \
        -f $manager_boundary/manifest.json && \
        $(sha256sum "$manager_boundary/servicemanager" | cut -d' ' -f1) == "$expected_manager_sha" && \
        $(sha256sum "$manager_boundary/hwservicemanager" | cut -d' ' -f1) == "$expected_hwmanager_sha" && \
        $(sha256sum "$manager_boundary/manifest.json" | cut -d' ' -f1) == "$expected_manager_manifest_sha" ]] && \
    python3 - "$manager_boundary/manifest.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    manifest = json.load(stream)
assert manifest["selinux_enforcement"] is False
assert manifest["requesting_transaction_sid"] is False
assert manifest["preload_shim"] is False
assert "private binderfs devices" in manifest["security_boundary"]
PY
then
  pass binder_security audited_no_selinux_manager_boundary
else
  fail binder_security audited_manager_boundary_absent_or_mismatched
fi

for mountpoint in \
  "$stock_system" \
  "$stock_vendor" \
  "$stock_system_ext" \
  "$stock_product" \
  "$stock_odm" \
  "$stock_runtime"; do
  if findmnt -rn -T "$mountpoint" -o OPTIONS 2>/dev/null | tr ',' '\n' | grep -qx ro; then
    pass "mount_$(basename "$mountpoint")" read_only
  else
    fail "mount_$(basename "$mountpoint")" missing_or_writable
  fi
done

declare -A expected_hashes=(
  ["$stock_vendor/bin/hw/qcrilNrd"]='375648e1fb48311bf8aaf0fdeba848d0d0b59264f84d9e421e8352d375298e54'
  ["$stock_vendor/lib64/libqcrilNrImsModule.so"]='9a535b92f7b3d27d4968521ae0e070b31110df5f4ba38e306a071e9b2ec28ce7'
  ["$stock_vendor/bin/ims_rtp_daemon"]='c54f75b1b5bff65daf9618631de3b8825a15fd9c8526d1d51560bfeacb8b5e70'
  ["$stock_system_ext/priv-app/ims/ims.apk"]='8a8d4cb8dc15482f2b9946aa1b18ef38ef4a9ad58ab4106eea5a2d8c6659b8bc'
  ["$stock_system/system/priv-app/TeleService/TeleService.apk"]='2a09d2eb70edc122fd30e85eaaf5676f977b6748367309e75a85d0e770f869b5'
  ["$stock_system/system/priv-app/Telecom/Telecom.apk"]='08e8ab7d4d3fe2b4aaccd4dec0ba818ac106fdf1645ad977bb18e2d2cc4932e1'
  ["$stock_system/system/bin/audioserver"]='9033b26bb4433d71f753c6c7672a313640b8f3bb3b0f9207c0ac711014dfd068'
  ["$stock_vendor/bin/hw/android.hardware.audio.service_64"]='39c8805285bc548e5f702cbf34aad1a9ce27ce1ca2c4c91c2510db34b9a8344f'
  ["$stock_vendor/lib64/hw/audio.primary.volcano.so"]='83e31c6b0362761e0f318d21212ac422aeaadae8832bdd35296fcb2f46a7bdcb'
)

for artifact in "${!expected_hashes[@]}"; do
  if [[ ! -f $artifact || -L $artifact ]]; then
    fail "artifact_$(basename "$artifact")" missing_or_symlink
    continue
  fi
  actual=$(sha256sum "$artifact" | cut -d' ' -f1)
  if [[ $actual == "${expected_hashes[$artifact]}" ]]; then
    pass "artifact_$(basename "$artifact")" hash_match
  else
    fail "artifact_$(basename "$artifact")" hash_mismatch
  fi
done

if [[ -f $stock_odm_image && ! -L $stock_odm_image ]] && \
    [[ $(sha256sum "$stock_odm_image" | cut -d' ' -f1) == "$expected_odm_sha" ]] && \
    findmnt -rn -T "$stock_odm" -o OPTIONS 2>/dev/null | tr ',' '\n' | grep -qx ro; then
  pass odm exact_qrel_hash_and_read_only_mount
else
  fail odm exact_qrel_hash_or_mount_mismatch
fi

compressed_apex_count=$(find "$stock_system/system/apex" -maxdepth 1 \
  -type f -name '*_compressed.apex' -printf . 2>/dev/null | wc -c)
apex_inventory=$stock_apex_runtime/metadata/activated-apex-inventory.json
apex_seal=$stock_apex_runtime/metadata/HASHES.sha256
mounted_apex_count=$(findmnt -rn -o TARGET | \
  awk -v root="$stock_apex_runtime/mounts/" 'index($0, root) == 1 { count++ } END { print count + 0 }')
inventory_count=$(python3 - "$apex_inventory" <<'PY' 2>/dev/null || printf 0
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(len(json.load(stream)))
PY
)
if [[ $compressed_apex_count -eq 27 && $inventory_count -eq 41 && \
      $mounted_apex_count -eq 41 && -f $apex_seal ]] && \
    sha256sum -c "$apex_seal" >/dev/null 2>&1 && \
    ! findmnt -rn -o TARGET,OPTIONS | \
      awk -v root="$stock_apex_runtime/mounts/" \
        'index($1, root) == 1 && $2 !~ /(^|,)ro(,|$)/ { bad=1 } END { exit bad ? 0 : 1 }'; then
  pass apex exact_41_payloads_avb_verified_hash_sealed_read_only
else
  fail apex "source_compressed_$compressed_apex_count inventory_$inventory_count mounted_$mounted_apex_count"
fi

for service in ModemManager luma-fp6-cellular-link luma-fp6-imsd; do
  if systemctl is-active --quiet "$service"; then
    pass "owner_$service" luma_active
  else
    fail "owner_$service" unexpected_inactive
  fi
done

declare -A stock_process_patterns=(
  [qcrilNrd]='(^|/)qcrilNrd($| )'
  [imsdaemon]='(^|/)imsdaemon($| )'
  [servicemanager]='(^|/)servicemanager($| )'
  [hwservicemanager]='(^|/)hwservicemanager($| )'
)

for process in "${!stock_process_patterns[@]}"; do
  if pgrep -f "${stock_process_patterns[$process]}" >/dev/null; then
    fail "stock_process_$process" unexpectedly_running
  else
    pass "stock_process_$process" absent
  fi
done

if ((${#failures[@]} == 0)); then
  printf 'READY_FOR_CONTAINER_BOOT=true\n'
  exit 0
fi

printf 'READY_FOR_CONTAINER_BOOT=false\n'
printf 'BLOCKING_GATE_COUNT=%d\n' "${#failures[@]}"
printf 'BLOCKING_GATES=%s\n' "$(IFS=,; printf '%s' "${failures[*]}")"
exit 1
