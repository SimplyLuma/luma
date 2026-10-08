#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Wi-Fi Guard build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] || {
  printf 'error: build the architecture-independent Wi-Fi Guard RPM on a Fedora Linux builder\n' >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported Wi-Fi Guard build architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

output_dir="$repo_root/build/packages/luma-wifi-guard"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
source_dir="$work_dir/luma-wifi-guard"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$source_dir"
cp -R "$repo_root/src/luma-wifi-guard/." "$source_dir/"
mkdir -p "$source_dir/tests" && cp -R "$repo_root/tests/luma-wifi-guard/." "$source_dir/tests/"
find "$source_dir" -type d -name __pycache__ -prune -exec rm -rf {} +
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
tar -C "$work_dir" --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 \
  --numeric-owner -cf - luma-wifi-guard | gzip -n >"$rpmbuild_dir/SOURCES/luma-wifi-guard.tar.gz"
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
install -m 0644 "$repo_root/packaging/rpm/luma-wifi-guard.spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    # The builder mounts whichever build root it was created with, which is not
    # always this repository's own, so absolute /build paths do not survive a
    # nested checkout. Everything here is resolved from the working directory
    # the runner already places us in: <build root>/packages/<pkg>.work.X/rpmbuild
    dnf5 -q -y install --allowerasing \
      meson ninja-build python3-devel python3-gobject-base rpm-build systemd-rpm-macros
    rpmbuild -ba --define \"_topdir \$PWD\" SPECS/luma-wifi-guard.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Wi-Fi Guard packages: %s\n' "$output_dir"
