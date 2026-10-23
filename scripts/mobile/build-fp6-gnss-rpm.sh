#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build one half of the pinned FP6 GNSS userspace stack on a disposable,
# native Fedora 44 AArch64 builder. This script never accesses a phone and
# never installs the resulting unsigned packages.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
component=${1:?usage: build-fp6-gnss-rpm.sh libqmi|modemmanager OUTPUT_DIR}
output_dir=${2:?usage: build-fp6-gnss-rpm.sh libqmi|modemmanager OUTPUT_DIR}
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-gnss.env"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] || die 'FP6 GNSS package build requires Linux'
[ "$(uname -m)" = aarch64 ] || die 'FP6 GNSS package build requires native AArch64'
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}:${VERSION_ID:-}" = fedora:44 ] || die 'FP6 GNSS package build requires Fedora 44'
if [ -r /sys/firmware/devicetree/base/model ]; then
  model=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$model" in *Fairphone*) die 'refuse to compile on the FP6 target' ;; esac
fi

for command in git gzip meson ninja pkg-config rpm rpmbuild sha256sum; do
  command -v "$command" >/dev/null || die "missing build command: $command"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

case "$component" in
  libqmi)
    source_repo=$FP6_GNSS_LIBQMI_REPO
    source_commit=$FP6_GNSS_LIBQMI_COMMIT
    source_version=$FP6_GNSS_LIBQMI_VERSION
    archive_name=libqmi-$source_commit.tar.gz
    archive_prefix=libqmi-$source_commit/
    spec_file=$repo_root/packaging/rpm/libqmi-fp6.spec
    patch_files=(
      "$repo_root/patches/libqmi-fp6/0003-qmicli-pdc-select-platform-or-software-load-type.patch"
    )
    ;;
  modemmanager)
    source_repo=$FP6_GNSS_MODEMMANAGER_REPO
    source_commit=$FP6_GNSS_MODEMMANAGER_COMMIT
    source_version=$FP6_GNSS_MODEMMANAGER_VERSION
    archive_name=ModemManager-$source_commit.tar.gz
    archive_prefix=ModemManager-$source_commit/
    spec_file=$repo_root/packaging/rpm/ModemManager-fp6.spec
    patch_files=(
      "$repo_root/patches/modemmanager-fp6/0001-shared-qmi-unlock-AFW-gated-GNSS-engines-at-LOC-start.patch"
      "$repo_root/patches/modemmanager-fp6/0002-shared-qmi-derive-location-from-Position-Report-indications.patch"
      "$repo_root/patches/modemmanager-fp6/0003-netlink-preserve-completion-callback-before-transaction-removal.patch"
      "$repo_root/patches/modemmanager-fp6/0004-broadband-modem-detect-5GNR-capability-from-C5GREG.patch"
      "$repo_root/patches/modemmanager-fp6/0005-iface-modem-voice-optional-idle-call-list-polling.patch"
      "$repo_root/patches/modemmanager-fp6/0006-broadband-bearer-external-packet-data-through-an-external-service.patch"
      "$repo_root/patches/modemmanager-fp6/0007-modem-helpers-accept-MediaTek-IMS-voice-calls-in-CLCC.patch"
      "$repo_root/patches/modemmanager-fp6/0008-broadband-modem-optional-CFUN-power-down.patch"
      "$repo_root/patches/modemmanager-fp6/0009-broadband-modem-ignore-access-technology-from-unregistered-domains.patch"
      "$repo_root/patches/modemmanager-fp6/0010-broadband-modem-enable-5GS-registration-reports.patch"
      "$repo_root/patches/modemmanager-fp6/0011-broadband-modem-initial-EPS-bearer-settings-through-the-external-data-service.patch"
    )
    pkg-config --atleast-version=1.39.1 qmi-glib ||
      die 'build the libqmi component and install it only in this disposable builder first'
    ;;
  *) die "unknown component: $component" ;;
esac

[ -f "$spec_file" ] || die "missing package spec: $spec_file"
for patch_file in "${patch_files[@]}"; do
  [ -f "$patch_file" ] || die "missing source patch: $patch_file"
done

install -d -m 0700 "$output_dir"
topdir=$output_dir/rpmbuild
source_tree=$output_dir/source
install -d -m 0700 "$topdir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

offline_archive=${LUMA_FP6_GNSS_PINNED_SOURCE_ARCHIVE:-}
if [ -n "$offline_archive" ]; then
  [ -f "$offline_archive" ] && [ ! -L "$offline_archive" ] || die 'pinned archive must be a regular file'
  [ "${LUMA_FP6_GNSS_PINNED_SOURCE_COMMIT:-}" = "$source_commit" ] || die 'offline source commit differs from maintained pin'
  [ "$component:$source_commit:$archive_prefix" = modemmanager:d776ea38d29ca472a12323c1d45002ee19a66f57:ModemManager-d776ea38d29ca472a12323c1d45002ee19a66f57/ ] || die 'offline archive is outside the exact maintained MM source pin'
  offline_sha=${LUMA_FP6_GNSS_PINNED_SOURCE_SHA256:-}
  [ "$offline_sha" = aa9bcdceae348c49c53db9935942dbacf3a9d4d146499b8ca6d98526d9c5f56e ] || die 'offline archive differs from independently retained pristine source'
  [[ "$offline_sha" =~ ^[0-9a-f]{64}$ ]] || die 'missing exact offline archive SHA256'
  printf '%s  %s\n' "$offline_sha" "$offline_archive" | sha256sum -c -
  install -m 0644 "$offline_archive" "$topdir/SOURCES/$archive_name"
  printf '%s  %s\n' "$offline_sha" "$topdir/SOURCES/$archive_name" | sha256sum -c -
else
  git clone --filter=blob:none --no-checkout "$source_repo" "$source_tree"
  git -C "$source_tree" checkout --detach "$source_commit"
  [ "$(git -C "$source_tree" rev-parse HEAD)" = "$source_commit" ] ||
    die 'source checkout differs from the pinned commit'
  git -C "$source_tree" archive --format=tar --prefix="$archive_prefix" "$source_commit" |
    gzip -n >"$topdir/SOURCES/$archive_name"
fi
install -m 0644 "$spec_file" "$topdir/SPECS/"
for patch_file in "${patch_files[@]}"; do
  install -m 0644 "$patch_file" "$topdir/SOURCES/"
done
if [ "$component" = modemmanager ]; then
  for owned_name in luma-introspection-org.freedesktop.ModemManager1.Sim.xml luma-meson.build luma-src-luma-owned-apdu-wire.h luma-src-luma-profile-terminal-registry-DRAFT.h luma-src-mm-base-modem.c luma-src-mm-base-modem.h luma-src-mm-sim-qmi.c luma-src-mm-sim-qmi.h luma-src-mm-iface-modem.c luma-src-mm-iface-modem.h luma-src-mm-broadband-modem-qmi.c luma-src-mm-uim-deactivation-recovery.inc luma-src-luma-native-candidate-evidence.h luma-src-luma-readonly-es10-guard.h luma-src-luma-native-operation-epoch.h luma-src-mm-shared-qmi.c luma-src-mm-base-call.c luma-src-mm-iface-modem-voice.c luma-test-native-recovery-controls.py luma-test-native-recovery-controls.c.in; do
    owned_source="$repo_root/patches/ModemManager/owned-fd/$owned_name"
    [ -f "$owned_source" ] || die 'missing maintained owned-FD source overlay'
    install -m 0644 "$owned_source" "$topdir/SOURCES/"
  done
fi

rpmbuild -ba --define "_topdir $topdir" "$topdir/SPECS/$(basename "$spec_file")"

rpm_count=$(find "$topdir/RPMS" -type f -name '*.rpm' | wc -l | tr -d ' ')
srpm_count=$(find "$topdir/SRPMS" -type f -name '*.src.rpm' | wc -l | tr -d ' ')
[ "$rpm_count" -gt 0 ] || die 'no binary RPM was produced'
[ "$srpm_count" -eq 1 ] || die 'expected exactly one source RPM'
for rpm_file in $(find "$topdir/RPMS" "$topdir/SRPMS" -type f -name '*.rpm' | sort); do
  rpm -qp --qf '%{NAME}-%{EVR}.%{ARCH}\n' "$rpm_file" >/dev/null
done

{
  printf 'LUMA_FP6_GNSS_BUILD_VERSION=1\n'
  printf 'COMPONENT=%s\n' "$component"
  printf 'SOURCE_REPOSITORY=%s\n' "$source_repo"
  printf 'SOURCE_COMMIT=%s\n' "$source_commit"
  printf 'SOURCE_VERSION=%s\n' "$source_version"
  printf 'SOURCE_ARCHIVE_SHA256=%s\n' "$(sha256sum "$topdir/SOURCES/$archive_name" | awk '{print $1}')"
  printf 'SPEC_SHA256=%s\n' "$(sha256sum "$spec_file" | awk '{print $1}')"
  for patch_file in "${patch_files[@]}"; do
    name=$(basename "$patch_file" | tr '[:lower:].-' '[:upper:]__')
    printf 'PATCH_%s_SHA256=%s\n' "$name" "$(sha256sum "$patch_file" | awk '{print $1}')"
  done
  printf 'BINARY_RPM_COUNT=%s\n' "$rpm_count"
  printf 'SOURCE_RPM_COUNT=%s\n' "$srpm_count"
  for rpm_file in $(find "$topdir/RPMS" "$topdir/SRPMS" -type f -name '*.rpm' | sort); do
    name=$(basename "$rpm_file" | tr '[:lower:].+-' '[:upper:]___')
    printf 'RPM_%s_SHA256=%s\n' "$name" "$(sha256sum "$rpm_file" | awk '{print $1}')"
  done
  printf 'RPM_SIGNATURE=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PACKAGE_INSTALLED=false\n'
  printf 'GNSS_ENABLED=false\n'
} >"$output_dir/manifest.env"
chmod 0600 "$output_dir/manifest.env"

printf 'Offline FP6 GNSS %s RPM build complete: %s\n' "$component" "$output_dir"
printf 'No phone was accessed; installation and GNSS activation remain separate gates.\n'
