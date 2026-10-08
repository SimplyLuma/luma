#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# NOT YET RUN. This script was written against the shape of
# build-luma-tide.sh; it has never been executed on a Fedora builder, and the
# Luma Developer Platform release it should pin has not been chosen. It
# discovers whatever platform RPM is already built for the target architecture
# rather than inventing a pin, so the first real run may need that replaced
# with an explicit NEVRA the way Tide's script carries one.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Sticky Notes build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] || {
  printf 'error: build the architecture-independent Sticky Notes RPM on a Fedora Linux builder\n' >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported Sticky Notes build architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

platform_rpm_dir="$repo_root/build/packages/luma-developer-platform/$architecture/RPMS"
platform_rpm=$(find "$platform_rpm_dir" -maxdepth 1 -type f \
  -name "luma-developer-platform-*.${architecture}.rpm" -print 2>/dev/null | sort | tail -n1)
[ -n "$platform_rpm" ] || {
  printf 'error: build the Luma Developer Platform for %s before Sticky Notes\n' "$architecture" >&2
  exit 1
}
platform_rpm_name=$(basename "$platform_rpm")

output_dir="$repo_root/build/packages/luma-sticky-notes"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
source_dir="$work_dir/luma-sticky-notes"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$source_dir"
cp -R "$repo_root/src/luma-sticky-notes/." "$source_dir/"
mkdir -p "$source_dir/tests" && cp -R "$repo_root/tests/luma-sticky-notes/." "$source_dir/tests/"
find "$source_dir" -type d -name __pycache__ -prune -exec rm -rf {} +
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
tar -C "$work_dir" --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 \
  --numeric-owner -cf - luma-sticky-notes | gzip -n >"$rpmbuild_dir/SOURCES/luma-sticky-notes.tar.gz"
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
install -m 0644 "$repo_root/packaging/rpm/luma-sticky-notes.spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    dnf5 -q -y install \
      ../../luma-developer-platform/$architecture/RPMS/$platform_rpm_name \
      appstream desktop-file-utils gtk4 libadwaita libappstream-glib meson ninja-build \
      python3-devel python3-gobject rpm-build shared-mime-info
    rpmbuild -ba --define \"_topdir \$PWD\" SPECS/luma-sticky-notes.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Sticky Notes packages: %s\n' "$output_dir"
