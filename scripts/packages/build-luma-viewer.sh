#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the architecture-independent Viewer RPM in a Fedora builder container.
#   scripts/packages/build-luma-viewer.sh [container-image]
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
image=${1:-${FEDORA_RPM_BUILD_CONTAINER:-registry.fedoraproject.org/fedora:44}}
output_dir="$repo_root/build/packages/luma-viewer"
mkdir -p "$repo_root/build"
work_dir=$(mktemp -d "$repo_root/build/luma-viewer.work.XXXXXX")
chmod 0755 "$work_dir"
trap 'rm -rf "$work_dir"' EXIT INT TERM
version=$(awk '/^Version:/ {print $2; exit}' "$repo_root/packaging/rpm/luma-viewer.spec")
mkdir -p "$work_dir"/rpmbuild/{SOURCES,SPECS}
cp -R "$repo_root/src/luma-viewer" "$work_dir/luma-viewer-$version"
find "$work_dir/luma-viewer-$version" -type d -name __pycache__ -prune -exec rm -rf {} +
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
tar -C "$work_dir" --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 --numeric-owner \
  -cf - "luma-viewer-$version" | gzip -n >"$work_dir/rpmbuild/SOURCES/luma-viewer-$version.tar.gz"
install -m 0644 "$repo_root/packaging/rpm/luma-viewer.spec" "$work_dir/rpmbuild/SPECS/"
# %check imports the app, which needs the pinned Luma Developer Platform.
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
case "$(uname -m)" in
  x86_64) platform_nevra=$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA ;;
  aarch64) platform_nevra=$LUMA_DEVELOPER_PLATFORM_AARCH64_NEVRA ;;
  *) printf 'error: unsupported Viewer build architecture: %s\n' "$(uname -m)" >&2; exit 1 ;;
esac
platform_rpm="$repo_root/build/packages/luma-developer-platform/$(uname -m)/RPMS/$platform_nevra.rpm"
[ -f "$platform_rpm" ] || {
  printf 'error: build %s before Viewer: %s\n' "$platform_nevra" "$platform_rpm" >&2
  exit 1
}
mkdir -p "$work_dir/rpmbuild/platform"
cp "$platform_rpm" "$work_dir/rpmbuild/platform/"
# Viewer uses Leaf's maintained EPUB reader instead of duplicating its engine.
leaf_nevra=$(awk '$1 ~ /^luma-leaf-/ {print $1; exit}' "$repo_root/config/desktop/packages.txt")
[ -n "$leaf_nevra" ] || { printf 'error: no pinned Leaf package\n' >&2; exit 1; }
leaf_rpm=${LUMA_LEAF_RPM:-$repo_root/build/packages/luma-leaf/RPMS/noarch/$leaf_nevra.rpm}
[ -f "$leaf_rpm" ] || {
  printf 'error: build the pinned Leaf before Viewer: %s\n' "$leaf_rpm" >&2
  exit 1
}
cp "$leaf_rpm" "$work_dir/rpmbuild/platform/"
# A development builder may have the older exact-version SDK tuple installed.
# Upgrade that tuple coherently rather than leaving devel's exact Requires
# tied to the previous main package.
platform_dir=$(dirname "$platform_rpm")
for pattern in luma-developer-platform-devel-*.rpm luma-developer-platform-sdk-*.rpm; do
  for artifact in "$platform_dir"/$pattern; do
    [ ! -f "$artifact" ] || cp "$artifact" "$work_dir/rpmbuild/platform/"
  done
done

bash "$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work_dir/rpmbuild" "$image" '
  set -euo pipefail
  dnf -q -y install platform/*.rpm rpm-build python3-devel desktop-file-utils >/dev/null
  # Everything else the spec declares, so this list cannot fall behind it.
  rpmspec -q --buildrequires SPECS/luma-viewer.spec | xargs -r -d "\n" dnf5 -q -y install
  if [ "$(id -u)" = 0 ]; then
    id conform >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash conform
    work=$PWD
    chown -R conform:conform "$work"
    runuser -u conform -- rpmbuild -ba --define "_topdir $work" SPECS/luma-viewer.spec
  else
    rpmbuild -ba --define "_topdir $PWD" SPECS/luma-viewer.spec
  fi
'
rm -rf "$output_dir"
install -d "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$work_dir"/rpmbuild/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$work_dir"/rpmbuild/SRPMS/*.rpm "$output_dir/SRPMS/"
(cd "$output_dir" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
printf 'Viewer packages: %s\n' "$output_dir"
