#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
env_file=$repo_root/config/mobile/fp6-esim.env
spec=$repo_root/packaging/rpm/lpac-fp6.spec
patch=$repo_root/patches/lpac/0001-profile-download-read-activation-code-from-stdin.patch
enable_patch=$repo_root/patches/lpac/0002-profile-enable-read-identifier-from-stdin.patch
selector_patch=$repo_root/patches/lpac/0003-profile-enable-accept-19-digit-iccid.patch
builder=$repo_root/scripts/mobile/build-fp6-esim-rpm.sh
probe=$repo_root/scripts/mobile/inspect-fp6-esim-readiness.sh
installer=$repo_root/scripts/mobile/install-fp6-esim-profile.py
pdc_patch=$repo_root/patches/libqmi-fp6/0003-qmicli-pdc-select-platform-or-software-load-type.patch
libqmi_spec=$repo_root/packaging/rpm/libqmi-fp6.spec
libqmi_builder=$repo_root/scripts/mobile/build-fp6-gnss-rpm.sh
link_adapter=$repo_root/config/mobile/fp6-physical/overlay/usr/libexec/luma-fp6-cellular-link
link_unit=$repo_root/config/mobile/fp6-physical/overlay/etc/systemd/system/luma-fp6-cellular-link.service
link_config=$repo_root/config/mobile/fp6-physical/overlay/etc/luma/fp6-cellular-link.conf
preset=$repo_root/config/mobile/systemd-preset/80-luma-handheld.preset

for file in "$env_file" "$spec" "$patch" "$enable_patch" "$selector_patch" "$builder" "$probe" "$installer" \
  "$pdc_patch" "$libqmi_spec" "$libqmi_builder"; do
  [ -f "$file" ] || { printf 'missing %s\n' "$file" >&2; exit 1; }
done
for file in "$link_adapter" "$link_unit" "$link_config" "$preset"; do
  [ -f "$file" ] || { printf 'missing %s\n' "$file" >&2; exit 1; }
done

grep -Eq '^FP6_ESIM_LPAC_COMMIT=[0-9a-f]{40}$' "$env_file"
grep -Fq 'LPAC_WITH_APDU_QMI_QRTR=ON' "$spec"
grep -Fq 'LPAC_WITH_APDU_PCSC=OFF' "$spec"
grep -Fq 'Read Activation Code from standard input' "$patch"
grep -Fq 'read_profile_id' "$enable_patch"
grep -Fq 'explicit_bzero' "$enable_patch"
grep -Fq 'length == 19 || length == 20' "$selector_patch"
grep -Fq 'Patch1:         0002-profile-enable-read-identifier-from-stdin.patch' "$spec"
grep -Fq 'Patch2:         0003-profile-enable-accept-19-digit-iccid.patch' "$spec"
grep -Fq 'FP6_ESIM_LPAC_RELEASE=3.luma1' "$env_file"
grep -Fq 'FP6_ESIM_PROFILE_SELECTOR_19_DIGIT_ACCEPTED=true' "$env_file"
grep -Fq 'r"[0-9]{19,20}"' "$installer"
grep -Eq '^FP6_ESIM_LPAC_RUNTIME_BINARY_SHA256=[0-9a-f]{64}$' "$env_file"
grep -Eq '^FP6_ESIM_LPAC_RUNTIME_RPM_SHA256=[0-9a-f]{64}$' "$env_file"
grep -Fq 'LPAC_APDU=qmi_qrtr' "$probe"
grep -Fq 'IDENTIFIERS_REDACTED=true' "$probe"
grep -Fq 'MUTATION_PERFORMED=false' "$probe"
! grep -Eq 'profile (download|enable|disable|delete)|chip purge' "$probe"
grep -Fq 'FP6_ESIM_PDC_PLATFORM_PROFILE=DSDS-LA-Milos' "$env_file"
grep -Eq '^FP6_ESIM_PDC_PLATFORM_SHA256=[0-9a-f]{64}$' "$env_file"
grep -Eq '^FP6_ESIM_PDC_PLATFORM_ID_SHA1=[0-9a-f]{40}$' "$env_file"
grep -Eq '^FP6_ESIM_PDC_QMICLI_SHA256=[0-9a-f]{64}$' "$env_file"
grep -Fq 'FP6_ESIM_PDC_PLATFORM_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_PDC_SOFTWARE_PROFILE=ROW-Commercial' "$env_file"
grep -Eq '^FP6_ESIM_PDC_SOFTWARE_SHA256=[0-9a-f]{64}$' "$env_file"
grep -Eq '^FP6_ESIM_PDC_SOFTWARE_ID_SHA1=[0-9a-f]{40}$' "$env_file"
grep -Fq 'FP6_ESIM_PDC_SOFTWARE_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_PDC_TMO_PROFILE=TMO-Commercial' "$env_file"
grep -Eq '^FP6_ESIM_PDC_TMO_SHA256=[0-9a-f]{64}$' "$env_file"
grep -Eq '^FP6_ESIM_PDC_TMO_ID_SHA1=[0-9a-f]{40}$' "$env_file"
grep -Fq 'FP6_ESIM_PDC_TMO_LOADED=true' "$env_file"
grep -Fq 'FP6_ESIM_PDC_TMO_REFRESH_PERSISTED=true' "$env_file"
grep -Fq 'FP6_ESIM_PDC_TMO_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_MODEMST_PERSISTENCE_ACCEPTED=true' "$env_file"
grep -Eq '^FP6_ESIM_MODEMST1_ACCEPTED_SHA256=[0-9a-f]{64}$' "$env_file"
grep -Eq '^FP6_ESIM_MODEMST2_ACCEPTED_SHA256=[0-9a-f]{64}$' "$env_file"
grep -Eq '^FP6_ESIM_FSG_ACCEPTED_SHA256=[0-9a-f]{64}$' "$env_file"
grep -Eq '^FP6_ESIM_RMTFS_RUNTIME_BINARY_SHA256=[0-9a-f]{64}$' "$env_file"
grep -Eq '^FP6_ESIM_RMTFS_RUNTIME_RPM_SHA256=[0-9a-f]{64}$' "$env_file"
grep -Fq 'FP6_ESIM_RMTFS_RUNTIME_INSTALLED=false' "$env_file"
grep -Fq 'FP6_ESIM_READ_ONLY_PROBE_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_PROFILE_DOWNLOAD_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_PROFILE_ENABLE_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_CARRIER_REGISTRATION_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_CELLULAR_DATA_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_CELLULAR_LINK_ADAPTER_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_CELLULAR_RECONNECT_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_SMS_INBOUND_TRANSPORT_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_SMS_OUTBOUND_TRANSPORT_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_SMS_UI_INTEGRATION_ACCEPTED=true' "$env_file"
grep -Fq 'FP6_ESIM_SMS_ACCEPTED=true' "$env_file"
grep -Fq '[(platform|software),Path to config]' "$pdc_patch"
grep -Fq 'qmicli_read_pdc_configuration_type_from_string' "$pdc_patch"
grep -Fq -- '-    g_free (file_contents);' "$pdc_patch"
grep -Fq 'Patch0: 0003-qmicli-pdc-select-platform-or-software-load-type.patch' "$libqmi_spec"
grep -Fq '%autosetup -p1' "$libqmi_spec"
grep -Fq 'Release: 0.3.luma1' "$libqmi_spec"
grep -Fq '0003-qmicli-pdc-select-platform-or-software-load-type.patch' "$libqmi_builder"
grep -Fq 'qmapmux' "$link_adapter"
grep -Fq 'metric", "700"' "$link_adapter"
grep -Fq 'ipv4.dns' "$link_adapter"
grep -Fq 'CapabilityBoundingSet=CAP_NET_ADMIN' "$link_unit"
grep -Fq 'APN=' "$link_config"
grep -Fq 'enable luma-fp6-cellular-link.service' "$preset"

bash -n "$builder" "$probe" "$libqmi_builder"
python3 -m py_compile "$link_adapter"
python3 -m py_compile "$installer"
printf 'FP6 eSIM source contract passed.\n'
