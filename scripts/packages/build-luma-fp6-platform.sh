#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build luma-fp6-platform (Fairphone 6 platform services) as a noarch RPM.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required FP6 platform build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] || {
  printf 'error: build the architecture-independent FP6 platform RPM on a Fedora Linux builder\n' >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported FP6 platform build architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture
export LUMA_RPM_BUILDER_NAME=${LUMA_RPM_BUILDER_NAME:-luma-fp6-platform-rpm-builder-f44}

output_dir="$repo_root/build/packages/luma-fp6-platform"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
# The RPM's directory modes come from this tree: drop a setgid bit and default
# ACLs inherited from the checkout (00755: GNU chmod keeps setgid under 0755).
chmod 00755 "$work_dir"
if command -v setfacl >/dev/null 2>&1; then setfacl -b -k "$work_dir" 2>/dev/null || true; fi
rpmbuild_dir="$work_dir/rpmbuild"
source_dir="$work_dir/luma-fp6-platform"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$source_dir"
cp -R "$repo_root/src/luma-fp6-platform/." "$source_dir/"
find "$source_dir" -type d -name __pycache__ -prune -exec rm -rf {} +
# The shared handheld defaults, one source for every phone.
install -m 0644 "$repo_root/config/mobile/luma-shell-handheld.dconf" "$source_dir/luma-shell-handheld.dconf"
install -m 0644 "$repo_root/config/mobile/luma-greeter-handheld.dconf" "$source_dir/luma-greeter-handheld.dconf"
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
tar -C "$work_dir" --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 \
  --numeric-owner -cf - luma-fp6-platform | gzip -n >"$rpmbuild_dir/SOURCES/luma-fp6-platform.tar.gz"
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
install -m 0644 "$repo_root/packaging/rpm/luma-fp6-platform.spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    dnf5 -q -y install --allowerasing \
      rpm-build systemd-rpm-macros
    rpmbuild -ba --define \"_topdir \$PWD\" SPECS/luma-fp6-platform.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'FP6 platform packages: %s\n' "$output_dir"
