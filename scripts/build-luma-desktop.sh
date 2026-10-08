#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
base_image="$repo_root/build/track-a/luma-track-a-baseline.qcow2"
output_dir="$repo_root/build/luma-desktop"
output_image="$output_dir/luma-desktop.qcow2"
report="$output_dir/build-report.txt"

if [ -e "$output_image" ]; then
  printf 'error: existing Luma desktop image must be archived before rebuilding: %s\n' \
    "$output_image" >&2
  exit 1
fi

if [ ! -f "$base_image" ]; then
  "$repo_root/scripts/build-track-a.sh"
fi
"$repo_root/scripts/packages/build-gnome-shell.sh"
"$repo_root/scripts/packages/build-gnome-control-center.sh"
"$repo_root/scripts/packages/build-gtk4.sh"
"$repo_root/scripts/packages/build-gtk3.sh"
"$repo_root/scripts/packages/build-libhandy.sh"
"$repo_root/scripts/packages/build-libadwaita.sh"
"$repo_root/scripts/packages/build-luma-developer-platform.sh"
"$repo_root/scripts/packages/build-luma-python-app.sh" luma-terminal
"$repo_root/scripts/packages/build-luma-python-app.sh" luma-disks
"$repo_root/scripts/packages/build-nautilus.sh"
"$repo_root/scripts/packages/build-luma-android-runtime.sh"
"$repo_root/scripts/packages/build-luma-relay.sh"
"$repo_root/scripts/packages/build-luma-apk-metadata.sh"
"$repo_root/scripts/packages/build-luma-application-installer.sh"
"$repo_root/scripts/packages/build-wine-relay-integration.sh"
"$repo_root/scripts/packages/build-luma-python-app.sh" luma-calculator
"$repo_root/scripts/packages/build-tiling.sh"
# Build the shared source artifact on desktop builders as well. The desktop
# profile deliberately leaves it disabled; the mobile overlay enables it.
"$repo_root/scripts/packages/build-luma-handheld.sh"
"$repo_root/scripts/packages/build-luma-shell-state.sh"
"$repo_root/scripts/packages/build-luma-keyring.sh"
"$repo_root/scripts/packages/build-luma-search.sh"
"$repo_root/scripts/packages/build-luma-mods.sh"
"$repo_root/scripts/packages/build-luma-update.sh"
"$repo_root/scripts/packages/build-backgrounds.sh"
"$repo_root/scripts/packages/build-figtree-fonts.sh"
"$repo_root/scripts/packages/build-caveat-fonts.sh"
"$repo_root/scripts/packages/build-plymouth.sh"
"$repo_root/scripts/packages/build-boot-theme.sh"
"$repo_root/scripts/packages/build-prairie-icon-theme.sh"
"$repo_root/scripts/packages/build-luma-desktop-launcher-policy.sh"
"$repo_root/scripts/packages/build-luma-search.sh"
"$repo_root/scripts/packages/build-luma-quick-view.sh"
"$repo_root/scripts/packages/build-luma-monitor.sh"
"$repo_root/scripts/packages/build-luma-tide.sh"
"$repo_root/scripts/packages/build-luma-darkroom.sh"
"$repo_root/scripts/packages/build-luma-messages-e2ee.sh"
"$repo_root/scripts/packages/build-prairie-core-apps.sh"

mkdir -p "$output_dir"
"$repo_root/scripts/vm/write-desktop-build-report.sh" "$base_image" "$report"

"$repo_root/scripts/vm/compose-desktop-image.sh" "$base_image" "$output_image"
printf 'output_sha256=%s\n' "$(sha256sum "$output_image" | awk '{print $1}')" >>"$report"
printf 'completed_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$report"
printf 'Luma desktop build complete: %s\n' "$output_image"
