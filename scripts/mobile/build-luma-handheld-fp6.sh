#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build the shared architecture-independent Luma Handheld extension natively on
# a Fedora AArch64 physical builder. This creates an offline RPM bundle only.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio find install mktemp rpm rpm2cpio rpmbuild sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required native AArch64 build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != aarch64 ]; then
  printf 'error: build the FP6 handheld bundle on native Linux/aarch64\n' >&2
  exit 1
fi

output_dir=${1:-$repo_root/build/mobile/fp6-physical/luma-handheld-aarch64}
mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
topdir="$work_dir/rpmbuild"
mkdir -p "$topdir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

install -m 0644 "$repo_root/extensions/luma-handheld/extension.js" \
  "$topdir/SOURCES/extension.js"
install -m 0644 "$repo_root/extensions/luma-handheld/metadata.json" \
  "$topdir/SOURCES/metadata.json"
install -m 0644 "$repo_root/extensions/luma-handheld/README.md" \
  "$topdir/SOURCES/README.md"
install -m 0644 "$repo_root/extensions/luma-handheld/handheld.css" \
  "$topdir/SOURCES/handheld.css"
install -m 0755 "$repo_root/extensions/luma-handheld/luma-power-key-broker.py" \
  "$topdir/SOURCES/luma-power-key-broker.py"
install -m 0644 "$repo_root/extensions/luma-handheld/70-luma-handheld-power-key.rules" \
  "$topdir/SOURCES/70-luma-handheld-power-key.rules"
install -m 0755 "$repo_root/extensions/luma-handheld/luma-mobile-input-settings.py" \
  "$topdir/SOURCES/luma-mobile-input-settings.py"
install -m 0644 "$repo_root/extensions/luma-handheld/org.project_luma.MobileInputSettings.desktop" \
  "$topdir/SOURCES/org.project_luma.MobileInputSettings.desktop"
install -m 0644 "$repo_root/extensions/luma-handheld/org.project_luma.handheld.gschema.xml" \
  "$topdir/SOURCES/org.project_luma.handheld.gschema.xml"
install -m 0755 "$repo_root/extensions/luma-handheld/luma-keyboard-lab" \
  "$topdir/SOURCES/luma-keyboard-lab"
install -m 0755 "$repo_root/extensions/luma-handheld/luma-keyboard-lab-session" \
  "$topdir/SOURCES/luma-keyboard-lab-session"
install -m 0755 "$repo_root/extensions/luma-handheld/luma-keyboard-lab-entry.py" \
  "$topdir/SOURCES/luma-keyboard-lab-entry.py"
install -m 0755 "$repo_root/extensions/luma-handheld/luma-kwin-keyboard-engine" \
  "$topdir/SOURCES/luma-kwin-keyboard-engine"
install -m 0644 "$repo_root/extensions/luma-handheld/luma-power-key-broker.service" \
  "$topdir/SOURCES/luma-power-key-broker.service"
install -m 0644 "$repo_root/LICENSE.md" "$topdir/SOURCES/LICENSE.md"
install -m 0644 \
  "$repo_root/packaging/rpm/gnome-shell-extension-luma-handheld.spec" \
  "$topdir/SPECS/"

rpmbuild -ba --define "_topdir $topdir" \
  "$topdir/SPECS/gnome-shell-extension-luma-handheld.spec"

rpm_path="$topdir/RPMS/noarch/$LUMA_HANDHELD_NEVRA.rpm"
test -f "$rpm_path"
verify_dir=$(mktemp -d "$output_dir/verify.XXXXXX")
(
  cd "$verify_dir"
  rpm2cpio "$rpm_path" | cpio -idm --quiet
  js=usr/share/gnome-shell/extensions/handheld@project-luma.local/extension.js
  grep -Fq 'this._bottomGesture = null' "$js"
  grep -Fq '_observeBottomNavigation(event)' "$js"
  grep -Fq 'const BOTTOM_GESTURE_DIRECTION_PX = 18' "$js"
  grep -Fq "this._recordGesture('bottomCandidates')" "$js"
  grep -Fq "this._recordGesture('bottomDirectionRejected')" "$js"
  grep -Fq '_observeEdgeNavigation(event)' "$js"
  grep -Fq "this._recordGesture('edgeCandidates')" "$js"
  grep -Fq "this._recordGesture('edgeDirectionRejected')" "$js"
  ! grep -Fq 'Shell.EdgeDragGesture' "$js"
  ! grep -Fq '_createBottomGesture()' "$js"
  grep -Fq 'this._finishBottomGesture(' "$js"
  grep -Fq 'global.window_group.add_child(this._homeSurface)' "$js"
  grep -Fq '_syncHomeStacking()' "$js"
  grep -Fq '_startActivityViewGuard()' "$js"
  ! grep -Fq 'Clutter.EventPhase.CAPTURE' "$js"
  grep -Fq 'this._drawerReturnHome' "$js"
  grep -Fq '_bottomNavigationStartY(monitor)' "$js"
  grep -Fq 'event.get_coords()' "$js"
  grep -Fq '<method name="GetGestureDiagnostics">' "$js"
  grep -Fq '_recordGesture(name) {' "$js"
  grep -Fq '_hasRunningApplication()' "$js"
  grep -Fq '_reconcileEmptyActivityView()' "$js"
  ! grep -Fq "this._bottomGesture.connect('progress'" "$js"
  ! grep -Fq 'new Background.BackgroundManager' "$js"
  grep -Fq 'background-color: transparent' \
    usr/share/gnome-shell/extensions/handheld@project-luma.local/handheld.css
)

bundle_dir="$output_dir/bundle"
mkdir -p "$bundle_dir"
install -m 0644 "$rpm_path" "$bundle_dir/$LUMA_HANDHELD_NEVRA.rpm"
rpm_sha=$(sha256sum "$bundle_dir/$LUMA_HANDHELD_NEVRA.rpm" | awk '{print $1}')

{
  printf 'LUMA_FP6_HANDHELD_BUILD_VERSION=1\n'
  printf 'FEDORA_RELEASE=44\n'
  printf 'BUILD_ARCHITECTURE=aarch64\n'
  printf 'PACKAGE_ARCHITECTURE=noarch\n'
  printf 'HANDHELD_NEVRA=%s\n' "$LUMA_HANDHELD_NEVRA"
  printf 'HANDHELD_RPM_SHA256=%s\n' "$rpm_sha"
  printf 'PHONE_ACCESSED=false\n'
  printf 'PACKAGE_INSTALLED=false\n'
  printf 'SESSION_RESTARTED=false\n'
} >"$output_dir/manifest.env"

chmod 0644 "$output_dir/manifest.env"
printf 'FP6 Luma Handheld bundle: %s\n' "$output_dir"
