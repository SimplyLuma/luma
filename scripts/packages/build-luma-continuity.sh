#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build native Connect using its existing deterministic source/spec boundary.
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
for tool in python3 rpm podman sha256sum; do command -v "$tool" >/dev/null; done
output="$repo_root/build/packages/luma-continuity"
mkdir -p "$output"
version=$(awk '/^Version:/ {print $2;exit}' "$repo_root/packaging/rpm/luma-continuity.spec")
release=$(awk '/^Release:/ {sub(/%\{\?dist\}/,"",$2);print $2;exit}' "$repo_root/packaging/rpm/luma-continuity.spec")
for old in "$output"/RPMS/noarch/"luma-continuity-$version-$release"*.rpm; do
  [ ! -e "$old" ] || { printf 'error: successful immutable release already exists: %s\n' "$old" >&2;exit 1; }
done
work=$(mktemp -d "$output/work.XXXXXX")
mkdir -p "$work"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS,dependencies}
python3 "$repo_root/scripts/packages/build-luma-continuity-source.py" "$work/SOURCES/luma-continuity.tar.gz"
install -m 0644 "$repo_root/packaging/rpm/luma-continuity.spec" "$work/SPECS/"
install -m 0644 "$repo_root/src/luma-shell-state/org.project_luma.shell-state.gschema.xml" "$work/SOURCES/"
# A prior qualified shared app release supplies native protocol providers for
# the Connect tests. This never builds a second implementation or installs live.
prairie=${LUMA_CONTINUITY_PRAIRIE_RPM:-}
[ -f "$prairie" ] || { printf 'error: LUMA_CONTINUITY_PRAIRIE_RPM must name a qualified shared-app RPM\n' >&2;exit 1; }
[ "$(rpm -qp --qf '%{NAME}' "$prairie")" = prairie-core-apps ] || exit 1
install -m 0644 "$prairie" "$work/dependencies/"
platform="$repo_root/build/packages/luma-developer-platform/$(uname -m)/RPMS/$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA.rpm"
[ -f "$platform" ] || { printf 'error: build the exact pinned Luma platform first: %s\n' "$platform" >&2;exit 1; }
[ "$(rpm -qp --qf '%{NVRA}' "$platform")" = "$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA" ] || exit 1
install -m 0644 "$platform" "$work/dependencies/"
figtree="$repo_root/build/packages/figtree-fonts/RPMS/noarch/$FIGTREE_NEVRA.rpm"
[ -f "$figtree" ] || { printf 'error: build the exact pinned Figtree font first: %s\n' "$figtree" >&2;exit 1; }
install -m 0644 "$figtree" "$work/dependencies/"
export LUMA_RPM_BUILDER_NAME=${LUMA_RPM_BUILDER_NAME:-luma-continuity-rpm-builder-f44}
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work" "$FEDORA_RPM_BUILD_CONTAINER" '
  set -euo pipefail
  # --cacheonly forbids a preview run from pulling new dependency generations.
  # Missing build requirements fail normally in rpmbuild; never ignore them.
  dnf5 -q -y --cacheonly --setopt=install_weak_deps=False install dependencies/*.rpm
  rpmbuild -ba --define "_topdir $PWD" SPECS/luma-continuity.spec
'
for kind in RPMS/noarch SRPMS; do
  mkdir -p "$output/$kind"
  files=("$work/$kind"/"luma-continuity-$version-$release"*.rpm)
  [ "${#files[@]}" -eq 1 ] && [ -f "${files[0]}" ] || exit 1
  install -m 0644 "${files[0]}" "$output/$kind/"
done
(cd "$output" && sha256sum RPMS/noarch/*.rpm SRPMS/*.rpm >SHA256SUMS)
printf 'Connect packages: %s\n' "$output"
