#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Independent, read-only acceptance checks for an offline FP6 rmtfs build.

set -euo pipefail
umask 077

output_dir=${1:?usage: smoke-rmtfs-fp6.sh OUTPUT_DIR}
manifest=$output_dir/manifest.env

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

for command in cpio grep rpm rpm2cpio sha256sum strings; do
  command -v "$command" >/dev/null || die "missing acceptance command: $command"
done
[ -f "$manifest" ] || die "missing build manifest: $manifest"
# shellcheck disable=SC1090
. "$manifest"

[ "$LUMA_FP6_RMTFS_BUILD_VERSION" = 2 ]
[ "$UPSTREAM_VERSION:$FEDORA_RELEASE:$ARCHITECTURE" = 1.1.1:44:aarch64 ]
[ "$UPSTREAM_COMMIT" = 27b3a6f00f121a0f8195e25c40faf97b57e2cec1 ]
[ "$MODEM_STUDY_MAPPING" = true ]
[ "$MODEMST_ONLY_PERSISTENCE" = true ]
[ "$PHONE_ACCESSED:$PACKAGE_INSTALLED:$P5_INSTALL_AUTHORIZED" = false:false:false ]
[ "$RPM_SIGNATURE" = false ]

rpm_path=$output_dir/rpmbuild/RPMS/aarch64/$RPM_FILENAME
srpm_path=$output_dir/rpmbuild/SRPMS/$SRPM_FILENAME
[ -f "$rpm_path" ] || die "missing binary RPM: $rpm_path"
[ -f "$srpm_path" ] || die "missing source RPM: $srpm_path"
[ "$RPM_BYTES" -eq "$(stat -c '%s' "$rpm_path")" ]
[ "$SRPM_BYTES" -eq "$(stat -c '%s' "$srpm_path")" ]
[ "$RPM_SHA256" = "$(sha256sum "$rpm_path" | awk '{print $1}')" ]
[ "$SRPM_SHA256" = "$(sha256sum "$srpm_path" | awk '{print $1}')" ]

[ "$(rpm -qp --qf '%{NAME}-%{EVR}.%{ARCH}' "$rpm_path")" = \
  rmtfs-1.1.1-3.luma.1.fc44.aarch64 ]
[ "$(rpm -qp --qf '%{LICENSE}' "$rpm_path")" = BSD-3-Clause ]
[ "$(rpm -qp --qf '%{SIGPGP:pgpsig}|%{SIGGPG:pgpsig}' "$rpm_path")" = '(none)|(none)' ]
[ "$(rpm --eval '%{lua:print(rpm.vercmp("1.1.1-3.luma.1.fc44", "1.1.1-2.luma.1.fc44"))}')" -gt 0 ]

temporary=$(mktemp -d "$output_dir/.smoke.XXXXXX")
trap 'rm -rf "$temporary"' EXIT
(cd "$temporary" && rpm2cpio "$rpm_path" | cpio -idm --quiet)
strings "$temporary/usr/bin/rmtfs" | grep -Fqx '/boot/modem_study'
strings "$temporary/usr/bin/rmtfs" | grep -Fqx -- '-W requires -r -P and forbids -o'
[ "$RUNTIME_BINARY_SHA256" = "$(sha256sum "$temporary/usr/bin/rmtfs" | awk '{print $1}')" ]
grep -Fqx 'ExecStart=/usr/bin/rmtfs -r -P -s' \
  "$temporary/usr/lib/systemd/system/rmtfs.service"
! grep -Eq 'ExecStart=.*[[:space:]]-W([[:space:]]|$)' \
  "$temporary/usr/lib/systemd/system/rmtfs.service"
rpm -qpl "$rpm_path" | grep -Fqx /usr/bin/rmtfs

printf 'Fedora FP6 rmtfs offline package smoke test: PASS\n'
printf 'Unsigned evidence only; this result does not authorize installation.\n'
