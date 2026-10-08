#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
output="$repo_root/build/packages/gnome-shell-extension-luma-energy"
mkdir -p "$output"
work=$(mktemp -d "$output/work.XXXXXX")
mkdir -p "$work"/{SOURCES,SPECS}
install -m0644 "$repo_root/extensions/luma-energy/extension.js" "$work/SOURCES/"
install -m0644 "$repo_root/extensions/luma-energy/metadata.json" "$work/SOURCES/"
install -m0644 "$repo_root/extensions/luma-energy/README.md" "$work/SOURCES/"
install -m0644 "$repo_root/src/luma-energy/LICENSE" "$work/SOURCES/"
install -m0644 "$repo_root/packaging/rpm/gnome-shell-extension-luma-energy.spec" "$work/SPECS/"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work" \
  "${LUMA_POLICY_RPM_BUILD_CONTAINER:-$FEDORA_RPM_BUILD_CONTAINER}" '
  set -euo pipefail
  dnf5 -y install rpm-build nodejs python3
  rpmbuild -ba --define "_topdir $PWD" SPECS/gnome-shell-extension-luma-energy.spec
  '
mkdir -p "$output/RPMS/noarch" "$output/SRPMS"
install -m0644 "$work"/RPMS/noarch/*.rpm "$output/RPMS/noarch/"
install -m0644 "$work"/SRPMS/*.src.rpm "$output/SRPMS/"
(cd "$output"; find RPMS SRPMS -name '*.rpm' | sort | xargs sha256sum >SHA256SUMS)
printf 'luma-energy extension: %s\n' "$output"
