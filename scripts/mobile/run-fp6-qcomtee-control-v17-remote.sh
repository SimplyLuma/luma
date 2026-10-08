#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Bounded physical gate for Linux's object-based Qualcomm TEE transport on the
# FP6. This temporarily loads only qcomtee.ko, authenticates a real root client
# environment, opens only QSEEComCompat AppLoader UID 122, and lookup/loads the
# signed non-biometric smplap64 control TA. The client never invokes the TA and
# unloads it through only the compatibility controller before all state is
# removed.

set -Eeuo pipefail
umask 077

stage=/tmp/luma-fingerprint-qcomtee-v17
expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2-luma-fp-cma1
expected_boot_size=100663296
expected_boot_sha=c6acad3a30d944d59648258eef383c33a3b51ac1148a98ef24256ee4dee61dd8
expected_config_sha=5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe
expected_installed_qsee_sha=047100c255b64b14ec1565cfc089f9bd0c5eb3a28e67b259f206bc930af39f5b
expected_installed_focal_sha=be860585db2c68ca80b3ee9e5e4929413c0d7207f9524ba489f974c46e26d051
expected_qcomtee_sha=6884c4de0e2205d7597855cf2aaa9f146743cdf7c717e77b5f23aa776c831170
expected_client_sha=ce61f7f1ae0dd7412196ec65017959f3602fed5a6cf6107e62b5fe7c304c8a3b
expected_bundle_sha=cf447217e7251b74db542cfaecaa001d0a36603dd1053c38d7298efce73a7f74

module_loaded=false
cleaned=false
baseline_dmesg_lines=0

fail() {
  printf 'event=qcomtee_control_v17_failed reason=%s\n' "$1" >&2
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
    pkill -TERM -f "^$stage/fp6-qseecompat-identity " 2>/dev/null || true
    for _ in {1..30}; do
      pgrep -f "^$stage/fp6-qseecompat-identity " >/dev/null || break
      sleep 0.1
    done
    if pgrep -f "^$stage/fp6-qseecompat-identity " >/dev/null; then
      printf 'event=qcomtee_control_v17_cleanup_error reason=client_still_running\n' >&2
      cleanup_status=1
    fi
    if [[ $module_loaded == true ]] && grep -qw qcomtee /proc/modules; then
      rmmod qcomtee || cleanup_status=1
    fi
    if grep -qw qcomtee /proc/modules; then
      printf 'event=qcomtee_control_v17_cleanup_error reason=module_still_loaded\n' >&2
      cleanup_status=1
    fi
    if [[ -d $stage && ! -L $stage && $stage == /tmp/luma-fingerprint-qcomtee-v17 ]]; then
      find "$stage" -depth -delete || cleanup_status=1
    else
      printf 'event=qcomtee_control_v17_cleanup_error reason=stage_identity\n' >&2
      cleanup_status=1
    fi
    cleaned=true
  fi
  printf 'event=qcomtee_control_v17_cleanup module_loaded=%s stage_present=%s\n' \
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
[[ $(find "$stage" -type f | wc -l) -eq 11 ]] || fail staged_file_count
[[ $(find "$stage" -type l | wc -l) -eq 0 ]] || fail staged_symlink
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == "$expected_model" ]] || fail model
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
[[ $(head -c "$expected_boot_size" /dev/disk/by-partlabel/boot_b | sha256sum | cut -d' ' -f1) == "$expected_boot_sha" ]] || fail boot_hash
runtime_config=$(gzip -dc /proc/config.gz)
[[ $(sha256sum <<<"$runtime_config" | cut -d' ' -f1) == "$expected_config_sha" ]] || fail runtime_config_hash
grep -qx 'CONFIG_QCOM_TZMEM=y' <<<"$runtime_config" || fail tzmem_config
grep -qx 'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y' <<<"$runtime_config" || fail shmbridge_config
grep -qx '# CONFIG_QCOMTEE is not set' <<<"$runtime_config" || fail qcomtee_already_in_kernel
[[ $(sha256sum /usr/lib/modules/7.1.2-luma-fp-cma1/kernel/drivers/tee/qseecom/qseecomtee.ko.zst | cut -d' ' -f1) == "$expected_installed_qsee_sha" ]] || fail installed_qsee_hash
[[ $(sha256sum /usr/lib/modules/7.1.2-luma-fp-cma1/kernel/drivers/input/finger/focal_finger/focaltech_fp.ko.zst | cut -d' ' -f1) == "$expected_installed_focal_sha" ]] || fail installed_focal_hash
[[ $(sha256sum "$stage/qcomtee.ko" | cut -d' ' -f1) == "$expected_qcomtee_sha" ]] || fail staged_module_hash
[[ $(sha256sum "$stage/fp6-qseecompat-identity" | cut -d' ' -f1) == "$expected_client_sha" ]] || fail client_hash
[[ $(modinfo -F vermagic "$stage/qcomtee.ko" | awk '{print $1}') == "$expected_kernel" ]] || fail module_vermagic
[[ $(modinfo -F sig_hashalgo "$stage/qcomtee.ko") == sha512 ]] || fail module_signature_algorithm
[[ -n $(modinfo -F signer "$stage/qcomtee.ko") ]] || fail module_unsigned

[[ $(find "$stage/firmware" -maxdepth 1 -type f -name 'smplap64.b??' | wc -l) -eq 9 ]] || fail trustlet_file_count
bundle_sha=$(
  cd "$stage/firmware"
  sha256sum \
    smplap64.b00 smplap64.b01 smplap64.b02 smplap64.b03 smplap64.b04 \
    smplap64.b05 smplap64.b06 smplap64.b07 smplap64.b08 |
    sha256sum | cut -d' ' -f1
)
[[ $bundle_sha == "$expected_bundle_sha" ]] || fail trustlet_bundle_hash
[[ $(sha256sum "$stage/firmware/smplap64.b00" | cut -d' ' -f1) == 1c35ee4fcc4fad7aaadcf678ad0c7f165f013d2274027dc790bfbdf2264a5a2c ]] || fail trustlet_b00_hash
[[ $(sha256sum "$stage/firmware/smplap64.b01" | cut -d' ' -f1) == 6405fa5e69b0abe890b4fbf824295e7221c591b2ed58e13f445ef27dbdee4b44 ]] || fail trustlet_b01_hash
[[ $(sha256sum "$stage/firmware/smplap64.b02" | cut -d' ' -f1) == a483fe0af222dbc46374bdd59d8fb7b6c2a1746e37c75a3835f77c718f882783 ]] || fail trustlet_b02_hash
[[ $(sha256sum "$stage/firmware/smplap64.b03" | cut -d' ' -f1) == 80ea8e754a61dc0b81d3c3acde600372275f133fc006cdf9cbae5753bdd3d119 ]] || fail trustlet_b03_hash
[[ $(sha256sum "$stage/firmware/smplap64.b04" | cut -d' ' -f1) == ff05708a551db5c7bf0bb3c0265f4387b33fdb24e7d3f5188fa2804ddc8bd529 ]] || fail trustlet_b04_hash
[[ $(sha256sum "$stage/firmware/smplap64.b05" | cut -d' ' -f1) == e63665052e1f8fa2d5864c9970d10390d51d44e18ed822d8688643f9e3f88952 ]] || fail trustlet_b05_hash
[[ $(sha256sum "$stage/firmware/smplap64.b06" | cut -d' ' -f1) == 37e14e7466eb8aa8c1528f309a903c120e920f9f4ab61bbb145ffb15800eea2a ]] || fail trustlet_b06_hash
[[ $(sha256sum "$stage/firmware/smplap64.b07" | cut -d' ' -f1) == 80ea8e754a61dc0b81d3c3acde600372275f133fc006cdf9cbae5753bdd3d119 ]] || fail trustlet_b07_hash
[[ $(sha256sum "$stage/firmware/smplap64.b08" | cut -d' ' -f1) == 29e4ef0e39d884e24f7ed79b19479393b8c01ea6d2501c54e5b41fc31883d265 ]] || fail trustlet_b08_hash

[[ -d /sys/bus/platform/devices/qcomtee && ! -L /sys/bus/platform/devices/qcomtee/driver ]] || fail qcomtee_platform_state
! grep -qw qcomtee /proc/modules || fail module_already_loaded
! grep -qw qseecomtee /proc/modules || fail legacy_qsee_module_loaded
! pgrep -x qsee-supplicant >/dev/null || fail legacy_supplicant_running
! pgrep -x qsee-app-loader >/dev/null || fail legacy_loader_running
! pgrep -f "^$stage/fp6-qseecompat-identity " >/dev/null || fail client_already_running
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 0 ]] || fail preexisting_tee_nodes

dmesg_before=$(dmesg)
[[ -z $(critical_faults <<<"$dmesg_before") ]] || fail preexisting_critical_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units
baseline_dmesg_lines=$(wc -l <<<"$dmesg_before")

insmod "$stage/qcomtee.ko"
module_loaded=true
grep -qw qcomtee /proc/modules || fail module_did_not_load

module_ready=false
for _ in {1..100}; do
  module_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
  if grep -Eq 'qcomtee: QTEE version [0-9]+\.[0-9]+\.[0-9]+' <<<"$module_dmesg" &&
     [[ -c /dev/tee0 ]]; then
    module_ready=true
    break
  fi
  sleep 0.1
done
[[ $module_ready == true ]] || fail module_probe_timeout
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 1 ]] || fail tee_node_count
[[ $(stat -c '%a:%U:%G' /dev/tee0) == 600:root:root ]] || fail tee_node_permissions
[[ -L /sys/bus/platform/devices/qcomtee/driver ]] || fail platform_driver_bind
new_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
[[ -z $(critical_faults <<<"$new_dmesg") ]] || fail module_critical_fault

if ! timeout --signal=TERM --kill-after=2s 15s \
  "$stage/fp6-qseecompat-identity" --lookup-only /dev/tee0 "$stage/firmware" smplap64 \
  >"$stage/lookup.log" 2>&1; then
  sed -n '1,160p' "$stage/lookup.log" >&2
  fail lookup_client
fi
grep -Fxq 'credentialed client environment accepted uid=0' "$stage/lookup.log" || fail lookup_credential
grep -Fxq 'QSEEComCompat app-loader service accepted uid=122' "$stage/lookup.log" || fail lookup_service
grep -Fxq 'lookup result=23 app=smplap64' "$stage/lookup.log" || fail lookup_result
[[ -z $(critical_faults < <(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")) ]] || fail lookup_critical_fault

if ! timeout --signal=TERM --kill-after=2s 30s \
  "$stage/fp6-qseecompat-identity" --load-control /dev/tee0 "$stage/firmware" smplap64 \
  >"$stage/load.log" 2>&1; then
  sed -n '1,200p' "$stage/load.log" >&2
  fail load_client
fi
grep -Fxq 'credentialed client environment accepted uid=0' "$stage/load.log" || fail load_credential
grep -Fxq 'QSEEComCompat app-loader service accepted uid=122' "$stage/load.log" || fail load_service
grep -Fxq 'lookup result=23 app=smplap64' "$stage/load.log" || fail load_lookup_absent
grep -Eq '^identity accepted app=smplap64 distinguished_name=.* elf_bytes=1978456$' "$stage/load.log" || fail load_identity
grep -Fxq 'control TA object returned; no TA operation invoked' "$stage/load.log" || fail ta_command_boundary
grep -Fxq 'control TA unloaded' "$stage/load.log" || fail control_unload

new_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
printf '%s\n' '--- qcomtee-v17 kernel evidence ---'
grep -Ei 'qcomtee|QTEE|SCM|TZMEM|SHM Bridge' <<<"$new_dmesg" || true
printf '%s\n' '--- qcomtee-v17 lookup evidence ---'
sed -n '1,160p' "$stage/lookup.log"
printf '%s\n' '--- qcomtee-v17 load evidence ---'
sed -n '1,200p' "$stage/load.log"
printf '%s\n' '--- end qcomtee-v17 evidence ---'
[[ -z $(critical_faults <<<"$new_dmesg") ]] || fail load_critical_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running_after_probe
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units_after_probe

rmmod qcomtee
module_loaded=false
! grep -qw qcomtee /proc/modules || fail module_did_not_unload
[[ ! -L /sys/bus/platform/devices/qcomtee/driver ]] || fail platform_did_not_unbind
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 0 ]] || fail tee_nodes_remain
find "$stage" -depth -delete
[[ ! -e $stage ]] || fail stage_cleanup
cleaned=true
trap - EXIT INT TERM HUP

post_dmesg=$(dmesg | tail -n "+$((baseline_dmesg_lines + 1))")
[[ -z $(critical_faults <<<"$post_dmesg") ]] || fail cleanup_critical_fault
printf 'event=qcomtee_control_v17_complete qtee_transport=true credential_uid=0 service_uid=122 control_app=smplap64 control_loaded=true ta_commands=0 biometric_commands=0 module_loaded=false stage_present=false\n'
