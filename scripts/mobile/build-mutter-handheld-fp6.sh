#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build the narrow Luma handheld scale-policy patch from Fedora's exact Mutter
# source RPM on a native Fedora 44 AArch64 builder. This script does not install
# the resulting packages or access partitions, slots, or the bootloader.

set -euo pipefail
umask 077

source_rpm=${1:?usage: build-mutter-handheld-fp6.sh SOURCE_RPM PATCH OUTPUT_DIR}
patch_file=${2:?usage: build-mutter-handheld-fp6.sh SOURCE_RPM PATCH OUTPUT_DIR}
output_dir=${3:?usage: build-mutter-handheld-fp6.sh SOURCE_RPM PATCH OUTPUT_DIR}
source_sha256=e11d5b0cb6d7736b10bbbdc6867037dc2a32783e0576f38a11563f1a47b9d3e2

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] || die 'Mutter handheld build requires Linux'
[ "$(uname -m)" = aarch64 ] || die 'Mutter handheld build requires native AArch64'
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}:${VERSION_ID:-}" = fedora:44 ] || die 'Mutter handheld build requires Fedora 44'

for command in cpio git grep rpm rpm2cpio rpmbuild sed sha256sum strings; do
  command -v "$command" >/dev/null || die "missing build command: $command"
done
printf '%s  %s\n' "$source_sha256" "$source_rpm" |
  sha256sum --check --status || die 'Fedora Mutter source RPM checksum mismatch'
[ -f "$patch_file" ] || die 'missing Luma Mutter patch'
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

install -d -m 0700 "$output_dir"
topdir=$output_dir/rpmbuild
extract=$output_dir/source-rpm
install -d -m 0700 "$extract" "$topdir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
(cd "$extract" && rpm2cpio "$source_rpm" | cpio -idm --quiet)
mv "$extract/mutter.spec" "$topdir/SPECS/"
find "$extract" -maxdepth 1 -type f -exec mv -t "$topdir/SOURCES" {} +
install -m 0644 "$patch_file" "$topdir/SOURCES/0001-luma-handheld-scales.patch"

sed -i \
  -e 's/^Release:.*/Release:       1.luma.1%{?dist}/' \
  -e '/^Source0:/a Patch900:     0001-luma-handheld-scales.patch' \
  "$topdir/SPECS/mutter.spec"

grep -Fqx 'Patch900: 0001-luma-handheld-scales.patch' "$topdir/SPECS/mutter.spec"
grep -Fqx 'Release:       1.luma.1%{?dist}' "$topdir/SPECS/mutter.spec"

rpmbuild -ba --define "_topdir $topdir" "$topdir/SPECS/mutter.spec"

rpm_path=$(find "$topdir/RPMS/aarch64" -maxdepth 1 -type f \
  -name 'mutter-50.4-1.luma.1.fc44.aarch64.rpm' -print -quit)
[ -n "$rpm_path" ] || die 'expected AArch64 Mutter RPM was not produced'
[ "$(rpm -qp --qf '%{NAME}-%{EVR}.%{ARCH}' "$rpm_path")" = \
  mutter-50.4-1.luma.1.fc44.aarch64 ] || die 'unexpected Mutter NEVRA'

payload=$output_dir/payload
install -d -m 0700 "$payload"
rpm2cpio "$rpm_path" | (cd "$payload" && cpio -idm --quiet)
strings "$payload/usr/lib64/libmutter-18.so.0.0.0" |
  grep -Fqx LUMA_DEVICE_CLASS || die 'handheld policy marker missing from runtime'

find "$topdir/RPMS/aarch64" "$topdir/SRPMS" -maxdepth 1 -type f \
  -name '*.rpm' -exec sha256sum {} + | sort >"$output_dir/SHA256SUMS"
printf 'Luma AArch64 Mutter packages: %s\n' "$topdir/RPMS/aarch64"
printf 'No package was installed.\n'
