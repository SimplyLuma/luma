#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Look up only the stock Keymaster QSEE application needed by FocalTech's
# pre-initialize bootstrap. No TA command is invoked and no biometric path
# exists in this transaction.

set -Eeuo pipefail
umask 077

stage=/tmp/luma-fingerprint-qcomtee-keymaster-v24
expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2-luma-fp-cma1
expected_boot_sha=c6acad3a30d944d59648258eef383c33a3b51ac1148a98ef24256ee4dee61dd8
expected_config_sha=5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe
expected_module_sha=77ccd603bb4476dbbef1df72b3cd2e58f8b7399a693e5d96edcff77b5a505230
marker=luma-qcomtee-keymaster-v24-baseline

module_loaded=false
cleaned=false

fail() {
  printf 'event=qcomtee_keymaster_v24_failed reason=%s\n' "$1" >&2
  exit 1
}

critical_faults() {
  grep -Ei \
    'page allocation failure|allocation failed|WARNING: CPU|hangcheck|GMU.*(timeout|fault)|GPU.*(timeout|fault|hang)|qsee.*(fault|timeout)|qcomtee.*(fault|timeout)|TEE.*(fault|timeout)|SCM.*(fault|timeout)|BUG:|kernel panic|Oops:|Call trace:' \
    || true
}

cleanup() {
  local status=$?
  local cleanup_status=0
  set +e
  if [[ $cleaned != true ]]; then
    if [[ $module_loaded == true ]] && grep -qw qcomtee /proc/modules; then
      rmmod qcomtee || cleanup_status=1
    fi
    if [[ -d $stage && ! -L $stage && $stage == /tmp/luma-fingerprint-qcomtee-keymaster-v24 ]]; then
      find "$stage" -depth -delete || cleanup_status=1
    else
      cleanup_status=1
    fi
    cleaned=true
  fi
  printf 'event=qcomtee_keymaster_v24_cleanup module_loaded=%s stage_present=%s\n' \
    "$(grep -qw qcomtee /proc/modules && printf true || printf false)" \
    "$([[ -e $stage ]] && printf true || printf false)"
  if (( cleanup_status != 0 && status == 0 )); then
    return "$cleanup_status"
  fi
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ -d $stage && ! -L $stage ]] || fail stage_identity
[[ $(find "$stage" -type f | wc -l) -eq 2 ]] || fail staged_file_count
[[ $(find "$stage" -type l | wc -l) -eq 0 ]] || fail staged_symlink
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == "$expected_model" ]] || fail model
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
[[ $(head -c 100663296 /dev/disk/by-partlabel/boot_b | sha256sum | cut -d' ' -f1) == "$expected_boot_sha" ]] || fail boot_hash
runtime_config=$(gzip -dc /proc/config.gz)
[[ $(sha256sum <<<"$runtime_config" | cut -d' ' -f1) == "$expected_config_sha" ]] || fail runtime_config_hash
grep -qx 'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y' <<<"$runtime_config" || fail shmbridge_config
grep -qx '# CONFIG_MODULE_SIG_FORCE is not set' <<<"$runtime_config" || fail signature_policy
grep -qx '# CONFIG_QCOMTEE is not set' <<<"$runtime_config" || fail qcomtee_already_in_kernel
[[ $(sha256sum "$stage/qcomtee-control.ko" | cut -d' ' -f1) == "$expected_module_sha" ]] || fail module_hash
[[ $(modinfo -F vermagic "$stage/qcomtee-control.ko" | awk '{print $1}') == "$expected_kernel" ]] || fail module_vermagic
[[ $(modinfo -F name "$stage/qcomtee-control.ko") == qcomtee ]] || fail module_name
[[ -z $(modinfo -F depends "$stage/qcomtee-control.ko") ]] || fail module_dependencies
[[ -z $(modinfo -F signer "$stage/qcomtee-control.ko") ]] || fail module_signature

[[ -d /sys/bus/platform/devices/qcomtee && ! -L /sys/bus/platform/devices/qcomtee/driver ]] || fail platform_state
! grep -qw qcomtee /proc/modules || fail module_already_loaded
! grep -qw qseecomtee /proc/modules || fail legacy_qsee_loaded
! pgrep -x qsee-supplicant >/dev/null || fail supplicant_running
! pgrep -x qsee-app-loader >/dev/null || fail loader_running
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 0 ]] || fail preexisting_tee_nodes
[[ -z $(dmesg | critical_faults) ]] || fail preexisting_critical_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units
[[ $(cat /sys/module/firmware_class/parameters/path) == /lib/firmware/postmarketos ]] || fail firmware_path

printf '<6>%s\n' "$marker" >/dev/kmsg
insmod "$stage/qcomtee-control.ko" luma_keymaster_lookup=1
module_loaded=true
grep -qw qcomtee /proc/modules || fail module_did_not_load
new_dmesg=$(dmesg | sed -n "/$marker/,\$p")

grep -Eq 'qcomtee: QTEE version [0-9]+\.[0-9]+\.[0-9]+' <<<"$new_dmesg" || fail qtee_version
grep -Fq 'luma-control: begin app=keymaster loader_uid=122 ta_commands=0 biometric_commands=0' <<<"$new_dmesg" || fail control_begin
grep -Fq 'luma-control: service uid=122 accepted' <<<"$new_dmesg" || fail compatibility_service
grep -Eq 'luma-control: keymaster lookup accepted arch=[0-9]+ ta_commands=0' <<<"$new_dmesg" || fail keymaster_lookup
grep -Fq 'luma-control: complete ta_commands=0 biometric_commands=0' <<<"$new_dmesg" || fail control_complete
! grep -Eq 'luma-control: (.*failed|.*rejected|lookup transport=|keymaster lookup unavailable)' <<<"$new_dmesg" || fail control_error
[[ -z $(critical_faults <<<"$new_dmesg") ]] || fail critical_fault

printf '%s\n' '--- qcomtee keymaster-lookup-v24 evidence ---'
grep -E 'qcomtee: QTEE version|luma-control:' <<<"$new_dmesg"
printf '%s\n' '--- end qcomtee keymaster-lookup-v24 evidence ---'

rmmod qcomtee
module_loaded=false
! grep -qw qcomtee /proc/modules || fail module_cleanup
find "$stage" -depth -delete
cleaned=true
trap - EXIT INT TERM HUP

post_dmesg=$(dmesg | sed -n "/$marker/,\$p")
[[ -z $(critical_faults <<<"$post_dmesg") ]] || fail cleanup_critical_fault
printf 'event=qcomtee_keymaster_v24_complete app=keymaster lookup=accepted ta_commands=0 biometric_commands=0 module_loaded=false stage_present=false\n'
