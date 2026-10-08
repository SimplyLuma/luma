#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Activate the RAM-only, root-only QTEE enrollment transport after reproducing
# the already accepted stock initialization and enumeration sequence. This
# gate deliberately issues no enroll, capture, authentication, or template
# write command. On success the staged session remains active for the separate
# userspace enrollment client; on failure every runtime change is rolled back.

set -Eeuo pipefail
umask 077

stage=/tmp/luma-fingerprint-enrollment-v52
phase=/sys/bus/platform/devices/qcomtee/luma_focal_phase
firmware_path=/sys/module/firmware_class/parameters/path
marker=luma-fingerprint-enrollment-v52-baseline
expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2-luma-fp-cma1
expected_boot_sha=c6acad3a30d944d59648258eef383c33a3b51ac1148a98ef24256ee4dee61dd8
expected_config_sha=5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe
expected_qcomtee_sha=cc9aad84c57b86b7535e4a3289b0e3b208331b59acc1d998455c29a664efbe83
expected_helper_sha=9b41c1368561a71450de764cec2b7bf1caf4925eed84025292f21582a35baccf
expected_focal_sha=3d90a639fc07d43afd04f31c6ebe313824cb5714356f6ef9de51777cb0a6cded
expected_installed_focal_sha=be860585db2c68ca80b3ee9e5e4929413c0d7207f9524ba489f974c46e26d051
expected_bundle_sha=9159f0cead5b6478b4279e0ac85d83b38f62374dad80f0a925f0cb1ad02fbdb7
expected_keymaster_sha=8acb7eec3333ba720fb7fb95ad6832567b561d41e0125386451e475ec9899198
expected_old_firmware_path=/lib/firmware/postmarketos

qcomtee_loaded=false
candidate_focal_loaded=false
original_focal_loaded=false
driver_prepared=false
old_firmware_path=
success=false

fail() { printf 'event=fp6_enrollment_v52_failed reason=%s\n' "$1" >&2; exit 1; }
critical_faults() {
  grep -Ei 'page allocation failure|WARNING: CPU|hangcheck|GMU.*(timeout|fault)|GPU.*(timeout|fault|hang)|qsee.*(fault|timeout)|qcomtee.*(fault|timeout)|TEE.*(fault|timeout)|SCM.*(fault|timeout)|BUG:|kernel panic|Oops:|Call trace:' || true
}
cleanup() {
  local status=$?
  set +e
  if [[ $success != true ]]; then
    if [[ -r $phase ]]; then
      case $(cat "$phase" 2>/dev/null) in
        secure-prepared|probe-retry|device-initialized|complete)
          printf '%s\n' abort >"$phase" 2>/dev/null || true
          ;;
      esac
    fi
    if [[ $driver_prepared == true ]]; then
      "$stage/fp6-focal-driver-stage" cleanup 2>/dev/null || true
    fi
    if [[ $qcomtee_loaded == true ]] && grep -qw qcomtee /proc/modules; then
      rmmod qcomtee 2>/dev/null || true
    fi
    if [[ -n $old_firmware_path ]]; then
      printf '%s' "$old_firmware_path" >"$firmware_path" 2>/dev/null || true
    fi
    if [[ $candidate_focal_loaded == true ]] && grep -qw focaltech_fp /proc/modules; then
      rmmod focaltech_fp 2>/dev/null || true
    fi
    if [[ $original_focal_loaded == true ]] && ! grep -qw focaltech_fp /proc/modules; then
      modprobe focaltech_fp 2>/dev/null || true
    fi
    if [[ -d $stage && ! -L $stage ]]; then
      find "$stage" -depth -delete 2>/dev/null || true
    fi
  fi
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ -d $stage && ! -L $stage ]] || fail stage_identity
[[ $(find "$stage" -type f | wc -l) -eq 14 ]] || fail staged_file_count
[[ $(find "$stage" -type l | wc -l) -eq 0 ]] || fail staged_symlink
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == "$expected_model" ]] || fail model
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
[[ $(head -c 100663296 /dev/disk/by-partlabel/boot_b | sha256sum | cut -d' ' -f1) == "$expected_boot_sha" ]] || fail boot_hash
runtime_config=$(gzip -dc /proc/config.gz)
[[ $(sha256sum <<<"$runtime_config" | cut -d' ' -f1) == "$expected_config_sha" ]] || fail runtime_config_hash
grep -qx '# CONFIG_MODULE_SIG_FORCE is not set' <<<"$runtime_config" || fail signature_policy
[[ $(sha256sum "$stage/qcomtee-enrollment.ko" | cut -d' ' -f1) == "$expected_qcomtee_sha" ]] || fail qcomtee_hash
[[ $(sha256sum "$stage/fp6-focal-driver-stage" | cut -d' ' -f1) == "$expected_helper_sha" ]] || fail helper_hash
[[ $(sha256sum "$stage/focaltech_fp.ko" | cut -d' ' -f1) == "$expected_focal_sha" ]] || fail focal_hash
[[ $(modinfo -F vermagic "$stage/qcomtee-enrollment.ko" | awk '{print $1}') == "$expected_kernel" ]] || fail qcomtee_vermagic
[[ $(modinfo -F name "$stage/qcomtee-enrollment.ko") == qcomtee ]] || fail qcomtee_name
[[ -z $(modinfo -F signer "$stage/qcomtee-enrollment.ko") ]] || fail qcomtee_signature
[[ $(find "$stage/firmware" -maxdepth 1 -type f -name 'focal64.b??' | wc -l) -eq 9 ]] || fail focal_bundle_count
bundle_sha=$(cd "$stage/firmware" && sha256sum focal64.b0{0,1,2,3,4,5,6,7,8} | sha256sum | cut -d' ' -f1)
[[ $bundle_sha == "$expected_bundle_sha" ]] || fail focal_bundle_hash
[[ $(sha256sum "$stage/firmware/keymaster64.elf" | cut -d' ' -f1) == "$expected_keymaster_sha" ]] || fail keymaster_hash

[[ -d /sys/bus/platform/devices/qcomtee && ! -L /sys/bus/platform/devices/qcomtee/driver ]] || fail platform_state
! grep -qw qcomtee /proc/modules || fail qcomtee_already_loaded
! grep -qw qseecomtee /proc/modules || fail legacy_qsee_loaded
! pgrep -x qsee-supplicant >/dev/null || fail supplicant_running
! pgrep -x qsee-app-loader >/dev/null || fail loader_running
[[ $(systemctl is-system-running) == running ]] || fail system_not_running
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units

if grep -qw focaltech_fp /proc/modules; then
  original_focal_loaded=true
  installed_focal=$(modinfo -n focaltech_fp)
  [[ $(sha256sum "$installed_focal" | cut -d' ' -f1) == "$expected_installed_focal_sha" ]] || fail installed_focal_hash
fi
old_firmware_path=$(cat "$firmware_path")
[[ $old_firmware_path == "$expected_old_firmware_path" ]] || fail firmware_path_precondition
printf '%s' "$stage/firmware" >"$firmware_path"
if [[ $original_focal_loaded == true ]]; then
  modprobe -r focaltech_fp || fail original_focal_unload
fi
insmod "$stage/focaltech_fp.ko"
candidate_focal_loaded=true
[[ -c /dev/focaltech_fp ]] || fail focal_device
printf '<6>%s\n' "$marker" >/dev/kmsg

"$stage/fp6-focal-driver-stage" prepare
driver_prepared=true
insmod "$stage/qcomtee-enrollment.ko" luma_focal_enroll_service=1
qcomtee_loaded=true
[[ -c /dev/luma-fp6-fingerprint ]] || fail enrollment_device_missing
[[ $(stat -c %a /dev/luma-fp6-fingerprint) == 600 ]] || fail enrollment_device_mode
[[ $(cat "$phase") == secure-prepared ]] || fail secure_prepare_state
"$stage/fp6-focal-driver-stage" prepare-probe
if ! printf '%s\n' probe >"$phase"; then
  [[ $(cat "$phase") == probe-retry ]] || fail physical_probe
  "$stage/fp6-focal-driver-stage" retry-probe
  printf '%s\n' probe >"$phase" || fail physical_probe_retry
fi
[[ $(cat "$phase") == device-initialized ]] || fail device_initialize_state
"$stage/fp6-focal-driver-stage" finish-probe
printf '%s\n' enumerate >"$phase" || fail enumeration_phase
[[ $(cat "$phase") == complete ]] || fail enumeration_state

new_dmesg=$(dmesg | sed -n "/$marker/,\$p")
grep -Fq 'luma-focal: root-only enrollment transport registered payload_contents_logged=0 raw_images=0' <<<"$new_dmesg" || fail transport_registration
grep -Eq 'luma-focal: enumeration accepted count=[0-9]+ raw_images=0 templates_written=0' <<<"$new_dmesg" || fail enumeration_acceptance
! grep -Eq 'luma-focal: command=0x(1013|1018|2000|2001|2008)' <<<"$new_dmesg" || fail forbidden_biometric_command
[[ -z $(critical_faults <<<"$new_dmesg") ]] || fail critical_fault

success=true
trap - EXIT INT TERM HUP
printf 'event=fp6_enrollment_v52_active slot=b kernel=%s device=/dev/luma-fp6-fingerprint mode=600 qcomtee_loaded=true candidate_focal_loaded=true driver_prepared=true raw_images=0 templates_written=0\n' "$expected_kernel"
