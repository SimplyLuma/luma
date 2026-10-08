#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Reproduce the audited stock FP6 FocalTech initialization sequence and read
# only the template count. The caller must supply the exact freshly built
# module/helper hashes. This script has no capture, enrollment, authentication,
# template-write, raw-image, partition, slot, or bootloader operation.

set -Eeuo pipefail
umask 077

stage=/tmp/luma-fingerprint-qcomtee-focal-v41
phase=/sys/bus/platform/devices/qcomtee/luma_focal_phase
firmware_path=/sys/module/firmware_class/parameters/path
expected_old_firmware_path=/lib/firmware/postmarketos
expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2-luma-fp-cma1
expected_boot_sha=c6acad3a30d944d59648258eef383c33a3b51ac1148a98ef24256ee4dee61dd8
expected_config_sha=5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe
expected_module_sha=${LUMA_EXPECTED_QCOMTEE_MODULE_SHA256:?set exact qcomtee module SHA-256}
expected_helper_sha=${LUMA_EXPECTED_FOCAL_DRIVER_HELPER_SHA256:?set exact driver helper SHA-256}
expected_focal_module_sha=be860585db2c68ca80b3ee9e5e4929413c0d7207f9524ba489f974c46e26d051
expected_bundle_sha=9159f0cead5b6478b4279e0ac85d83b38f62374dad80f0a925f0cb1ad02fbdb7
expected_keymaster_sha=8acb7eec3333ba720fb7fb95ad6832567b561d41e0125386451e475ec9899198
expected_keymaster_size=441312
expected_sync_config_sha=8b013facc735799355dd39b78d0df0a11b547b5faa0f93ff18dd080a78a220c5
marker=luma-qcomtee-focal-v41-baseline

qcomtee_loaded=false
focal_loaded=false
focal_preloaded=false
driver_prepared=false
old_firmware_path=
cleaned=false

fail() {
  printf 'event=qcomtee_focal_v41_failed reason=%s\n' "$1" >&2
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
  local current_phase=unavailable
  set +e
  if [[ $cleaned != true ]]; then
    if [[ -r $phase ]]; then
      current_phase=$(cat "$phase" 2>/dev/null)
      case $current_phase in
        secure-prepared|probe-retry|device-initialized)
          printf '%s\n' abort >"$phase" 2>/dev/null || true
          ;;
      esac
    fi
    if [[ $driver_prepared == true && -x $stage/fp6-focal-driver-stage ]]; then
      "$stage/fp6-focal-driver-stage" cleanup || cleanup_status=1
      driver_prepared=false
    fi
    if [[ $qcomtee_loaded == true ]] && grep -qw qcomtee /proc/modules; then
      rmmod qcomtee || cleanup_status=1
      qcomtee_loaded=false
    fi
    if [[ -n $old_firmware_path && -w $firmware_path ]]; then
      printf '%s' "$old_firmware_path" >"$firmware_path" || cleanup_status=1
      old_firmware_path=
    fi
    if [[ $focal_loaded == true ]] && grep -qw focaltech_fp /proc/modules; then
      modprobe -r focaltech_fp || cleanup_status=1
      focal_loaded=false
    fi
    if [[ -d $stage && ! -L $stage && $stage == /tmp/luma-fingerprint-qcomtee-focal-v41 ]]; then
      find "$stage" -depth -delete || cleanup_status=1
    else
      cleanup_status=1
    fi
    cleaned=true
  fi
  printf 'event=qcomtee_focal_v41_cleanup firmware_path=%s qcomtee_loaded=%s focal_loaded=%s stage_present=%s\n' \
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
[[ $(find "$stage" -type f | wc -l) -eq 13 ]] || fail staged_file_count
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
[[ $(sha256sum "$stage/fp6-focal-driver-stage" | cut -d' ' -f1) == "$expected_helper_sha" ]] || fail helper_hash
[[ $(modinfo -F vermagic "$stage/qcomtee-control.ko" | awk '{print $1}') == "$expected_kernel" ]] || fail module_vermagic
[[ $(modinfo -F name "$stage/qcomtee-control.ko") == qcomtee ]] || fail module_name
[[ -z $(modinfo -F signer "$stage/qcomtee-control.ko") ]] || fail module_signature
file "$stage/fp6-focal-driver-stage" | grep -Fq 'ELF 64-bit LSB executable, ARM aarch64' || fail helper_elf
file "$stage/fp6-focal-driver-stage" | grep -Fq 'statically linked' || fail helper_linkage

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
grep -Fq "FOCAL_SYNC_CONFIG_SHA256=$expected_sync_config_sha" "$stage/manifest.env" || fail sync_config_manifest

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

"$stage/fp6-focal-driver-stage" prepare
driver_prepared=true
insmod "$stage/qcomtee-control.ko" luma_focal_enumerate=1
qcomtee_loaded=true
grep -qw qcomtee /proc/modules || fail qcomtee_module_did_not_load
[[ -r $phase && -w $phase ]] || fail phase_control_missing
[[ $(cat "$phase") == secure-prepared ]] || fail secure_prepare_state

"$stage/fp6-focal-driver-stage" prepare-probe
if ! printf '%s\n' probe >"$phase"; then
  [[ $(cat "$phase") == probe-retry ]] || fail physical_probe
  "$stage/fp6-focal-driver-stage" retry-probe
  printf '%s\n' probe >"$phase" || fail physical_probe_retry
fi
[[ $(cat "$phase") == device-initialized ]] || fail device_initialize_state
"$stage/fp6-focal-driver-stage" finish-probe
printf '%s\n' enumerate >"$phase" || fail enumerate_phase
[[ $(cat "$phase") == complete ]] || fail enumeration_state

new_dmesg=$(dmesg | sed -n "/$marker/,\$p")
grep -Eq 'qcomtee: QTEE version [0-9]+\.[0-9]+\.[0-9]+' <<<"$new_dmesg" || fail qtee_version
grep -Fq 'luma-control: service uid=122 accepted' <<<"$new_dmesg" || fail compatibility_service
grep -Eq 'luma-control: identity accepted app=focal64 distinguished_name=.* elf_bytes=3600472; bounded TA operations requested' <<<"$new_dmesg" || fail identity_acceptance
grep -Fq 'luma-keymaster: QSEE identity accepted app=keymaster64 distinguished_name=keymaster elf_bytes=441312 partition_read_only=1' <<<"$new_dmesg" || fail keymaster_identity_acceptance
grep -Fq 'luma-keymaster: command=0x207 accepted client_version=5 contents_logged=0' <<<"$new_dmesg" || fail keymaster_client_version_acceptance
grep -Fq 'luma-keymaster: shared-HMAC negotiation accepted participants=1 secret_material_logged=0' <<<"$new_dmesg" || fail keymaster_hmac_acceptance
grep -Fq 'luma-keymaster: stock command=0x205 accepted blob_bytes=152 contents_logged=0' <<<"$new_dmesg" || fail keymaster_stock_key_acceptance
grep -Fq 'luma-focal: command=0x1011 accepted response_payload=0' <<<"$new_dmesg" || fail key_sync_acceptance
grep -Fq "luma-focal: stock configuration accepted bytes=7845 sha256=$expected_sync_config_sha" <<<"$new_dmesg" || fail config_acceptance
grep -Fq 'luma-focal: command=0x1006 accepted response_payload=0' <<<"$new_dmesg" || fail spi_acceptance
grep -Fq 'luma-focal: command=0x100a accepted response_payload=60' <<<"$new_dmesg" || fail probe_acceptance
grep -Fq 'luma-focal: command=0x100b accepted response_payload=60' <<<"$new_dmesg" || fail device_acceptance
grep -Fq 'luma-focal: command=0x1004 accepted response_payload=192' <<<"$new_dmesg" || fail initialize_acceptance
grep -Fq 'luma-focal: command=0x2005 accepted response_payload=' <<<"$new_dmesg" || fail enumerate_command_acceptance
grep -Eq 'luma-focal: enumeration accepted count=[0-9]+ raw_images=0 templates_written=0' <<<"$new_dmesg" || fail enumerate_acceptance
grep -Fq 'luma-focal: command=0x1007 accepted response_payload=0' <<<"$new_dmesg" || fail spi_cleanup
grep -Fq 'luma-control: staged focal TA unloaded' <<<"$new_dmesg" || fail control_unload
! grep -Eq 'luma-focal: command=0x(1013|2001|2008)' <<<"$new_dmesg" || fail forbidden_biometric_command
! grep -Eq 'luma-control: (.*failed|.*rejected|lookup transport=|reconstruction failed=|load transport=)|luma-focal: .*failed|luma-focal: command=.*(malformed|trustlet_status)' <<<"$new_dmesg" || fail control_error
! grep -Eq 'luma-keymaster: .*(transport=|rejected|malformed|failed=|lookup result=|identity mismatch|status=-?[1-9])' <<<"$new_dmesg" || fail keymaster_error
[[ -z $(critical_faults <<<"$new_dmesg") ]] || fail critical_fault

printf '%s\n' '--- qcomtee focal-stock-init-v41 evidence ---'
grep -E 'qcomtee: QTEE version|luma-control:|luma-keymaster:|luma-focal:' <<<"$new_dmesg"
printf '%s\n' '--- end qcomtee focal-stock-init-v41 evidence ---'

"$stage/fp6-focal-driver-stage" cleanup
driver_prepared=false
rmmod qcomtee
qcomtee_loaded=false
printf '%s' "$old_firmware_path" >"$firmware_path"
old_firmware_path=
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
printf 'event=qcomtee_focal_v41_complete stock_sequence=true enumerate=true raw_images=0 templates_written=0 qcomtee_loaded=false focal_preloaded=%s focal_loaded_now=%s stage_present=false\n' \
  "$focal_preloaded" "$(grep -qw focaltech_fp /proc/modules && printf true || printf false)"
