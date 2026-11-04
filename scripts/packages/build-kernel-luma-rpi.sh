#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build kernel-luma-rpi natively on aarch64 from the sources recorded in
# config/kernel/rpi-7.2.json: Raspberry Pi's tree and Fedora's kernel
# configuration (from the pinned kernel-core RPM). The spec configures and
# builds the tree itself. A cross build on x86_64 is scripts/kernel/package-rpi.sh.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
lock="$repo_root/config/kernel/rpi-7.2.json"
field() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): d=d[k]
print(d)' "$lock" "$1"; }

[ "$(uname -m)" = aarch64 ] || { printf 'error: build kernel-luma-rpi on aarch64\n' >&2; exit 1; }
export LUMA_RPM_BUILDER_ARCHITECTURE=aarch64
builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64

output_dir="$repo_root/build/packages/kernel-luma-rpi"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$work_dir/fedora"

tarball="$rpmbuild_dir/SOURCES/$(field sources.linux.name)"
curl -fsSL --retry 3 -o "$tarball" "$(field sources.linux.url)"
echo "$(field sources.linux.sha256)  $tarball" | sha256sum -c --quiet - || {
  printf 'error: the Raspberry Pi kernel tarball differs from the source lock\n' >&2; exit 1; }
core="$work_dir/kernel-core.rpm"
curl -fsSL --retry 3 -o "$core" "$(field base_config.source_rpm)"
echo "$(field base_config.source_rpm_sha256)  $core" | sha256sum -c --quiet - || {
  printf 'error: the Fedora kernel-core RPM differs from the source lock\n' >&2; exit 1; }
(cd "$work_dir/fedora" && rpm2cpio "$core" | cpio -idm --quiet "./$(field base_config.member)")
config="$work_dir/fedora/$(field base_config.member)"
echo "$(field base_config.sha256)  $config" | sha256sum -c --quiet - || {
  printf 'error: the Fedora configuration differs from the source lock\n' >&2; exit 1; }
install -m 0644 "$config" "$rpmbuild_dir/SOURCES/fedora-base.config"
git -C "$repo_root" archive --format=tar.gz HEAD config/kernel/rpi-7.2.json config/kernel/rpi.config \
  config/kernel/rpi-config-exceptions.txt scripts/kernel/configure-rpi.sh \
  scripts/kernel/merge-rpi-config.py >"$rpmbuild_dir/SOURCES/luma-rpi-kernel-sources.tar.gz"
install -m 0644 "$repo_root/packaging/rpm/kernel-luma-rpi.spec" "$rpmbuild_dir/SPECS/"

jobs=${RPM_BUILD_NCPUS:-$(nproc)}
"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    dnf5 -q -y install --allowerasing rpm-build dnf5-plugins
    dnf5 -q -y builddep --allowerasing SPECS/kernel-luma-rpi.spec
    export SOURCE_DATE_EPOCH=$(git -C "$repo_root" log -1 --format=%ct HEAD)
    rpmbuild -ba --define \"_topdir \$PWD\" --define 'dist .fc44' --define '_smp_mflags -j$jobs' \
      SPECS/kernel-luma-rpi.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/aarch64" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/aarch64/*.rpm "$output_dir/RPMS/aarch64/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(cd "$output_dir" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
printf 'Luma Raspberry Pi kernel: %s\n' "$output_dir"
