#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the maintained native host around the byte-identical admitted engine.
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
engine=${LUMA_VIOLA_ENGINE_ARCHIVE:?Supply the admitted native30 engine archive}
engine_sha=2760bcadbb3864ffb637888b06e9717642c1add31656c2b81c6d0fb7fd5b1028
[ -f "$engine" ] && [ ! -L "$engine" ]
printf '%s  %s\n' "$engine_sha" "$engine" | sha256sum -c -
architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
[ "$architecture" = x86_64 ] || { printf 'error: the admitted Viola engine is x86_64 only\n' >&2; exit 1; }
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture
out="$repo_root/build/packages/viola-browser-stable"
mkdir -p "$(dirname "$out")"
work=$(mktemp -d "$out.work.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM
rpmroot="$work/rpmbuild"
mkdir -p "$rpmroot"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
host="$work/viola-luma-host"
mkdir -p "$host/appkit-spike" "$host/luma-package"
base="$repo_root/src/external/viola/chromium-linux"
native_release=$(awk '$1 == "%global" && $2 == "native_release" {print $3}' "$repo_root/packaging/rpm/viola-browser-stable.spec")
[[ "$native_release" =~ ^[1-9][0-9]{0,5}$ ]]
[ "$(awk '$1 == "%global" && $2 == "engine_release" {print $3}' "$repo_root/packaging/rpm/viola-browser-stable.spec")" = 30 ]
manifest="$base/luma-package/native-$native_release-host.sha256"
(cd "$base/appkit-spike" && sha256sum -c "$manifest")
while IFS= read -r member; do
  case "$member" in ''|/*|../*|*/../*|*/..) exit 1 ;; esac
  [ -f "$base/appkit-spike/$member" ] && [ ! -L "$base/appkit-spike/$member" ]
  install -D -m 0644 "$base/appkit-spike/$member" "$host/appkit-spike/$member"
done < <(cut -c67- "$manifest")
cp -R "$base/luma-package/." "$host/luma-package/"
find "$host" -type d -name __pycache__ -prune -exec rm -rf {} +
epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
tar -C "$work" --sort=name --mtime="@$epoch" --owner=0 --group=0 --numeric-owner \
  -cf - viola-luma-host | gzip -n > "$rpmroot/SOURCES/viola-luma-host-0.2.10-native$native_release.tar.gz"
cp --reflink=auto "$engine" "$rpmroot/SOURCES/viola-native-engine-0.2.10-native30.tar.zst"
install -m 0644 "$repo_root/packaging/rpm/viola-browser-stable.spec" "$rpmroot/SPECS/"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$rpmroot" "$FEDORA_RPM_BUILD_CONTAINER" '
  set -euo pipefail
  dnf5 -q -y builddep SPECS/viola-browser-stable.spec
  rpmbuild -ba --define "_topdir $PWD" SPECS/viola-browser-stable.spec
  [ "$(find RPMS/x86_64 -name "viola-browser-stable-*.rpm" | wc -l)" = 1 ]
  [ "$(find SRPMS -name "viola-browser-stable-*.src.rpm" | wc -l)" = 1 ]
'
[ ! -e "$out" ] || { printf 'error: preserve the previous immutable Viola output\n' >&2; exit 1; }
install -d -m 0755 "$out/RPMS/x86_64" "$out/SRPMS"
install -m 0644 "$rpmroot"/RPMS/x86_64/*.rpm "$out/RPMS/x86_64/"
install -m 0644 "$rpmroot"/SRPMS/*.rpm "$out/SRPMS/"
(cd "$out" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort > SHA256SUMS)
printf 'Viola native$native_release normal host and exact admitted engine packages: %s\n' "$out"
