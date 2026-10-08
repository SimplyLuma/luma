#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Build luma-install-commands (ADR-038) as a noarch RPM in the pinned Fedora builder.
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
output="$repo_root/build/packages/luma-install-commands"
mkdir -p "$output"
work=$(mktemp -d "$output/work.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM
mkdir -p "$work"/{SOURCES,SPECS}
stage="$work/stage/luma-install-commands"
mkdir -p "$stage"
cp -R "$repo_root/src/luma-install-commands/." "$stage/"
cp "$repo_root/docs/decisions/038-no-install-hurdles.md" "$stage/"
find "$stage" -name __pycache__ -type d -prune -exec rm -rf {} +
tar -C "$work/stage" --owner=0 --group=0 --numeric-owner -czf "$work/SOURCES/luma-install-commands.tar.gz" luma-install-commands
install -m0644 "$repo_root/packaging/rpm/luma-install-commands.spec" "$work/SPECS/"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work" \
  "${LUMA_INSTALL_COMMANDS_RPM_BUILD_CONTAINER:-$FEDORA_RPM_BUILD_CONTAINER}" '
  set -euo pipefail
  dnf5 -y install rpm-build systemd-rpm-macros python3-devel
  rpmbuild -ba --define "_topdir $PWD" SPECS/luma-install-commands.spec
  '
mkdir -p "$output/RPMS/noarch" "$output/SRPMS"
install -m0644 "$work"/RPMS/noarch/luma-install-commands-*.noarch.rpm "$output/RPMS/noarch/"
install -m0644 "$work"/SRPMS/luma-install-commands-*.src.rpm "$output/SRPMS/"
(cd "$output" && sha256sum RPMS/noarch/*.rpm SRPMS/*.rpm > SHA256SUMS)
