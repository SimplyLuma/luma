#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build luma-qcom-userspace (qbootctl and tqftpserv) natively for aarch64 from
# the upstream archives the spec pins by version and SHA-256, with the qbootctl
# patches and checks in patches/qbootctl.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
spec="$repo_root/packaging/rpm/luma-qcom-userspace.spec"
global() { sed -n "s/^%global $1 //p" "$spec"; }

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
[ "$architecture" = aarch64 ] && [ "$(uname -m)" = aarch64 ] || {
  printf 'error: build luma-qcom-userspace natively on aarch64\n' >&2; exit 1; }
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

output_dir="$repo_root/build/packages/luma-qcom-userspace"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

fetch() { # name version url-version
  local name=$1 version=$2 tag=$3 sha256 archive
  sha256=$(global "${name}_sha256")
  archive="$rpmbuild_dir/SOURCES/$name-$version.tar.gz"
  curl -fsSL --retry 3 -o "$archive" "https://github.com/linux-msm/$name/archive/$tag/$name-$version.tar.gz"
  echo "$sha256  $archive" | sha256sum -c --quiet - || {
    printf 'error: %s-%s differs from the recorded archive\n' "$name" "$version" >&2; exit 1; }
}
qbootctl_version=$(global qbootctl_version)
tqftpserv_version=$(global tqftpserv_version)
fetch qbootctl "$qbootctl_version" "$qbootctl_version"
fetch tqftpserv "$tqftpserv_version" "v$tqftpserv_version"
install -m 0644 "$repo_root"/patches/qbootctl/* "$rpmbuild_dir/SOURCES/"
install -m 0644 "$spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$FEDORA_RPM_BUILD_CONTAINER_AARCH64" "
    set -euo pipefail
    dnf5 -q -y install --allowerasing rpm-build dnf5-plugins
    dnf5 -q -y builddep --allowerasing SPECS/luma-qcom-userspace.spec
    rpmbuild -ba --define \"_topdir \$PWD\" --define 'dist .fc44' SPECS/luma-qcom-userspace.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/$architecture" "$output_dir/SRPMS"
for rpm in "$rpmbuild_dir"/RPMS/"$architecture"/*.rpm; do
  case "$rpm" in *-debuginfo-*|*-debugsource-*) continue ;; esac
  install -m 0644 "$rpm" "$output_dir/RPMS/$architecture/"
done
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(cd "$output_dir" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
printf 'Qualcomm userspace packages: %s\n' "$output_dir"
