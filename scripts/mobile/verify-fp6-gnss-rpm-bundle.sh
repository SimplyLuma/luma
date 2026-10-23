#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Verify the complete, unsigned FP6 GNSS RPM bundle without installing it,
# starting ModemManager, enabling a location source, or accessing a phone.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
bundle_root=${1:?usage: verify-fp6-gnss-rpm-bundle.sh BUNDLE_ROOT}
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-gnss.env"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

for command in basename cut find grep rpm sha256sum sort tr; do
  command -v "$command" >/dev/null 2>&1 || die "missing verification command: $command"
done

verify_component() {
  local component=$1 version=$2 commit=$3 binary_count=$4 spec=$5
  local component_root=$bundle_root/$component manifest=$bundle_root/$component/manifest.env
  local rpm_file rpm_name key expected_hash actual_hash package_name package_version package_arch

  [ -f "$manifest" ] || die "$component manifest is missing"
  if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._:/+-]+$' "$manifest"; then
    die "$component manifest contains unexpected syntax"
  fi
  # shellcheck disable=SC1090
  . "$manifest"
  [ "${LUMA_FP6_GNSS_BUILD_VERSION:-}" = 1 ] || die "$component bundle version differs"
  [ "${COMPONENT:-}" = "$component" ] || die "$component identity differs"
  [ "${SOURCE_COMMIT:-}" = "$commit" ] || die "$component source commit differs"
  [ "${SOURCE_VERSION:-}" = "$version" ] || die "$component source version differs"
  [ "${BINARY_RPM_COUNT:-}" = "$binary_count" ] || die "$component binary RPM count differs"
  [ "${SOURCE_RPM_COUNT:-}" = 1 ] || die "$component source RPM count differs"
  [ "${SPEC_SHA256:-}" = "$(hash "$spec")" ] || die "$component spec hash differs"
  [ "${RPM_SIGNATURE:-}" = false ] || die "$component signature state differs"
  [ "${PHONE_ACCESSED:-}" = false ] || die "$component build accessed a phone"
  [ "${PACKAGE_INSTALLED:-}" = false ] || die "$component build installed its output"
  [ "${GNSS_ENABLED:-}" = false ] || die "$component build enabled GNSS"
  [ "$(find "$component_root/RPMS" -type f -name '*.rpm' | wc -l | tr -d ' ')" = "$binary_count" ] ||
    die "$component binary RPM payload count differs"
  [ "$(find "$component_root/SRPMS" -type f -name '*.src.rpm' | wc -l | tr -d ' ')" = 1 ] ||
    die "$component source RPM payload count differs"

  while IFS= read -r rpm_file; do
    rpm_name=$(basename "$rpm_file")
    key=RPM_$(printf '%s' "$rpm_name" | tr '[:lower:].+-' '[:upper:]___')_SHA256
    expected_hash=${!key:-}
    [ -n "$expected_hash" ] || die "$component manifest has no hash for $rpm_name"
    actual_hash=$(hash "$rpm_file")
    [ "$actual_hash" = "$expected_hash" ] || die "$component hash differs for $rpm_name"
    read -r package_name package_version package_arch < <(
      rpm -qp --qf '%{NAME} %{VERSION} %{ARCH}\n' "$rpm_file"
    )
    [ "$package_version" = "$version" ] || die "$component version differs in $rpm_name"
    case "$rpm_file:$package_arch" in
      # RPM records the build target (aarch64) in this source package's Arch
      # header even though the source artifact filename correctly ends in
      # .src.rpm.
      */SRPMS/*.src.rpm:aarch64) ;;
      */RPMS/*.rpm:aarch64) ;;
      *) die "$component architecture differs in $rpm_name" ;;
    esac
    case "$component:$package_name" in
      libqmi:libqmi|libqmi:libqmi-*) ;;
      modemmanager:ModemManager|modemmanager:ModemManager-*) ;;
      *) die "$component package identity differs in $rpm_name" ;;
    esac
  done < <(find "$component_root/RPMS" "$component_root/SRPMS" -type f -name '*.rpm' | sort)
}

verify_component libqmi "$FP6_GNSS_LIBQMI_VERSION" "$FP6_GNSS_LIBQMI_COMMIT" 6 \
  "$repo_root/packaging/rpm/libqmi-fp6.spec"
[ "${SOURCE_REPOSITORY:-}" = "$FP6_GNSS_LIBQMI_REPO" ] || die 'libqmi repository differs'

verify_component modemmanager "$FP6_GNSS_MODEMMANAGER_VERSION" \
  "$FP6_GNSS_MODEMMANAGER_COMMIT" 8 "$repo_root/packaging/rpm/ModemManager-fp6.spec"
[ "${SOURCE_REPOSITORY:-}" = "$FP6_GNSS_MODEMMANAGER_REPO" ] || die 'ModemManager repository differs'
[ "${PATCH_0001_SHARED_QMI_UNLOCK_AFW_GATED_GNSS_ENGINES_AT_LOC_START_PATCH_SHA256:-}" = \
  "$(hash "$repo_root/patches/modemmanager-fp6/0001-shared-qmi-unlock-AFW-gated-GNSS-engines-at-LOC-start.patch")" ] ||
  die 'ModemManager AFW patch hash differs'
[ "${PATCH_0002_SHARED_QMI_DERIVE_LOCATION_FROM_POSITION_REPORT_INDICATIONS_PATCH_SHA256:-}" = \
  "$(hash "$repo_root/patches/modemmanager-fp6/0002-shared-qmi-derive-location-from-Position-Report-indications.patch")" ] ||
  die 'ModemManager position-report patch hash differs'
[ "${PATCH_0003_NETLINK_PRESERVE_COMPLETION_CALLBACK_BEFORE_TRANSACTION_REMOVAL_PATCH_SHA256:-}" = \
  "$(hash "$repo_root/patches/modemmanager-fp6/0003-netlink-preserve-completion-callback-before-transaction-removal.patch")" ] ||
  die 'ModemManager netlink transaction patch hash differs'
[ "${PATCH_0004_BROADBAND_MODEM_DETECT_5GNR_CAPABILITY_FROM_C5GREG_PATCH_SHA256:-}" = \
  "$(hash "$repo_root/patches/modemmanager-fp6/0004-broadband-modem-detect-5GNR-capability-from-C5GREG.patch")" ] ||
  die 'ModemManager 5GNR capability patch hash differs'
[ "${PATCH_0005_IFACE_MODEM_VOICE_OPTIONAL_IDLE_CALL_LIST_POLLING_PATCH_SHA256:-}" = \
  "$(hash "$repo_root/patches/modemmanager-fp6/0005-iface-modem-voice-optional-idle-call-list-polling.patch")" ] ||
  die 'ModemManager idle call list patch hash differs'
[ "${PATCH_0006_BROADBAND_BEARER_EXTERNAL_PACKET_DATA_THROUGH_AN_EXTERNAL_SERVICE_PATCH_SHA256:-}" = \
  "$(hash "$repo_root/patches/modemmanager-fp6/0006-broadband-bearer-external-packet-data-through-an-external-service.patch")" ] ||
  die 'ModemManager external data bearer patch hash differs'
[ "${PATCH_0007_MODEM_HELPERS_ACCEPT_MEDIATEK_IMS_VOICE_CALLS_IN_CLCC_PATCH_SHA256:-}" = \
  "$(hash "$repo_root/patches/modemmanager-fp6/0007-modem-helpers-accept-MediaTek-IMS-voice-calls-in-CLCC.patch")" ] ||
  die 'ModemManager MediaTek CLCC patch hash differs'
[ "${PATCH_0008_BROADBAND_MODEM_OPTIONAL_CFUN_POWER_DOWN_PATCH_SHA256:-}" = \
  "$(hash "$repo_root/patches/modemmanager-fp6/0008-broadband-modem-optional-CFUN-power-down.patch")" ] ||
  die 'ModemManager CFUN power down patch hash differs'
[ "${PATCH_0009_BROADBAND_MODEM_IGNORE_ACCESS_TECHNOLOGY_FROM_UNREGISTERED_DOMAINS_PATCH_SHA256:-}" = \
  "$(hash "$repo_root/patches/modemmanager-fp6/0009-broadband-modem-ignore-access-technology-from-unregistered-domains.patch")" ] ||
  die 'ModemManager access technology patch hash differs'
[ "${PATCH_0010_BROADBAND_MODEM_ENABLE_5GS_REGISTRATION_REPORTS_PATCH_SHA256:-}" = \
  "$(hash "$repo_root/patches/modemmanager-fp6/0010-broadband-modem-enable-5GS-registration-reports.patch")" ] ||
  die 'ModemManager 5GS registration reports patch hash differs'
[ "${PATCH_0011_BROADBAND_MODEM_INITIAL_EPS_BEARER_SETTINGS_THROUGH_THE_EXTERNAL_DATA_SERVICE_PATCH_SHA256:-}" = \
  "$(hash "$repo_root/patches/modemmanager-fp6/0011-broadband-modem-initial-EPS-bearer-settings-through-the-external-data-service.patch")" ] ||
  die 'ModemManager external initial EPS bearer patch hash differs'

printf 'FP6 GNSS RPM bundle verification: PASS\n'
printf 'packages_installed=false\ngnss_enabled=false\nphone_accessed=false\n'
