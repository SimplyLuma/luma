#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Test only QSEEComCompat identity/load acceptance of Fairphone's exact signed
# focal64 image. The temporary module cannot obtain or invoke the TA application
# object and contains no biometric-command path.

set -Eeuo pipefail
umask 077

stage=/tmp/luma-fingerprint-qcomtee-focal-v22
firmware_path=/sys/module/firmware_class/parameters/path
expected_old_firmware_path=/lib/firmware/postmarketos
expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2-luma-fp-cma1
expected_boot_sha=c6acad3a30d944d59648258eef383c33a3b51ac1148a98ef24256ee4dee61dd8
expected_config_sha=5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe
expected_module_sha=224577ff7221aadb19a91242884526474a97f7ad5659e6259743a80f8d8a78af
expected_bundle_sha=9159f0cead5b6478b4279e0ac85d83b38f62374dad80f0a925f0cb1ad02fbdb7
marker=luma-qcomtee-focal-v22-baseline

module_loaded=false
old_firmware_path=
cleaned=false

fail() {
  printf 'event=qcomtee_focal_v22_failed reason=%s\n' "$1" >&2
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
    if [[ -n $old_firmware_path && -w $firmware_path ]]; then
      printf '%s' "$old_firmware_path" >"$firmware_path" || cleanup_status=1
    fi
    if [[ $module_loaded == true ]] && grep -qw qcomtee /proc/modules; then
      rmmod qcomtee || cleanup_status=1
    fi
    if grep -qw qcomtee /proc/modules; then
      printf 'event=qcomtee_focal_v22_cleanup_error reason=module_still_loaded\n' >&2
      cleanup_status=1
    fi
    if [[ -d $stage && ! -L $stage && $stage == /tmp/luma-fingerprint-qcomtee-focal-v22 ]]; then
      find "$stage" -depth -delete || cleanup_status=1
    else
      printf 'event=qcomtee_focal_v22_cleanup_error reason=stage_identity\n' >&2
      cleanup_status=1
    fi
    cleaned=true
  fi
  printf 'event=qcomtee_focal_v22_cleanup firmware_path=%s module_loaded=%s stage_present=%s\n' \
    "$(cat "$firmware_path" 2>/dev/null || printf unavailable)" \
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
[[ $(find "$stage" -type f | wc -l) -eq 10 ]] || fail staged_file_count
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

[[ $(find "$stage/firmware" -maxdepth 1 -type f -name 'focal64.b??' | wc -l) -eq 9 ]] || fail trustlet_file_count
bundle_sha=$(
  cd "$stage/firmware"
  sha256sum focal64.b00 focal64.b01 focal64.b02 focal64.b03 \
    focal64.b04 focal64.b05 focal64.b06 focal64.b07 focal64.b08 |
    sha256sum | cut -d' ' -f1
)
[[ $bundle_sha == "$expected_bundle_sha" ]] || fail trustlet_bundle_hash

[[ -d /sys/bus/platform/devices/qcomtee && ! -L /sys/bus/platform/devices/qcomtee/driver ]] || fail platform_state
! grep -qw qcomtee /proc/modules || fail module_already_loaded
! grep -qw qseecomtee /proc/modules || fail legacy_qsee_loaded
! pgrep -x qsee-supplicant >/dev/null || fail supplicant_running
! pgrep -x qsee-app-loader >/dev/null || fail loader_running
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 0 ]] || fail preexisting_tee_nodes
[[ -z $(dmesg | critical_faults) ]] || fail preexisting_critical_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units

old_firmware_path=$(cat "$firmware_path")
[[ $old_firmware_path == "$expected_old_firmware_path" ]] || fail firmware_path_precondition
printf '%s' "$stage/firmware" >"$firmware_path"
printf '<6>%s\n' "$marker" >/dev/kmsg

insmod "$stage/qcomtee-control.ko" luma_focal_identity=1
module_loaded=true
grep -qw qcomtee /proc/modules || fail module_did_not_load
[[ -c /dev/tee0 ]] || fail tee_node_missing
new_dmesg=$(dmesg | sed -n "/$marker/,\$p")

grep -Eq 'qcomtee: QTEE version [0-9]+\.[0-9]+\.[0-9]+' <<<"$new_dmesg" || fail qtee_version
grep -Fq 'luma-control: privileged client environment accepted' <<<"$new_dmesg" || fail privileged_environment
grep -Fq 'luma-control: begin app=focal64 loader_uid=122 ta_commands=0 biometric_commands=0' <<<"$new_dmesg" || fail control_begin
grep -Fq 'luma-control: service uid=122 accepted' <<<"$new_dmesg" || fail compatibility_service
grep -Fq 'luma-control: lookup result=23 app=focal64' <<<"$new_dmesg" || fail lookup_result
grep -Eq 'luma-control: identity accepted app=focal64 distinguished_name=.* elf_bytes=3600472; no TA operation invoked' <<<"$new_dmesg" || fail identity_acceptance
grep -Fq 'luma-control: control TA unloaded' <<<"$new_dmesg" || fail control_unload
grep -Fq 'luma-control: complete ta_commands=0 biometric_commands=0' <<<"$new_dmesg" || fail control_complete
[[ $(grep -c 'luma-control: identity accepted ' <<<"$new_dmesg") -eq 1 ]] || fail identity_count
! grep -Eq 'luma-control: (.*failed|.*rejected|app unexpectedly preloaded|lookup transport=|reconstruction failed=|load transport=|unload transport=)' <<<"$new_dmesg" || fail control_error
[[ -z $(critical_faults <<<"$new_dmesg") ]] || fail critical_fault

printf '%s\n' '--- qcomtee focal-identity-v22 evidence ---'
grep -E 'qcomtee: QTEE version|luma-control:' <<<"$new_dmesg"
printf '%s\n' '--- end qcomtee focal-identity-v22 evidence ---'

printf '%s' "$old_firmware_path" >"$firmware_path"
old_firmware_path=
rmmod qcomtee
module_loaded=false
! grep -qw qcomtee /proc/modules || fail module_cleanup
[[ ! -L /sys/bus/platform/devices/qcomtee/driver ]] || fail driver_cleanup
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 0 ]] || fail tee_node_cleanup
find "$stage" -depth -delete
[[ ! -e $stage ]] || fail stage_cleanup
cleaned=true
trap - EXIT INT TERM HUP

post_dmesg=$(dmesg | sed -n "/$marker/,\$p")
[[ -z $(critical_faults <<<"$post_dmesg") ]] || fail cleanup_critical_fault
printf 'event=qcomtee_focal_v22_complete loader_uid=122 app=focal64 identity_accepted=true controller_unloaded=true ta_commands=0 biometric_commands=0 module_loaded=false stage_present=false\n'
