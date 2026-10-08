#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build the minimal FP6 rmtfs backport on a native Fedora 44 AArch64 builder.
# This script never accesses a phone or installs the resulting package.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output_dir=${1:-$repo_root/build/mobile/fp6-physical/rmtfs-fp6}
source_sha512=15b8dd0e0b2105e331feb7632c44e213566b1feb42c5bef7fe342e96a948fc6fe1e16c767fb67d726a771088a04f4b17c8fd813c469bf6c289beb2b9d11627d8
source_url=https://github.com/linux-msm/rmtfs/archive/v1.1.1/rmtfs-1.1.1.tar.gz
source_archive=${RMTFS_SOURCE_ARCHIVE:-}
patch_file=$repo_root/patches/rmtfs/0001-storage-add-fp6-modem-study.patch
runtime_patch=$repo_root/patches/rmtfs/0003-fp6-persist-modemst-only.patch
spec_file=$repo_root/packaging/rpm/rmtfs-fp6.spec

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] || die 'rmtfs FP6 build requires Linux'
[ "$(uname -m)" = aarch64 ] || die 'rmtfs FP6 build requires native AArch64'
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}:${VERSION_ID:-}" = fedora:44 ] || die 'rmtfs FP6 build requires Fedora 44'

for command in cpio curl gcc make rpm rpm2cpio rpmbuild sha256sum sha512sum strings; do
  command -v "$command" >/dev/null || die "missing build command: $command"
done
for package in qrtr-devel systemd-devel systemd-rpm-macros; do
  rpm -q "$package" >/dev/null || die "missing build dependency: $package"
done
[ -f "$patch_file" ] || die "missing source patch: $patch_file"
[ -f "$runtime_patch" ] || die "missing runtime patch: $runtime_patch"
[ -f "$spec_file" ] || die "missing package spec: $spec_file"
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

install -d -m 0700 "$output_dir"
topdir=$output_dir/rpmbuild
install -d -m 0700 "$topdir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
fixed_build_root=$repo_root/.rmtfs-build-fixed
[ ! -e "$fixed_build_root" ] || die "fixed build path is already in use: $fixed_build_root"
install -d -m 0700 "$fixed_build_root/BUILD" "$fixed_build_root/BUILDROOT"
cleanup() {
  rm -rf -- "$fixed_build_root"
}
trap cleanup EXIT

if [ -n "$source_archive" ]; then
  [ -f "$source_archive" ] || die "missing supplied source archive: $source_archive"
  install -m 0644 "$source_archive" "$topdir/SOURCES/rmtfs-1.1.1.tar.gz"
else
  curl -L --fail --silent --show-error -o "$topdir/SOURCES/rmtfs-1.1.1.tar.gz" "$source_url"
fi
printf '%s  %s\n' "$source_sha512" "$topdir/SOURCES/rmtfs-1.1.1.tar.gz" |
  sha512sum --check --status || die 'upstream rmtfs source checksum mismatch'
install -m 0644 "$patch_file" "$topdir/SOURCES/"
install -m 0644 "$runtime_patch" "$topdir/SOURCES/"
install -m 0644 "$spec_file" "$topdir/SPECS/"

rpmbuild -ba \
  --define "_topdir $topdir" \
  --define "_builddir $fixed_build_root/BUILD" \
  --define "_buildrootdir $fixed_build_root/BUILDROOT" \
  "$topdir/SPECS/rmtfs-fp6.spec"

rpm_path=$(find "$topdir/RPMS/aarch64" -maxdepth 1 -type f -name 'rmtfs-1.1.1-3.luma.1.fc44.aarch64.rpm' -print -quit)
srpm_path=$(find "$topdir/SRPMS" -maxdepth 1 -type f -name 'rmtfs-1.1.1-3.luma.1.fc44.src.rpm' -print -quit)
[ -n "$rpm_path" ] || die 'expected AArch64 RPM was not produced'
[ -n "$srpm_path" ] || die 'expected source RPM was not produced'

rpm -qp --qf '%{NAME}-%{EVR}.%{ARCH}\n' "$rpm_path" |
  grep -Fxq 'rmtfs-1.1.1-3.luma.1.fc44.aarch64'
rpm -qp --scripts "$rpm_path" >/dev/null
payload_dir=$output_dir/payload
install -d -m 0700 "$payload_dir"
rpm2cpio "$rpm_path" | (cd "$payload_dir" && cpio -idm --quiet)
strings "$payload_dir/usr/bin/rmtfs" | grep -Fqx '/boot/modem_study'
strings "$payload_dir/usr/bin/rmtfs" | grep -Fqx -- '-W requires -r -P and forbids -o'
grep -Fq 'ExecStart=/usr/bin/rmtfs -r -P -s' \
  "$payload_dir/usr/lib/systemd/system/rmtfs.service"
! grep -Eq 'ExecStart=.*[[:space:]]-W([[:space:]]|$)' \
  "$payload_dir/usr/lib/systemd/system/rmtfs.service"

{
  printf 'LUMA_FP6_RMTFS_BUILD_VERSION=2\n'
  printf 'UPSTREAM_VERSION=1.1.1\n'
  printf 'FEDORA_RELEASE=44\n'
  printf 'ARCHITECTURE=aarch64\n'
  printf 'UPSTREAM_COMMIT=27b3a6f00f121a0f8195e25c40faf97b57e2cec1\n'
  printf 'SOURCE_SHA512=%s\n' "$source_sha512"
  printf 'PATCH_SHA256=%s\n' "$(sha256sum "$patch_file" | awk '{print $1}')"
  printf 'RUNTIME_PATCH_SHA256=%s\n' "$(sha256sum "$runtime_patch" | awk '{print $1}')"
  printf 'SPEC_SHA256=%s\n' "$(sha256sum "$spec_file" | awk '{print $1}')"
  printf 'RPM_FILENAME=%s\n' "$(basename "$rpm_path")"
  printf 'RPM_BYTES=%s\n' "$(stat -c '%s' "$rpm_path")"
  printf 'RPM_SHA256=%s\n' "$(sha256sum "$rpm_path" | awk '{print $1}')"
  printf 'RPM_SIGNATURE=false\n'
  printf 'RUNTIME_BINARY_SHA256=%s\n' "$(sha256sum "$payload_dir/usr/bin/rmtfs" | awk '{print $1}')"
  printf 'SRPM_FILENAME=%s\n' "$(basename "$srpm_path")"
  printf 'SRPM_BYTES=%s\n' "$(stat -c '%s' "$srpm_path")"
  printf 'SRPM_SHA256=%s\n' "$(sha256sum "$srpm_path" | awk '{print $1}')"
  printf 'MODEM_STUDY_MAPPING=true\n'
  printf 'MODEMST_ONLY_PERSISTENCE=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PACKAGE_INSTALLED=false\n'
  printf 'P5_INSTALL_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0600 "$output_dir/manifest.env"

printf 'Offline FP6 rmtfs package built: %s\n' "$rpm_path"
printf 'No phone was accessed. Package installation remains unauthorized.\n'
