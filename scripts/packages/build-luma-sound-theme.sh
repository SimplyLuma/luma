#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
output="$repo_root/build/packages/luma-sound-theme"
mkdir -p "$output"
work=$(mktemp -d "$output/work.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM
mkdir -p "$work"/{SOURCES,SPECS}
tar -C "$repo_root/src" -czf "$work/SOURCES/luma-sound-theme.tar.gz" luma-sound-theme
install -m0644 "$repo_root/src/luma-sound-theme/LICENSE" "$work/SOURCES/LICENSE"
install -m0644 "$repo_root/packaging/rpm/luma-sound-theme.spec" "$work/SPECS/"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work" \
  "${LUMA_SOUND_THEME_RPM_BUILD_CONTAINER:-$FEDORA_RPM_BUILD_CONTAINER}" '
  set -euo pipefail
  dnf5 -y install rpm-build glib2 gsettings-desktop-schemas
  rpmbuild -ba --define "_topdir $PWD" SPECS/luma-sound-theme.spec
  '
release=$(sed -n 's/^Release:[[:space:]]*\([^%]*\)%{?dist}$/\1/p' "$repo_root/packaging/rpm/luma-sound-theme.spec")
version=$(sed -n 's/^Version:[[:space:]]*//p' "$repo_root/packaging/rpm/luma-sound-theme.spec")
rpm_name="luma-sound-theme-$version-$release.fc44.noarch.rpm"
rm -rf "$output/RPMS" "$output/SRPMS"
mkdir -p "$output/RPMS/noarch" "$output/SRPMS"
install -m0644 "$work/RPMS/noarch/$rpm_name" "$output/RPMS/noarch/"
install -m0644 "$work"/SRPMS/*.src.rpm "$output/SRPMS/"
(cd "$output"; find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
printf 'Luma sound theme: %s\n' "$output"
