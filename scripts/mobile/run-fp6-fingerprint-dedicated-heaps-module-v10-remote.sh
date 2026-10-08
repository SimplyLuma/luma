#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Ephemeral module-only probe for the physically accepted FP6 v10 boot image.
# Run as root only after staging the one pinned qseecomtee module at $stage.
# This never installs a module, starts a supplicant, loads a trustlet, or sends
# any fingerprint/TEE command.

set -Eeuo pipefail

stage=/tmp/luma-fingerprint-dedicated-heaps-module-v10
module=$stage/qseecomtee.ko
expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2
expected_boot_size=27512832
expected_boot_sha=bc12893e0403245e2ea4a1cb6a4801c1efdba515d6f726be18f2e55f1752ab33
expected_config_sha=a403b3cb247d3f2ce358f8debcc3b93d3c1949813488da8e12ac81540cee519c
expected_module_sha=126074584721781d038312eb2d0129cd9d6044ee1b54523999a69824d54b7f5e
expected_installed_module_sha=030959db4f3c21ddeef76863594e7c5d184bfe484119fd9e483b662bd19cfcd5
expected_focal_module_sha=e9ca41da358fa6bf0be128ed5ae7c386ea0496d2aa953c04274a3ac41c549987

loaded_by_runner=false
cleaned=false

fail() {
  printf 'event=dedicated_heaps_module_v10_failed reason=%s\n' "$1" >&2
  exit 1
}

critical_faults() {
  grep -Ei \
    'page allocation failure|allocation failed|WARNING: CPU|hangcheck|GMU.*(timeout|fault)|GPU.*(timeout|fault|hang)|qsee.*(fault|timeout)|TEE.*(fault|timeout)|BUG:|kernel panic|Oops:|Call trace:' \
    || true
}

cleanup() {
  local status=$?
  local cleanup_status=0
  set +e
  if [[ $cleaned != true ]]; then
    if [[ $loaded_by_runner == true ]] && grep -qw qseecomtee /proc/modules; then
      rmmod qseecomtee || cleanup_status=1
    fi
    if grep -qw qseecomtee /proc/modules; then
      printf 'event=cleanup_error reason=module_still_loaded\n' >&2
      cleanup_status=1
    fi
    if [[ -d $stage && ! -L $stage && $stage == /tmp/luma-fingerprint-dedicated-heaps-module-v10 ]]; then
      find "$stage" -depth -delete || cleanup_status=1
    else
      printf 'event=cleanup_error reason=stage_identity\n' >&2
      cleanup_status=1
    fi
    cleaned=true
  fi
  printf 'event=dedicated_heaps_module_v10_cleanup module_loaded=%s stage_present=%s\n' \
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
[[ $(find "$stage" -maxdepth 1 -type f | wc -l) -eq 1 ]] || fail staged_file_count
[[ $(find "$stage" -type l | wc -l) -eq 0 ]] || fail staged_symlink
[[ -f $module && ! -L $module ]] || fail module_identity
[[ $(sha256sum "$module" | cut -d' ' -f1) == "$expected_module_sha" ]] || fail module_hash
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == "$expected_model" ]] || fail model
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
[[ $(head -c "$expected_boot_size" /dev/disk/by-partlabel/boot_b | sha256sum | cut -d' ' -f1) == "$expected_boot_sha" ]] || fail boot_hash
[[ -r /proc/config.gz ]] || fail runtime_config_unavailable
runtime_config=$(gzip -dc /proc/config.gz)
[[ $(sha256sum <<<"$runtime_config" | cut -d' ' -f1) == "$expected_config_sha" ]] || fail runtime_config_hash
grep -qx 'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y' <<<"$runtime_config" || fail shmbridge_config
grep -qx '# CONFIG_QCOM_TZMEM_MODE_GENERIC is not set' <<<"$runtime_config" || fail generic_tzmem_enabled
grep -qx '# CONFIG_DMA_CMA is not set' <<<"$runtime_config" || fail dma_cma_enabled
[[ $(sha256sum /lib/modules/7.1.2/extra/luma-fingerprint/focaltech_fp.ko | cut -d' ' -f1) == "$expected_focal_module_sha" ]] || fail focal_module_hash
[[ $(sha256sum /lib/modules/7.1.2/extra/luma-fingerprint/qseecomtee.ko | cut -d' ' -f1) == "$expected_installed_module_sha" ]] || fail installed_qsee_module_hash
! grep -qw qseecomtee /proc/modules || fail module_already_loaded
! pgrep -x qsee-supplicant >/dev/null || fail supplicant_running
! pgrep -x qsee-app-loader >/dev/null || fail loader_running

ta_pool=/proc/device-tree/reserved-memory/qseecom-ta-pool
apps_pool=/proc/device-tree/reserved-memory/qseecom-apps-pool
scm=/proc/device-tree/firmware/scm
child=$scm/qseecom-tee-heaps
[[ -d $ta_pool && -d $apps_pool && -d $child ]] || fail dedicated_dt_nodes
[[ ! -e $scm/memory-region ]] || fail global_scm_owns_pool
[[ $(tr -d '\0' <"$child/compatible") == luma,qseecom-tee-heaps ]] || fail child_compatible
[[ -e $child/luma,dedicated-heaps ]] || fail child_opt_in
[[ $(od -An -tx1 -v "$child/memory-region-names" | tr -d ' \n') == 7461006170707300 ]] || fail memory_region_names
[[ $(od -An -tx1 -v "$ta_pool/size" | tr -d ' \n') == 0000000001000000 ]] || fail ta_pool_size
[[ $(od -An -tx1 -v "$apps_pool/size" | tr -d ' \n') == 0000000001400000 ]] || fail apps_pool_size
[[ $(od -An -tx1 -v "$ta_pool/alignment" | tr -d ' \n') == 0000000000400000 ]] || fail ta_pool_alignment
[[ $(od -An -tx1 -v "$apps_pool/alignment" | tr -d ' \n') == 0000000000400000 ]] || fail apps_pool_alignment
[[ $(od -An -tx1 -v "$ta_pool/alloc-ranges" | tr -d ' \n') == 00000000800000000000000080000000 ]] || fail ta_pool_range
[[ $(od -An -tx1 -v "$apps_pool/alloc-ranges" | tr -d ' \n') == 00000000800000000000000080000000 ]] || fail apps_pool_range
[[ -e $ta_pool/no-map && -e $apps_pool/no-map ]] || fail pool_mapping_policy
expected_regions=$(od -An -tx1 -v "$ta_pool/phandle" "$apps_pool/phandle" | tr -d ' \n')
actual_regions=$(od -An -tx1 -v "$child/memory-region" | tr -d ' \n')
[[ $actual_regions == "$expected_regions" ]] || fail child_pool_ownership

dmesg_before=$(dmesg)
[[ -z $(critical_faults <<<"$dmesg_before") ]] || fail preexisting_critical_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units
baseline_dmesg_lines=$(wc -l <<<"$dmesg_before")

insmod "$module"
loaded_by_runner=true
grep -qw qseecomtee /proc/modules || fail module_did_not_load

ready=false
for _ in {1..50}; do
  new_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
  if grep -Fq 'dedicated TA heap SHM Bridge ready: size=16777216' <<<"$new_dmesg" &&
     grep -Fq 'dedicated apps heap SHM Bridge ready: size=20971520' <<<"$new_dmesg" &&
     grep -Fq 'dedicated QSEECOM heaps active' <<<"$new_dmesg"; then
    ready=true
    break
  fi
  sleep 0.1
done
[[ $ready == true ]] || fail dedicated_heap_probe_timeout
[[ -c /dev/tee0 && -c /dev/teepriv0 ]] || fail tee_nodes
[[ $(stat -c '%a:%U:%G' /dev/tee0) == 600:root:root ]] || fail tee_node_permissions
[[ $(stat -c '%a:%U:%G' /dev/teepriv0) == 600:root:root ]] || fail teepriv_node_permissions
[[ $(cat /sys/module/qseecomtee/refcnt) == 0 ]] || fail unexpected_module_user
sleep 2
new_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
printf '%s\n' '--- dedicated-heaps module evidence ---'
grep -Ei 'dedicated .* heap|dedicated QSEECOM|SHM Bridge|qseecom|qcom_tzmem' <<<"$new_dmesg" || true
printf '%s\n' '--- end dedicated-heaps module evidence ---'
[[ -z $(critical_faults <<<"$new_dmesg") ]] || fail critical_kernel_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running_after_probe
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units_after_probe

rmmod qseecomtee
loaded_by_runner=false
! grep -qw qseecomtee /proc/modules || fail module_did_not_unload
[[ ! -e /dev/tee0 && ! -e /dev/teepriv0 ]] || fail tee_nodes_remained
find "$stage" -depth -delete
[[ ! -e $stage ]] || fail stage_cleanup
cleaned=true
trap - EXIT INT TERM HUP

post_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
[[ -z $(critical_faults <<<"$post_dmesg") ]] || fail cleanup_kernel_fault
printf 'event=dedicated_heaps_module_v10_result module_hash=%s ta_bridge=true apps_bridge=true tee_nodes=true unloaded=true stage_present=false trustlet_loaded=false biometric_commands=0\n' \
  "$expected_module_sha"
