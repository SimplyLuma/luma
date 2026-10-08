#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
base_image="$repo_root/build/track-a/luma-track-a-baseline.qcow2"

if [ "$(id -u)" -eq 0 ]; then
  printf 'error: package builds must run as the rootless Luma build account\n' >&2
  exit 1
fi
if [ ! -f "$base_image" ]; then
  printf 'error: admitted Fedora/Luma base is missing: %s\n' "$base_image" >&2
  exit 1
fi

"$repo_root/scripts/prepare-test-provisioning.sh"

package_builders=(
  build-gnome-shell.sh
  # Desktop composition uses the native Shelf. This separately packaged dock
  # remains in the complete Stage build only for the explicitly scoped
  # handheld renderer until it consumes that shared Shelf contract.
  build-dash-to-dock.sh
  build-gnome-control-center.sh
  build-gtk4.sh
  build-gtk3.sh
  build-libhandy.sh
  build-libadwaita.sh
  build-nautilus.sh
  build-luma-android-runtime.sh
  build-luma-relay.sh
  build-luma-application-installer.sh
  build-wine-relay-integration.sh
  build-gnome-calculator.sh
  build-tiling.sh
  build-luma-handheld.sh
  build-luma-shell-state.sh
  build-luma-mods.sh
  build-luma-update.sh
  build-backgrounds.sh
  build-figtree-fonts.sh
  build-caveat-fonts.sh
  build-plymouth.sh
  build-boot-theme.sh
  build-prairie-icon-theme.sh
  build-luma-desktop-launcher-policy.sh
  build-luma-homebrew.sh
  build-luma-developer-platform.sh
  build-prairie-core-apps.sh
)

# Expensive Stage package builds are deliberately resumable. A failed package
# must not force the shared production builder to repeat every earlier,
# already-verified build. The value is a builder filename from the fixed list
# above; arbitrary commands are never accepted.
start_at=${LUMA_PACKAGE_START_AT:-${package_builders[0]}}
start_index=-1
for index in "${!package_builders[@]}"; do
  if [ "${package_builders[$index]}" = "$start_at" ]; then
    start_index=$index
    break
  fi
done
if [ "$start_index" -lt 0 ]; then
  printf 'error: unknown LUMA_PACKAGE_START_AT builder: %s\n' "$start_at" >&2
  exit 1
fi

printf 'Current Stage package build started: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'Resuming at package builder: %s\n' "$start_at"
for builder in "${package_builders[@]:$start_index}"; do
  printf 'BEGIN %s %s\n' "$builder" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "$repo_root/scripts/packages/$builder"
  printf 'PASS  %s %s\n' "$builder" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
done
printf 'Current Stage package build complete: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
