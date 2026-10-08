#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-gnss.env"

[ "$FP6_GNSS_LIBQMI_COMMIT" = 30f3e998e6cbda364ac1bc73223de20561e6d555 ]
[ "$FP6_GNSS_LIBQMI_VERSION" = 1.39.1 ]
[ "$FP6_GNSS_MODEMMANAGER_COMMIT" = d776ea38d29ca472a12323c1d45002ee19a66f57 ]
[ "$FP6_GNSS_MODEMMANAGER_VERSION" = 1.25.95 ]
[ "$FP6_GNSS_PHYSICAL_ACCEPTED" = false ]
[ "$FP6_GNSS_PACKAGE_INSTALL_AUTHORIZED" = false ]
[ "$FP6_GNSS_LIVE_PACKAGE_STATE" = installed-diagnostic ]
[ "$FP6_GNSS_ROLLBACK_STAGED" = true ]
[ "$FP6_GNSS_CAPABILITY_ACCEPTED" = true ]
[ "$FP6_GNSS_NMEA_STREAM_ACCEPTED" = true ]
[ "$FP6_GNSS_COORDINATE_FIX_ACCEPTED" = false ]
[ "$FP6_GNSS_IMAGE_INCLUSION_ACCEPTED" = false ]

acceptor=$repo_root/scripts/mobile/accept-fp6-gnss-fix.sh
bash -n "$acceptor"
grep -Fq 'COORDINATES_PRINTED=false' "$acceptor"
grep -Fq 'COORDINATES_PERSISTED=false' "$acceptor"
grep -Fq -- "--location-disable-gps-raw" "$acceptor"
grep -Fq 'LUMA_FP6_GNSS_SLEEP_INHIBITED' "$acceptor"
grep -Fq 'PERSISTENT_MUTATIONS=none' "$acceptor"
if grep -Eq '(fastboot|flash|erase|wipe|reboot|set[_-]active)' "$acceptor"; then
  printf 'error: out-of-scope system mutation found in GNSS acceptor\n' >&2
  exit 1
fi

grep -Fq 'QMI_LOC_EVENT_REGISTRATION_FLAG_POSITION_REPORT' \
  "$repo_root/patches/modemmanager-fp6/0002-shared-qmi-derive-location-from-Position-Report-indications.patch"
grep -Fq 'QMI_LOC_CLIENT_TYPE_AFW' \
  "$repo_root/patches/modemmanager-fp6/0001-shared-qmi-unlock-AFW-gated-GNSS-engines-at-LOC-start.patch"
grep -Fq 'Client Type' \
  "$repo_root/patches/libqmi-fp6/0001-loc-add-client-identification-TLVs-to-Register-Events.patch"
grep -Fq 'BuildRequires: libqmi-devel >= 1.39.1' \
  "$repo_root/packaging/rpm/ModemManager-fp6.spec"
grep -Fq 'BuildRequires: pkgconfig(gi-docgen) >= 2021.1' \
  "$repo_root/packaging/rpm/libqmi-fp6.spec"
grep -Fq '%{_datadir}/doc/libqmi-glib-*/' \
  "$repo_root/packaging/rpm/libqmi-fp6.spec"
grep -Fq 'PHONE_ACCESSED=false' "$repo_root/scripts/mobile/build-fp6-gnss-rpm.sh"
grep -Fq 'GNSS_ENABLED=false' "$repo_root/scripts/mobile/build-fp6-gnss-rpm.sh"
grep -Fq 'FP6 GNSS RPM bundle verification: PASS' \
  "$repo_root/scripts/mobile/verify-fp6-gnss-rpm-bundle.sh"
grep -Fq 'packages_installed=false' \
  "$repo_root/scripts/mobile/verify-fp6-gnss-rpm-bundle.sh"

if grep -Eq '(^|[[:space:]])(fastboot|flash|erase|wipe|reboot|set[_-]active)([[:space:]]|$)' \
  "$repo_root/scripts/mobile/build-fp6-gnss-rpm.sh"; then
  printf 'error: phone mutation token found in offline GNSS builder\n' >&2
  exit 1
fi

printf 'Mobile FP6 GNSS source and package contract: PASS\n'
