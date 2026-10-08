#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Register exactly the stock QSEE FS/GPFS listeners, then run the bounded
# kernel-privileged QSEEComCompat smplap64 identity control. No TA application
# object is obtained or invoked and no biometric command exists in this path.

set -Eeuo pipefail
umask 077

stage=/tmp/luma-fingerprint-qcomtee-listeners-v19
firmware_path=/sys/module/firmware_class/parameters/path
expected_old_firmware_path=/lib/firmware/postmarketos
expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2-luma-fp-cma1
expected_boot_sha=c6acad3a30d944d59648258eef383c33a3b51ac1148a98ef24256ee4dee61dd8
expected_config_sha=5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe
expected_installed_qsee_sha=047100c255b64b14ec1565cfc089f9bd0c5eb3a28e67b259f206bc930af39f5b
expected_installed_focal_sha=be860585db2c68ca80b3ee9e5e4929413c0d7207f9524ba489f974c46e26d051
expected_qsee_module_sha=1f74052412d9fc34eb581cb7aac0df80d949ca2c6da9bd6e9e70dc2fa68415df
expected_qcomtee_module_sha=f4e7699f6a4cfe9aa075600c2c61e43cac3ba7a8f3c28629d82ec2ead37dc675
expected_supplicant_sha=dd355adb58b1775752cc2ff1172b5e4ec2f5914429a9d0efbe7e8bd871e6085e
expected_bundle_sha=cf447217e7251b74db542cfaecaa001d0a36603dd1053c38d7298efce73a7f74
marker=luma-qcomtee-listeners-v19-baseline

qsee_loaded=false
qcomtee_loaded=false
supplicant_pid=
old_firmware_path=
cleaned=false

fail() {
  printf 'event=qcomtee_listeners_v19_failed reason=%s\n' "$1" >&2
  exit 1
}

critical_faults() {
  grep -Ei \
    'page allocation failure|allocation failed|WARNING: CPU|hangcheck|GMU.*(timeout|fault)|GPU.*(timeout|fault|hang)|qsee.*(fault|timeout)|qcomtee.*(fault|timeout)|TEE.*(fault|timeout)|SCM.*(fault|timeout)|BUG:|kernel panic|Oops:|Call trace:' \
    || true
}

stop_supplicant() {
  [[ -n $supplicant_pid ]] || return 0
  if kill -0 "$supplicant_pid" 2>/dev/null; then
    kill -TERM "$supplicant_pid"
    for _ in {1..50}; do
      kill -0 "$supplicant_pid" 2>/dev/null || break
      sleep 0.1
    done
  fi
  kill -0 "$supplicant_pid" 2>/dev/null && return 1
  wait "$supplicant_pid" 2>/dev/null || true
}

cleanup() {
  local status=$?
  local cleanup_status=0
  set +e
  if [[ $cleaned != true ]]; then
    stop_supplicant || cleanup_status=1
    if [[ -n $old_firmware_path && -w $firmware_path ]]; then
      printf '%s' "$old_firmware_path" >"$firmware_path" || cleanup_status=1
    fi
    if [[ $qcomtee_loaded == true ]] && grep -qw qcomtee /proc/modules; then
      rmmod qcomtee || cleanup_status=1
    fi
    if [[ $qsee_loaded == true ]] && grep -qw qseecomtee /proc/modules; then
      rmmod qseecomtee || cleanup_status=1
    fi
    if grep -Eq '(^| )(qcomtee|qseecomtee) ' /proc/modules; then
      printf 'event=qcomtee_listeners_v19_cleanup_error reason=module_still_loaded\n' >&2
      cleanup_status=1
    fi
    if [[ -d $stage && ! -L $stage && $stage == /tmp/luma-fingerprint-qcomtee-listeners-v19 ]]; then
      find "$stage" -depth -delete || cleanup_status=1
    else
      printf 'event=qcomtee_listeners_v19_cleanup_error reason=stage_identity\n' >&2
      cleanup_status=1
    fi
    cleaned=true
  fi
  printf 'event=qcomtee_listeners_v19_cleanup firmware_path=%s qsee_loaded=%s qcomtee_loaded=%s stage_present=%s\n' \
    "$(cat "$firmware_path" 2>/dev/null || printf unavailable)" \
    "$(grep -qw qseecomtee /proc/modules && printf true || printf false)" \
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
[[ $(find "$stage" -type f | wc -l) -eq 12 ]] || fail staged_file_count
[[ $(find "$stage" -type l | wc -l) -eq 0 ]] || fail staged_symlink
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == "$expected_model" ]] || fail model
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
[[ $(head -c 100663296 /dev/disk/by-partlabel/boot_b | sha256sum | cut -d' ' -f1) == "$expected_boot_sha" ]] || fail boot_hash
runtime_config=$(gzip -dc /proc/config.gz)
[[ $(sha256sum <<<"$runtime_config" | cut -d' ' -f1) == "$expected_config_sha" ]] || fail runtime_config_hash
grep -qx 'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y' <<<"$runtime_config" || fail shmbridge_config
[[ $(sha256sum /usr/lib/modules/7.1.2-luma-fp-cma1/kernel/drivers/tee/qseecom/qseecomtee.ko.zst | cut -d' ' -f1) == "$expected_installed_qsee_sha" ]] || fail installed_qsee_hash
[[ $(sha256sum /usr/lib/modules/7.1.2-luma-fp-cma1/kernel/drivers/input/finger/focal_finger/focaltech_fp.ko.zst | cut -d' ' -f1) == "$expected_installed_focal_sha" ]] || fail installed_focal_hash
[[ $(sha256sum "$stage/qseecomtee.ko" | cut -d' ' -f1) == "$expected_qsee_module_sha" ]] || fail qsee_module_hash
[[ $(sha256sum "$stage/qcomtee-control.ko" | cut -d' ' -f1) == "$expected_qcomtee_module_sha" ]] || fail qcomtee_module_hash
[[ $(sha256sum "$stage/qsee-supplicant" | cut -d' ' -f1) == "$expected_supplicant_sha" ]] || fail supplicant_hash
for module in "$stage/qseecomtee.ko" "$stage/qcomtee-control.ko"; do
  [[ $(modinfo -F vermagic "$module" | awk '{print $1}') == "$expected_kernel" ]] || fail module_vermagic
  [[ -z $(modinfo -F depends "$module") ]] || fail module_dependencies
  [[ $(modinfo -F signer "$module") == 'Build time autogenerated kernel key' ]] || fail module_signer
  [[ $(modinfo -F sig_hashalgo "$module") == sha512 ]] || fail module_signature
done

[[ $(find "$stage/firmware" -maxdepth 1 -type f -name 'smplap64.b??' | wc -l) -eq 9 ]] || fail trustlet_file_count
bundle_sha=$(
  cd "$stage/firmware"
  sha256sum smplap64.b00 smplap64.b01 smplap64.b02 smplap64.b03 \
    smplap64.b04 smplap64.b05 smplap64.b06 smplap64.b07 smplap64.b08 |
    sha256sum | cut -d' ' -f1
)
[[ $bundle_sha == "$expected_bundle_sha" ]] || fail trustlet_bundle_hash

[[ -d /sys/bus/platform/devices/qcomtee && ! -L /sys/bus/platform/devices/qcomtee/driver ]] || fail qcomtee_platform_state
! grep -qw qcomtee /proc/modules || fail qcomtee_already_loaded
! grep -qw qseecomtee /proc/modules || fail qsee_already_loaded
! pgrep -x qsee-supplicant >/dev/null || fail supplicant_already_running
! pgrep -x qsee-app-loader >/dev/null || fail loader_already_running
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 0 ]] || fail preexisting_tee_nodes
dmesg_before=$(dmesg)
[[ -z $(critical_faults <<<"$dmesg_before") ]] || fail preexisting_critical_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units

old_firmware_path=$(cat "$firmware_path")
[[ $old_firmware_path == "$expected_old_firmware_path" ]] || fail firmware_path_precondition
printf '%s' "$stage/firmware" >"$firmware_path"
[[ $(cat "$firmware_path") == "$stage/firmware" ]] || fail firmware_path_stage
printf '<6>%s\n' "$marker" >/dev/kmsg

insmod "$stage/qseecomtee.ko"
qsee_loaded=true
grep -qw qseecomtee /proc/modules || fail qsee_module_did_not_load
[[ -c /dev/tee0 && -c /dev/teepriv0 ]] || fail legacy_tee_nodes

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

insmod "$stage/qcomtee-control.ko" luma_control_identity=1
qcomtee_loaded=true
grep -qw qcomtee /proc/modules || fail qcomtee_module_did_not_load
[[ -c /dev/tee1 ]] || fail qcomtee_node_missing

new_dmesg=$(dmesg | sed -n "/$marker/,\$p")
grep -Eq 'qcomtee: QTEE version [0-9]+\.[0-9]+\.[0-9]+' <<<"$new_dmesg" || fail qtee_version
grep -Fq 'luma-control: privileged client environment accepted' <<<"$new_dmesg" || fail privileged_environment
grep -Fq 'luma-control: service uid=122 accepted' <<<"$new_dmesg" || fail compatibility_service
grep -Fq 'luma-control: lookup result=23 app=smplap64' <<<"$new_dmesg" || fail lookup_result
grep -Eq 'luma-control: identity accepted app=smplap64 distinguished_name=.* elf_bytes=1978456; no TA operation invoked' <<<"$new_dmesg" || fail identity_acceptance
grep -Fq 'luma-control: control TA unloaded' <<<"$new_dmesg" || fail control_unload
grep -Fq 'luma-control: complete ta_commands=0 biometric_commands=0' <<<"$new_dmesg" || fail control_complete
[[ $(grep -c 'luma-control: identity accepted ' <<<"$new_dmesg") -eq 1 ]] || fail identity_count
! grep -Eq 'luma-control: (.*failed|.*rejected|app unexpectedly preloaded|lookup transport=|reconstruction failed=|load transport=|unload transport=)' <<<"$new_dmesg" || fail control_error
[[ -z $(critical_faults <<<"$new_dmesg") ]] || fail critical_kernel_fault
[[ $(systemctl is-system-running) == running ]] || fail system_not_running_after_probe
[[ $(systemctl --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l) -eq 0 ]] || fail failed_units_after_probe

printf '%s\n' '--- qcomtee listeners-control-v19 evidence ---'
sed -n '1,120p' "$stage/supplicant.log"
grep -E 'qcomtee: QTEE version|luma-control:' <<<"$new_dmesg"
printf '%s\n' '--- end qcomtee listeners-control-v19 evidence ---'

stop_supplicant
supplicant_pid=
printf '%s' "$old_firmware_path" >"$firmware_path"
old_firmware_path=
rmmod qcomtee
qcomtee_loaded=false
rmmod qseecomtee
qsee_loaded=false
! grep -Eq '(^| )(qcomtee|qseecomtee) ' /proc/modules || fail module_cleanup
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 0 ]] || fail tee_nodes_remain
find "$stage" -depth -delete
[[ ! -e $stage ]] || fail stage_cleanup
cleaned=true
trap - EXIT INT TERM HUP

post_dmesg=$(dmesg | sed -n "/$marker/,\$p")
[[ -z $(critical_faults <<<"$post_dmesg") ]] || fail cleanup_critical_fault
printf 'event=qcomtee_listeners_v19_complete listeners=10,28672 privileged_kernel_client=true service_uid=122 control_app=smplap64 identity_accepted=true control_unloaded=true ta_commands=0 biometric_commands=0 modules_loaded=false stage_present=false\n'
