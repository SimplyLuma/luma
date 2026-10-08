#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Bounded secure-world identity probe for the physically accepted FP6 v10
# dedicated-heaps transport. This loads exactly one named TA and sends no
# biometric, sensor, enrollment, authentication, template, or raw-image command.

set -Eeuo pipefail

stage=/tmp/luma-fingerprint-identity-v9
firmware_path=/sys/module/firmware_class/parameters/path
expected_old_firmware_path=/lib/firmware/postmarketos
expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2
expected_boot_size=27512832
expected_boot_sha=bc12893e0403245e2ea4a1cb6a4801c1efdba515d6f726be18f2e55f1752ab33
expected_config_sha=a403b3cb247d3f2ce358f8debcc3b93d3c1949813488da8e12ac81540cee519c
expected_module_sha=126074584721781d038312eb2d0129cd9d6044ee1b54523999a69824d54b7f5e
expected_installed_module_sha=030959db4f3c21ddeef76863594e7c5d184bfe484119fd9e483b662bd19cfcd5
expected_focal_module_sha=e9ca41da358fa6bf0be128ed5ae7c386ea0496d2aa953c04274a3ac41c549987
expected_supplicant_sha=dd355adb58b1775752cc2ff1172b5e4ec2f5914429a9d0efbe7e8bd871e6085e
expected_loader_sha=57955bc42c50ab5fccd27fa7b27d9c3a1e9717cb2e213fc31c8c820f81335900
expected_bundle_sha=104792353c21f86efd7eb9a5773acf033431bc7293b18062e817e898b2973df9

supplicant_pid=
loader_pid=
old_firmware_path=
module_loaded=false
cleaned=false

fail() {
  printf 'event=identity_v9_failed reason=%s\n' "$1" >&2
  exit 1
}

critical_faults() {
  grep -Ei \
    'page allocation failure|allocation failed|WARNING: CPU|hangcheck|GMU.*(timeout|fault)|GPU.*(timeout|fault|hang)|qsee.*(fault|timeout)|TEE.*(fault|timeout)|BUG:|kernel panic|Oops:|Call trace:' \
    || true
}

stop_process() {
  local name=$1 pid=$2
  [[ -n $pid ]] || return 0
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid"
    for _ in {1..50}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
  fi
  if kill -0 "$pid" 2>/dev/null; then
    printf 'event=cleanup_error process=%s reason=did_not_stop_after_sigterm\n' "$name" >&2
    return 1
  fi
  wait "$pid" 2>/dev/null || true
}

cleanup() {
  local status=$?
  local cleanup_status=0
  set +e
  if [[ $cleaned != true ]]; then
    stop_process qsee-app-loader "$loader_pid" || cleanup_status=1
    stop_process qsee-supplicant "$supplicant_pid" || cleanup_status=1
    if [[ -n $old_firmware_path && -w $firmware_path ]]; then
      printf '%s' "$old_firmware_path" >"$firmware_path" || cleanup_status=1
    fi
    if [[ $module_loaded == true ]] && grep -qw qseecomtee /proc/modules; then
      rmmod qseecomtee || cleanup_status=1
    fi
    if grep -qw qseecomtee /proc/modules; then
      printf 'event=cleanup_error reason=module_still_loaded\n' >&2
      cleanup_status=1
    fi
    if [[ -d $stage && ! -L $stage && $stage == /tmp/luma-fingerprint-identity-v9 ]]; then
      find "$stage" -depth -delete || cleanup_status=1
    else
      printf 'event=cleanup_error reason=stage_identity\n' >&2
      cleanup_status=1
    fi
    cleaned=true
  fi
  printf 'event=identity_v9_cleanup firmware_path=%s module_loaded=%s stage_present=%s\n' \
    "$(cat "$firmware_path" 2>/dev/null || printf unavailable)" \
    "$(grep -qw qseecomtee /proc/modules && printf true || printf false)" \
    "$([[ -e $stage ]] && printf true || printf false)"
  if (( cleanup_status != 0 && status == 0 )); then
    return "$cleanup_status"
  fi
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ -d $stage && ! -L $stage ]] || fail stage_identity
[[ $(find "$stage" -type f | wc -l) -eq 13 ]] || fail staged_file_count
[[ $(find "$stage" -type l | wc -l) -eq 0 ]] || fail staged_symlink
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == "$expected_model" ]] || fail model
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
[[ $(head -c "$expected_boot_size" /dev/disk/by-partlabel/boot_b | sha256sum | cut -d' ' -f1) == "$expected_boot_sha" ]] || fail boot_hash
runtime_config=$(gzip -dc /proc/config.gz)
[[ $(sha256sum <<<"$runtime_config" | cut -d' ' -f1) == "$expected_config_sha" ]] || fail runtime_config_hash
grep -qx 'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y' <<<"$runtime_config" || fail shmbridge_config
grep -qx '# CONFIG_DMA_CMA is not set' <<<"$runtime_config" || fail dma_cma_enabled
[[ $(sha256sum /lib/modules/7.1.2/extra/luma-fingerprint/focaltech_fp.ko | cut -d' ' -f1) == "$expected_focal_module_sha" ]] || fail focal_module_hash
[[ $(sha256sum /lib/modules/7.1.2/extra/luma-fingerprint/qseecomtee.ko | cut -d' ' -f1) == "$expected_installed_module_sha" ]] || fail installed_module_hash
[[ $(sha256sum "$stage/qseecomtee.ko" | cut -d' ' -f1) == "$expected_module_sha" ]] || fail staged_module_hash
[[ $(sha256sum "$stage/qsee-supplicant" | cut -d' ' -f1) == "$expected_supplicant_sha" ]] || fail supplicant_hash
[[ $(sha256sum "$stage/qsee-app-loader" | cut -d' ' -f1) == "$expected_loader_sha" ]] || fail loader_hash
! grep -qw qseecomtee /proc/modules || fail module_already_loaded
! pgrep -x qsee-supplicant >/dev/null || fail supplicant_already_running
! pgrep -x qsee-app-loader >/dev/null || fail loader_already_running

ta_pool=/proc/device-tree/reserved-memory/qseecom-ta-pool
apps_pool=/proc/device-tree/reserved-memory/qseecom-apps-pool
scm=/proc/device-tree/firmware/scm
child=$scm/qseecom-tee-heaps
[[ -d $ta_pool && -d $apps_pool && -d $child ]] || fail dedicated_dt_nodes
[[ ! -e $scm/memory-region ]] || fail global_scm_owns_pool
[[ $(tr -d '\0' <"$child/compatible") == luma,qseecom-tee-heaps ]] || fail child_compatible
[[ -e $child/luma,dedicated-heaps ]] || fail child_opt_in
expected_regions=$(od -An -tx1 -v "$ta_pool/phandle" "$apps_pool/phandle" | tr -d ' \n')
actual_regions=$(od -An -tx1 -v "$child/memory-region" | tr -d ' \n')
[[ $actual_regions == "$expected_regions" ]] || fail child_pool_ownership

[[ $(find "$stage/firmware" -maxdepth 1 -type f -name 'focal64.*' | wc -l) -eq 10 ]] || fail trustlet_file_count
bundle_sha=$(
  cd "$stage/firmware"
  sha256sum \
    focal64.b00 focal64.b01 focal64.b02 focal64.b03 focal64.b04 \
    focal64.b05 focal64.b06 focal64.b07 focal64.b08 focal64.mdt |
    sha256sum | cut -d' ' -f1
)
[[ $bundle_sha == "$expected_bundle_sha" ]] || fail trustlet_bundle_hash

dmesg_before=$(dmesg)
[[ -z $(critical_faults <<<"$dmesg_before") ]] || fail preexisting_critical_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units
baseline_dmesg_lines=$(wc -l <<<"$dmesg_before")
old_firmware_path=$(cat "$firmware_path")
[[ $old_firmware_path == "$expected_old_firmware_path" ]] || fail firmware_path_precondition

insmod "$stage/qseecomtee.ko"
module_loaded=true
grep -qw qseecomtee /proc/modules || fail module_did_not_load

module_ready=false
for _ in {1..50}; do
  module_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
  if grep -Fq 'dedicated TA heap SHM Bridge ready: size=16777216' <<<"$module_dmesg" &&
     grep -Fq 'dedicated apps heap SHM Bridge ready: size=20971520' <<<"$module_dmesg" &&
     grep -Fq 'dedicated QSEECOM heaps active' <<<"$module_dmesg"; then
    module_ready=true
    break
  fi
  sleep 0.1
done
[[ $module_ready == true ]] || fail dedicated_heap_probe_timeout
[[ -c /dev/tee0 && -c /dev/teepriv0 ]] || fail tee_nodes
[[ $(stat -c '%a:%U:%G' /dev/tee0) == 600:root:root ]] || fail tee_node_permissions
[[ $(stat -c '%a:%U:%G' /dev/teepriv0) == 600:root:root ]] || fail teepriv_node_permissions

printf '%s' "$stage/firmware" >"$firmware_path"
[[ $(cat "$firmware_path") == "$stage/firmware" ]] || fail firmware_path_stage
umask 077
"$stage/qsee-supplicant" --state-dir "$stage/state" >"$stage/supplicant.log" 2>&1 &
supplicant_pid=$!

listeners_ready=false
for _ in {1..100}; do
  kill -0 "$supplicant_pid" 2>/dev/null || fail supplicant_exited
  grep -Eq 'event=(transport_error|notify_error)' "$stage/supplicant.log" && fail supplicant_error
  while IFS= read -r listener_id; do
    case $listener_id in
      10|28672) ;;
      *) fail unexpected_listener ;;
    esac
  done < <(sed -n 's/.*event=listener_registered.* id=\([0-9][0-9]*\).*/\1/p' "$stage/supplicant.log")
  if grep -Eq 'event=listener_registered.* id=10([[:space:]]|$)' "$stage/supplicant.log" &&
     grep -Eq 'event=listener_registered.* id=28672([[:space:]]|$)' "$stage/supplicant.log"; then
    [[ $(grep -c 'event=listener_registered' "$stage/supplicant.log") -eq 2 ]] || fail listener_count
    listeners_ready=true
    break
  fi
  sleep 0.1
done
[[ $listeners_ready == true ]] || fail listener_timeout

"$stage/qsee-app-loader" focal64 >"$stage/loader.log" 2>&1 &
loader_pid=$!
outcome=
for _ in {1..200}; do
  grep -q 'event=application_attached' "$stage/loader.log" && fail unexpected_preloaded_application
  if grep -Eq 'event=application_loaded app=focal64([[:space:]]|$)' "$stage/loader.log"; then
    [[ $(grep -c 'event=application_loaded' "$stage/loader.log") -eq 1 ]] || fail application_identity_count
    grep -q 'operation=attach-session result=-2' "$stage/loader.log" || fail loaded_without_absent_attach
    grep -q 'operation=load-session result=0' "$stage/loader.log" || fail loaded_without_successful_load
    outcome=loaded
    break
  fi
  if grep -Eq 'event=loader_error app=focal64 stage=acquire errno=[0-9]+' "$stage/loader.log"; then
    outcome=load-error
    break
  fi
  grep -Eq 'event=notify_error' "$stage/loader.log" && fail loader_notify_error
  if ! kill -0 "$loader_pid" 2>/dev/null; then
    sleep 0.1
    grep -Eq 'event=loader_error app=focal64 stage=acquire errno=[0-9]+' "$stage/loader.log" || fail loader_exited_without_boundary
    outcome=load-error
    break
  fi
  sleep 0.1
done
[[ -n $outcome ]] || fail loader_timeout
if [[ $outcome == loaded ]]; then
  sleep 2
  kill -0 "$loader_pid" 2>/dev/null || fail loader_did_not_remain_resident
fi

new_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
printf '%s\n' '--- identity-v9 module and kernel evidence ---'
grep -Ei 'dedicated .* heap|dedicated QSEECOM|SHM Bridge|qseecom|qcom_tzmem|QSEE' <<<"$new_dmesg" || true
printf '%s\n' '--- identity-v9 loader evidence ---'
sed -n '1,240p' "$stage/loader.log"
printf '%s\n' '--- identity-v9 supplicant evidence ---'
sed -n '1,240p' "$stage/supplicant.log"
printf '%s\n' '--- end identity-v9 evidence ---'
[[ -z $(critical_faults <<<"$new_dmesg") ]] || fail critical_kernel_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running_after_probe
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units_after_probe

printf 'event=identity_v9_result listeners=10,28672 app=focal64 outcome=%s ta_session=%s state_files=%s biometric_commands=0\n' \
  "$outcome" "$([[ $outcome == loaded ]] && printf true || printf false)" \
  "$(find "$stage/state" -type f | wc -l)"

stop_process qsee-app-loader "$loader_pid"
loader_pid=
stop_process qsee-supplicant "$supplicant_pid"
supplicant_pid=
printf '%s' "$old_firmware_path" >"$firmware_path"
old_firmware_path=
rmmod qseecomtee
module_loaded=false
! grep -qw qseecomtee /proc/modules || fail module_did_not_unload
find "$stage" -depth -delete
[[ ! -e $stage ]] || fail stage_cleanup
cleaned=true
trap - EXIT INT TERM HUP

post_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
[[ -z $(critical_faults <<<"$post_dmesg") ]] || fail cleanup_kernel_fault
printf 'event=identity_v9_cleanup_complete firmware_path=%s module_loaded=false stage_present=false\n' \
  "$expected_old_firmware_path"
