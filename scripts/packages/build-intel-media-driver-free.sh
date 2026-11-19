#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build Luma's intel-media-driver-free (the iHD VA-API driver): Fedora 44's
# package (sources from its source RPM, pinned by digest) with packaging/rpm's
# spec, which enables VDEnc H.264/H.265 encode. x86_64 only, native builds.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
spec="$repo_root/packaging/rpm/intel-media-driver-free.spec"
version=$(sed -n 's/^Version:[[:space:]]*//p' "$spec")
fedora_srpm=intel-media-driver-free-$version-1.fc44.src.rpm
fedora_srpm_url=https://kojipkgs.fedoraproject.org/packages/intel-media-driver-free/$version/1.fc44/src/$fedora_srpm
fedora_srpm_sha256=e566a6020240f38d5b24c71321d41c7828c5c7d9e37ff6d965bcf0cba72b19f9

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
[ "$architecture" = x86_64 ] || { printf 'error: the Intel media driver is x86_64 only\n' >&2; exit 1; }
[ "$architecture" = "$(uname -m)" ] || { printf 'error: build the Intel media driver natively\n' >&2; exit 1; }
builder_container=$FEDORA_RPM_BUILD_CONTAINER
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

output_dir="$repo_root/build/packages/intel-media-driver-free"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$work_dir/fedora"
curl -fsSL --retry 3 -o "$work_dir/$fedora_srpm" "$fedora_srpm_url"
echo "$fedora_srpm_sha256  $work_dir/$fedora_srpm" | sha256sum -c --quiet - || {
  printf 'error: %s differs from the recorded source RPM\n' "$fedora_srpm" >&2; exit 1; }
(cd "$work_dir/fedora" && rpm2cpio "../$fedora_srpm" | cpio -idm --quiet)
for file in "$work_dir"/fedora/*; do
  case "$(basename "$file")" in *.spec) ;; *) install -m 0644 "$file" "$rpmbuild_dir/SOURCES/" ;; esac
done
install -m 0644 "$spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    dnf5 -q -y install --allowerasing rpm-build dnf5-plugins
    dnf5 -q -y builddep --allowerasing SPECS/intel-media-driver-free.spec
    # Each compile command carries hundreds of absolute -I paths and must stay
    # under the kernel's 128 KiB single-argument limit (ninja runs it through
    # sh -c): build in a directory no longer than Koji's /builddir/build/BUILD.
    builddir=\$(mktemp -d /tmp/m.XXXXXX)
    trap 'rm -rf \"\$builddir\"' EXIT
    rpmbuild -ba --define \"_topdir \$PWD\" --define \"_builddir \$builddir\" \\
      --define 'dist .fc44' SPECS/intel-media-driver-free.spec
    # The driver must carry the VDEnc H.264/H.265 encoder pipelines (their
    # RTTI names); Fedora's build has neither.
    rm -rf check && mkdir check
    (cd check && rpm2cpio ../RPMS/$architecture/libva-intel-media-driver-$version-*.rpm | cpio -idm --quiet)
    for class in AvcVdencPipeline HevcVdencPipeline; do
      grep -aq \"\$class\" check/usr/lib64/dri/iHD_drv_video.so || {
        printf 'error: iHD_drv_video.so lacks %s\\n' \"\$class\" >&2; exit 1; }
    done
    rm -rf check
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/$architecture" "$output_dir/SRPMS"
for rpm in "$rpmbuild_dir"/RPMS/"$architecture"/*.rpm; do
  case "$rpm" in *-debuginfo-*|*-debugsource-*) continue ;; esac
  install -m 0644 "$rpm" "$output_dir/RPMS/$architecture/"
done
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(cd "$output_dir" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
printf 'Intel media driver packages: %s\n' "$output_dir"
