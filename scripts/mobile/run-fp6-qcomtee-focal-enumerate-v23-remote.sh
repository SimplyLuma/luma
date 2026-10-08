#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Physically exercise the stock Keymaster bootstrap followed by focal64
# initialization and template enumeration.
# The temporary module has no capture, enrollment, authentication, template
# write, raw-image, or partition interface.

set -Eeuo pipefail
umask 077

stage=/tmp/luma-fingerprint-qcomtee-focal-v39
firmware_path=/sys/module/firmware_class/parameters/path
expected_old_firmware_path=/lib/firmware/postmarketos
expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2-luma-fp-cma1
expected_boot_sha=c6acad3a30d944d59648258eef383c33a3b51ac1148a98ef24256ee4dee61dd8
expected_config_sha=5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe
expected_module_sha=2a41cb562af332a71c84bed6a6ea5732ded2b83b8e72a0de9e8f993914061755
expected_focal_module_sha=be860585db2c68ca80b3ee9e5e4929413c0d7207f9524ba489f974c46e26d051
expected_bundle_sha=9159f0cead5b6478b4279e0ac85d83b38f62374dad80f0a925f0cb1ad02fbdb7
expected_keymaster_sha=8acb7eec3333ba720fb7fb95ad6832567b561d41e0125386451e475ec9899198
expected_keymaster_size=441312
marker=luma-qcomtee-focal-v39-baseline

qcomtee_loaded=false
focal_loaded=false
focal_preloaded=false
old_firmware_path=
cleaned=false

fail() {
  printf 'event=qcomtee_focal_v39_failed reason=%s\n' "$1" >&2
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
    if [[ $qcomtee_loaded == true ]] && grep -qw qcomtee /proc/modules; then
      rmmod qcomtee || cleanup_status=1
    fi
    if [[ $focal_loaded == true ]] && grep -qw focaltech_fp /proc/modules; then
      modprobe -r focaltech_fp || cleanup_status=1
    fi
    if [[ -d $stage && ! -L $stage && $stage == /tmp/luma-fingerprint-qcomtee-focal-v39 ]]; then
      find "$stage" -depth -delete || cleanup_status=1
    else
      cleanup_status=1
    fi
    cleaned=true
  fi
  printf 'event=qcomtee_focal_v39_cleanup firmware_path=%s qcomtee_loaded=%s focal_loaded=%s stage_present=%s\n' \
    "$(cat "$firmware_path" 2>/dev/null || printf unavailable)" \
    "$(grep -qw qcomtee /proc/modules && printf true || printf false)" \
    "$(grep -qw focaltech_fp /proc/modules && printf true || printf false)" \
    "$([[ -e $stage ]] && printf true || printf false)"
  if (( cleanup_status != 0 && status == 0 )); then
    return "$cleanup_status"
  fi
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ -d $stage && ! -L $stage ]] || fail stage_identity
[[ $(find "$stage" -type f | wc -l) -eq 12 ]] || fail staged_file_count
[[ $(find "$stage" -type l | wc -l) -eq 0 ]] || fail staged_symlink
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == "$expected_model" ]] || fail model
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
[[ $(head -c 100663296 /dev/disk/by-partlabel/boot_b | sha256sum | cut -d' ' -f1) == "$expected_boot_sha" ]] || fail boot_hash
runtime_config=$(gzip -dc /proc/config.gz)
[[ $(sha256sum <<<"$runtime_config" | cut -d' ' -f1) == "$expected_config_sha" ]] || fail runtime_config_hash
grep -qx 'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y' <<<"$runtime_config" || fail shmbridge_config
grep -qx '# CONFIG_MODULE_SIG_FORCE is not set' <<<"$runtime_config" || fail signature_policy
[[ $(sha256sum "$stage/qcomtee-control.ko" | cut -d' ' -f1) == "$expected_module_sha" ]] || fail module_hash
[[ $(modinfo -F vermagic "$stage/qcomtee-control.ko" | awk '{print $1}') == "$expected_kernel" ]] || fail module_vermagic
[[ $(modinfo -F name "$stage/qcomtee-control.ko") == qcomtee ]] || fail module_name
[[ -z $(modinfo -F signer "$stage/qcomtee-control.ko") ]] || fail module_signature

installed_focal=$(modinfo -n focaltech_fp)
[[ -f $installed_focal ]] || fail focal_module_missing
[[ $(sha256sum "$installed_focal" | cut -d' ' -f1) == "$expected_focal_module_sha" ]] || fail focal_module_hash
[[ $(find "$stage/firmware" -maxdepth 1 -type f -name 'focal64.b??' | wc -l) -eq 9 ]] || fail trustlet_file_count
bundle_sha=$(
  cd "$stage/firmware"
  sha256sum focal64.b00 focal64.b01 focal64.b02 focal64.b03 \
    focal64.b04 focal64.b05 focal64.b06 focal64.b07 focal64.b08 |
    sha256sum | cut -d' ' -f1
)
[[ $bundle_sha == "$expected_bundle_sha" ]] || fail trustlet_bundle_hash
[[ -f $stage/firmware/keymaster64.elf ]] || fail keymaster_elf_missing
[[ $(stat -c %s "$stage/firmware/keymaster64.elf") -eq $expected_keymaster_size ]] || fail keymaster_elf_size
[[ $(sha256sum "$stage/firmware/keymaster64.elf" | cut -d' ' -f1) == "$expected_keymaster_sha" ]] || fail keymaster_elf_hash

[[ -d /sys/bus/platform/devices/qcomtee && ! -L /sys/bus/platform/devices/qcomtee/driver ]] || fail platform_state
! grep -qw qcomtee /proc/modules || fail qcomtee_already_loaded
! grep -qw qseecomtee /proc/modules || fail legacy_qsee_loaded
if grep -qw focaltech_fp /proc/modules; then
  focal_preloaded=true
  [[ -c /dev/focaltech_fp ]] || fail preloaded_focal_device_missing
fi
! pgrep -x qsee-supplicant >/dev/null || fail supplicant_running
! pgrep -x qsee-app-loader >/dev/null || fail loader_running
[[ -z $(dmesg | critical_faults) ]] || fail preexisting_critical_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units

old_firmware_path=$(cat "$firmware_path")
[[ $old_firmware_path == "$expected_old_firmware_path" ]] || fail firmware_path_precondition
printf '%s' "$stage/firmware" >"$firmware_path"
if [[ $focal_preloaded != true ]]; then
  modprobe focaltech_fp
  focal_loaded=true
fi
grep -qw focaltech_fp /proc/modules || fail focal_module_did_not_load
[[ -c /dev/focaltech_fp ]] || fail focal_device_missing
printf '<6>%s\n' "$marker" >/dev/kmsg

insmod "$stage/qcomtee-control.ko" luma_focal_enumerate=1
qcomtee_loaded=true
grep -qw qcomtee /proc/modules || fail qcomtee_module_did_not_load
new_dmesg=$(dmesg | sed -n "/$marker/,\$p")

grep -Eq 'qcomtee: QTEE version [0-9]+\.[0-9]+\.[0-9]+' <<<"$new_dmesg" || fail qtee_version
grep -Fq 'luma-control: service uid=122 accepted' <<<"$new_dmesg" || fail compatibility_service
grep -Eq 'luma-control: identity accepted app=focal64 distinguished_name=.* elf_bytes=3600472; bounded TA operations requested' <<<"$new_dmesg" || fail identity_acceptance
grep -Eq 'luma-keymaster: QSEE identity (accepted app=keymaster64 distinguished_name=keymaster elf_bytes=441312 partition_read_only=1|already resident app=keymaster64 arch=2)' <<<"$new_dmesg" || fail keymaster_identity_acceptance
grep -Fq 'luma-keymaster: Android KeyMint credential accepted uid=1000 contents_logged=0' <<<"$new_dmesg" || fail keymaster_credential_acceptance
grep -Fq 'luma-keymaster: service uid=151 selector=150 accepted' <<<"$new_dmesg" || fail keymaster_service_acceptance
grep -Fq 'luma-keymaster: command=0x200 accepted qseecom=' <<<"$new_dmesg" || fail keymaster_version_acceptance
grep -Fq 'luma-keymaster: command=0x207 accepted client_version=5 contents_logged=0' <<<"$new_dmesg" || fail keymaster_client_version_acceptance
grep -Fq 'luma-keymaster: command=0x3121 accepted keymint_message_version=300' <<<"$new_dmesg" || fail keymint_message_version_acceptance
grep -Fq 'luma-keymaster: shared-HMAC negotiation accepted participants=1 secret_material_logged=0' <<<"$new_dmesg" || fail keymaster_shared_hmac_acceptance
grep -Eq 'luma-keymaster: command=0x205 accepted blob_bytes=[1-9][0-9]* contents_logged=0' <<<"$new_dmesg" || fail keymaster_blob_acceptance
grep -Eq 'luma-keymaster: QSEE trustlet (unloaded|preserved preexisting=1)' <<<"$new_dmesg" || fail keymaster_lifecycle
grep -Fq 'luma-focal: command=0x1011 accepted response_payload=0' <<<"$new_dmesg" || fail key_sync_acceptance
grep -Fq 'luma-focal: command=0x1004 accepted response_payload=192' <<<"$new_dmesg" || fail initialize_acceptance
grep -Fq 'luma-focal: command=0x2005 accepted response_payload=' <<<"$new_dmesg" || fail enumerate_command_acceptance
grep -Eq 'luma-focal: enumeration accepted count=[0-9]+ raw_images=0 templates_written=0' <<<"$new_dmesg" || fail enumerate_acceptance
grep -Fq 'luma-control: control TA unloaded' <<<"$new_dmesg" || fail control_unload
grep -Fq 'luma-control: complete ta_commands=4 biometric_commands=0' <<<"$new_dmesg" || fail control_complete
! grep -Eq 'luma-control: (.*failed|.*rejected|app unexpectedly preloaded|lookup transport=|reconstruction failed=|load transport=|unload transport=)|luma-focal: .*failed|luma-focal: command=.*(transport|malformed|trustlet_status)' <<<"$new_dmesg" || fail control_error
! grep -Eq 'luma-keymaster: .*(rejected|transport=|status=|malformed)' <<<"$new_dmesg" || fail keymaster_error
[[ -z $(critical_faults <<<"$new_dmesg") ]] || fail critical_fault

printf '%s\n' '--- qcomtee focal-enumerate-v39 evidence ---'
grep -E 'qcomtee: QTEE version|luma-control:|luma-keymaster:|luma-focal:' <<<"$new_dmesg"
printf '%s\n' '--- end qcomtee focal-enumerate-v39 evidence ---'

printf '%s' "$old_firmware_path" >"$firmware_path"
old_firmware_path=
rmmod qcomtee
qcomtee_loaded=false
if [[ $focal_loaded == true ]]; then
  modprobe -r focaltech_fp
  focal_loaded=false
fi
find "$stage" -depth -delete
cleaned=true
trap - EXIT INT TERM HUP

post_dmesg=$(dmesg | sed -n "/$marker/,\$p")
[[ -z $(critical_faults <<<"$post_dmesg") ]] || fail cleanup_critical_fault
[[ $(systemctl is-system-running) == running ]] || fail cleanup_system_not_running
printf 'event=qcomtee_focal_v39_complete keymaster_identity=true keymaster_shared_hmac=true keymaster_lifecycle_clean=true key_sync=true initialize=true enumerate=true raw_images=0 templates_written=0 qcomtee_loaded=false focal_preloaded=%s focal_loaded_now=%s stage_present=false\n' \
  "$focal_preloaded" "$(grep -qw focaltech_fp /proc/modules && printf true || printf false)"
