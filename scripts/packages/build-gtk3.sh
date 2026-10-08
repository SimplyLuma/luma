#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio mktemp patch podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required GTK 3 build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Luma GTK 3 RPM on the canonical Linux/x86_64 host\n' >&2
  exit 1
fi

architecture=${LUMA_TARGET_ARCHITECTURE:-x86_64}
case "$architecture" in
  x86_64)
    builder_container=$FEDORA_RPM_BUILD_CONTAINER
    package_nevra=$GTK3_NEVRA
    ;;
  aarch64)
    builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64
    package_nevra=$GTK3_AARCH64_NEVRA
    ;;
  *)
    printf 'error: unsupported GTK 3 target architecture: %s\n' "$architecture" >&2
    exit 1
    ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/gtk3"
mkdir -p "$cache_dir" "$output_dir"

srpm="$cache_dir/$GTK3_SRPM"
if [ ! -f "$srpm" ]; then
  command -v dnf5 >/dev/null || { printf "error: dnf5 is required to download the uncached GTK 3 SRPM\n" >&2; exit 1; }
  dnf5 download --source --destdir "$cache_dir" gtk3-3.24.52-2.fc44
fi

printf '%s  %s\n' "$GTK3_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: GTK 3 source RPM checksum mismatch\n' >&2
    exit 1
  }

work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir/BUILD" "$rpmbuild_dir/BUILDROOT" \
  "$rpmbuild_dir/RPMS" "$rpmbuild_dir/SOURCES" \
  "$rpmbuild_dir/SPECS" "$rpmbuild_dir/SRPMS"

(
  cd "$work_dir"
  rpm2cpio "$srpm" >payload.cpio
  cpio -idm --quiet <payload.cpio
  rm payload.cpio
)

mv "$work_dir/gtk3.spec" "$rpmbuild_dir/SPECS/"
find "$work_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
install -m 0644 \
  "$repo_root/patches/gtk3/0001-luma-generic-application-identity.patch" \
  "$repo_root/patches/gtk3/0002-luma-native-application-surfaces.patch" \
  "$repo_root/patches/gtk3/0003-luma-generic-command-row.patch" \
  "$repo_root/patches/gtk3/0004-luma-title-slot-and-paned-islands.patch" \
  "$repo_root/patches/gtk3/0005-luma-identity-title-and-window-gutter.patch" \
  "$repo_root/patches/gtk3/0006-luma-frame-child-clipping.patch" \
  "$rpmbuild_dir/SOURCES/"

(
  cd "$rpmbuild_dir/SPECS"
  patch --batch --forward --fuzz=0 -p1 \
    <"$repo_root/patches/gtk3/0000-luma-fedora-spec.patch"
)

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$builder_container" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y builddep SPECS/gtk3.spec
    rpmbuild -ba --define "_topdir $PWD" SPECS/gtk3.spec

    main_rpm=$(find RPMS/'"$architecture"' -maxdepth 1 -type f \
      -name "gtk3-[0-9]*.rpm" -print -quit)
    test -n "$main_rpm"
    verify_dir=$(mktemp -d)
    cd "$verify_dir"
    # Extract in two steps: cpio closes the pipe at the archive trailer and
    # rpm2cpio then exits 141 under pipefail even though the payload is whole.
    rpm2cpio "$OLDPWD/$main_rpm" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio
    grep -aFq "gtk3-header-bar-luma-identity-owner" usr/lib64/libgtk-3.so.0
    grep -aFq "luma-no-automatic-identity" usr/lib64/libgtk-3.so.0
    grep -aFq "luma-identity-display" usr/lib64/libgtk-3.so.0
    grep -aFq "luma-automatic-identity" usr/lib64/libgtk-3.so.0
    grep -aFq "notify::application" usr/lib64/libgtk-3.so.0
    grep -aFq "luma-native-window" usr/lib64/libgtk-3.so.0
    grep -aFq "luma-native-work-surface" usr/lib64/libgtk-3.so.0
    grep -aFq "luma-command-host" usr/lib64/libgtk-3.so.0
    grep -aFq "luma-no-command-row" usr/lib64/libgtk-3.so.0
    grep -aFq "luma-split-paned" usr/lib64/libgtk-3.so.0
    grep -aFq "luma-command-title" usr/lib64/libgtk-3.so.0
    grep -aFq "luma-repeats-identity" usr/lib64/libgtk-3.so.0
    grep -aFq "notify::border-width" usr/lib64/libgtk-3.so.0
    grep -aFq "luma-clip-children" usr/lib64/libgtk-3.so.0
  '

stable_rpms="$output_dir/RPMS/$architecture"
stable_srpms="$output_dir/SRPMS"
rm -rf "$stable_rpms" "$output_dir/SHA256SUMS"
install -d -m 0755 "$stable_rpms" "$stable_srpms"
install -m 0644 "$rpmbuild_dir/RPMS/$architecture/$package_nevra.rpm" "$stable_rpms/"
install -m 0644 \
  "$rpmbuild_dir/SRPMS/gtk3-3.24.52-${GTK3_LUMA_RELEASE}.fc44.src.rpm" \
  "$stable_srpms/"

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma GTK 3 packages: %s\n' "$output_dir"
