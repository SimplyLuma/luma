#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio curl git mktemp podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required RPM build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Luma Dash to Dock RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/dash-to-dock"
mkdir -p "$cache_dir" "$output_dir"

srpm="$cache_dir/$DASH_TO_DOCK_SRPM"
if [ ! -f "$srpm" ]; then
  curl -fL --retry 3 -o "$srpm" "$DASH_TO_DOCK_SRPM_URL"
fi

printf '%s  %s\n' "$DASH_TO_DOCK_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: Dash to Dock source RPM checksum mismatch\n' >&2
    exit 1
  }

work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
extract_dir="$work_dir/srpm"
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$extract_dir" "$rpmbuild_dir/BUILD" "$rpmbuild_dir/BUILDROOT" \
  "$rpmbuild_dir/RPMS" "$rpmbuild_dir/SOURCES" \
  "$rpmbuild_dir/SPECS" "$rpmbuild_dir/SRPMS"

(
  cd "$extract_dir"
  rpm2cpio "$srpm" >payload.cpio
  cpio -idm --quiet <payload.cpio
  rm -f payload.cpio
)

mv "$extract_dir/gnome-shell-extension-dash-to-dock.spec" \
  "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f \
  -exec mv -t "$rpmbuild_dir/SOURCES" {} +
install -m 0644 \
  "$repo_root/patches/dash-to-dock/0001-luma-dock-material.patch" \
  "$rpmbuild_dir/SOURCES/"
(
  cd "$rpmbuild_dir/SPECS"
  GIT_CEILING_DIRECTORIES="$rpmbuild_dir" git apply "$repo_root/patches/dash-to-dock/0000-luma-fedora-spec.patch"
)
# A spec patch that applied as a no-op builds the stock package; the Release
# says whether it applied (scripts/packages/spec-release.sh).
. "$repo_root/scripts/packages/spec-release.sh"
luma_assert_spec_release "$rpmbuild_dir/SPECS/gnome-shell-extension-dash-to-dock.spec" \
  "$DASH_TO_DOCK_LUMA_RELEASE" || exit 1

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins gjs
    dnf5 -y builddep SPECS/gnome-shell-extension-dash-to-dock.spec
    rpmbuild -ba --define "_topdir $PWD" SPECS/gnome-shell-extension-dash-to-dock.spec

    dock_rpm=$(find RPMS/noarch -type f \
      -name "gnome-shell-extension-dash-to-dock-*.rpm" -print -quit)
    verify_dir=$(mktemp -d "$PWD/verify.XXXXXX")
    (
      cd "$verify_dir"
      rpm2cpio "$OLDPWD/$dock_rpm" >payload.cpio
      cpio -idm --quiet <payload.cpio
      rm -f payload.cpio
      extension_dir=usr/share/gnome-shell/extensions/dash-to-dock@micxgx.gmail.com
      grep -Fq "box-shadow: 0 5px 16px rgba(30, 41, 59, 0.1)" \
        "$extension_dir/stylesheet.css"
      grep -Fq "#dashtodockContainer.bottom.dashtodock #dash #dashtodockDashContainer" \
        "$extension_dir/stylesheet.css"
      test "$(grep -A1 -F "#dashtodockContainer.bottom.dashtodock #dash #dashtodockDashContainer" \
        "$extension_dir/stylesheet.css" | tail -n1 | tr -d " ")" = "spacing:4px;}"
      test "$(grep -A1 -F "#dashtodockContainer.bottom.dashtodock #dash #lumaDockAppBox" \
        "$extension_dir/stylesheet.css" | tail -n1 | tr -d " ")" = "spacing:4px;}"
      grep -Fq "padding: 4px;" "$extension_dir/stylesheet.css"
      ! grep -Fq "box-shadow: 0 4px 10px rgba(30, 41, 59, 0.18)" \
        "$extension_dir/stylesheet.css"
      grep -Fq "this.child.remove_clip()" "$extension_dir/docking.js"
      grep -Fq -- "-st-icon-style: regular" "$extension_dir/stylesheet.css"
      grep -Fq "styleClass: shellApp.isTrash ?" \
        "$extension_dir/locations.js"
      grep -Fq "this.app.isTrash" "$extension_dir/appIcons.js"
      grep -Fq "AppFavorites.getAppFavorites().removeFavorite" \
        "$extension_dir/appIcons.js"
      grep -Fq "setTrashDropHover" "$extension_dir/appIcons.js"
      grep -Fq "source._lumaRemovalPending = true" "$extension_dir/appIcons.js"
      grep -Fq "source._lumaReorderPending = true" "$extension_dir/dash.js"
      grep -Fq "Commit the favorite model before DND restores" \
        "$extension_dir/dash.js"
      test -s "$extension_dir/lumaPreferences.js"
      test "$(wc -l <"$extension_dir/lumaPreferences.js")" -eq 105
      test "$(tail -n1 "$extension_dir/lumaPreferences.js")" = "}"
      gjs -c "$(sed \
        -e "/^import {$/,/^} from /d" \
        -e "s/^export class /class /" \
        "$extension_dir/lumaPreferences.js")"
      grep -Fq "org.luma.shell.dock.appearance" \
        "$extension_dir/lumaPreferences.js"
      grep -Fq "ALLOWED_SURFACES" "$extension_dir/lumaPreferences.js"
      grep -Fq "Gio.FileMonitorFlags.NONE" "$extension_dir/lumaPreferences.js"
      grep -Fq "LumaPreferences.DockPreferenceMonitor" \
        "$extension_dir/docking.js"
      grep -Fq "luma-dock-surface-green-glow" \
        "$extension_dir/stylesheet.css"
      test -s "$extension_dir/media/luma-apps-grid.svg"
      grep -Fq "fill=\"#4878b8\"" \
        "$extension_dir/media/luma-apps-grid.svg"
    )
  '

stable_rpms="$output_dir/RPMS"
stable_srpms="$output_dir/SRPMS"
install -d -m 0755 "$stable_rpms/noarch" "$stable_srpms"
install -m 0644 \
  "$rpmbuild_dir/RPMS/noarch/$DASH_TO_DOCK_NEVRA.rpm" \
  "$stable_rpms/noarch/"
install -m 0644 \
  "$rpmbuild_dir/SRPMS/gnome-shell-extension-dash-to-dock-105-${DASH_TO_DOCK_LUMA_RELEASE}.fc44.src.rpm" \
  "$stable_srpms/"

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma Dash to Dock packages: %s\n' "$output_dir"
