#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio mktemp podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required RPM build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Luma GTK RPM on the canonical Linux/x86_64 host\n' >&2
  exit 1
fi

architecture=${LUMA_TARGET_ARCHITECTURE:-x86_64}
case "$architecture" in
  x86_64)
    builder_container=$FEDORA_RPM_BUILD_CONTAINER
    package_nevra=$GTK4_NEVRA
    ;;
  aarch64)
    builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64
    package_nevra=$GTK4_AARCH64_NEVRA
    ;;
  *)
    printf 'error: unsupported GTK target architecture: %s\n' "$architecture" >&2
    exit 1
    ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/gtk4"
mkdir -p "$cache_dir" "$output_dir"

srpm="$cache_dir/$GTK4_SRPM"
if [ ! -f "$srpm" ]; then
  command -v dnf5 >/dev/null 2>&1 || {
    printf 'error: %s is not cached and dnf5 is not available to download it\n' "$GTK4_SRPM" >&2
    exit 1
  }
  dnf5 download --source --destdir "$cache_dir" "gtk4-4.22.4-1.fc44"
fi

printf '%s  %s\n' "$GTK4_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: GTK source RPM checksum mismatch\n' >&2
    exit 1
  }

work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir/BUILD" "$rpmbuild_dir/BUILDROOT" \
  "$rpmbuild_dir/RPMS" "$rpmbuild_dir/SOURCES" \
  "$rpmbuild_dir/SPECS" "$rpmbuild_dir/SRPMS"

(
  cd "$work_dir"
  rpm2cpio "$srpm" >payload.cpio
  cpio -idm --quiet <payload.cpio
  rm -f payload.cpio
)

mv "$work_dir/gtk4.spec" "$rpmbuild_dir/SPECS/"
find "$work_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
install -m 0644 \
  "$repo_root/patches/gtk4/0001-luma-calm-window-shadow.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gtk4/0002-luma-native-window-elevation.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gtk4/0003-luma-native-headerbar-chrome.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gtk4/0004-luma-generic-application-identity.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gtk4/0005-luma-application-identity-presentation.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gtk4/0006-luma-native-application-surfaces.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gtk4/0007-luma-generic-application-frame.patch" \
  "$repo_root/patches/gtk4/0008-luma-title-slot-and-paned-islands.patch" \
  "$repo_root/patches/gtk4/0009-luma-split-island-shadows.patch" \
  "$repo_root/patches/gtk4/0010-luma-native-menu-presentation.patch" \
  "$repo_root/patches/gtk4/0011-luma-grab-a-corner-where-it-is-drawn.patch" \
  "$repo_root/patches/gtk4/0012-luma-forget-unbound-input-method-context.patch" \
  "$repo_root/patches/gtk4/0013-gsk-restore-the-clip-when-a-first-node-is-rejected.patch" \
  "$repo_root/patches/gtk4/0014-gsk-cover-hinted-glyph-outlines.patch" \
  "$rpmbuild_dir/SOURCES/"

install -m 0644 "$repo_root/patches/gtk4/0015-luma-preserve-generic-client-layout.patch" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/patches/gtk4/0016-luma-precompiled-native-menu-css.patch" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/patches/gtk4/0017-luma-maximized-shadow-extents.patch" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/tests/toolkit-native/generic-client.py" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/tests/toolkit-native/maximized-shadow.py" "$rpmbuild_dir/SOURCES/"

(
  cd "$rpmbuild_dir/SPECS"
  # Image-based hosts ship git but not patch.
  if command -v patch >/dev/null 2>&1; then
    patch --batch --forward -p1 <"$repo_root/patches/gtk4/0000-luma-fedora-spec.patch"
  else
    GIT_CEILING_DIRECTORIES="$rpmbuild_dir" git apply -p1 --whitespace=nowarn "$repo_root/patches/gtk4/0000-luma-fedora-spec.patch"
  fi
)
# A spec patch that applied as a no-op builds the stock package; the Release
# says whether it applied (scripts/packages/spec-release.sh).
. "$repo_root/scripts/packages/spec-release.sh"
luma_assert_spec_release "$rpmbuild_dir/SPECS/gtk4.spec" \
  "$GTK4_LUMA_RELEASE" || exit 1

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$builder_container" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y builddep SPECS/gtk4.spec
    # Keep the native object cache for subsequent shared toolkit revisions.
    rpmbuild -ba --noclean --without docs --define "_topdir $PWD" \
      --define "_smp_build_ncpus 4" SPECS/gtk4.spec

    topdir=$PWD
    main_rpm=$(find "$topdir/RPMS/'"$architecture"'" -maxdepth 1 -type f \
      -name "gtk4-[0-9]*.rpm" -print -quit)
    test -n "$main_rpm"
    verify_dir=$(mktemp -d)
    cd "$verify_dir"
    rpm2cpio "$main_rpm" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio
    gresource extract ./usr/lib64/libgtk-4.so.1 \
      /org/gtk/libgtk/theme/Default/Default-light.css >Default-light.css
    grep -Fq "window.csd { box-shadow: 0 2px 5px rgba(25, 27, 31, 0.18), 0 18px 42px -14px rgba(25, 27, 31, 0.42), 0 54px 110px -28px rgba(25, 27, 31, 0.56), 0 0 0 1px rgba(17, 24, 39, 0.10)" \
      Default-light.css
    grep -Fq "window.csd:backdrop { box-shadow: 0 2px 5px rgba(25, 27, 31, 0.12), 0 18px 42px -14px rgba(25, 27, 31, 0.28), 0 0 0 1px rgba(17, 24, 39, 0.08)" \
      Default-light.css
    gresource extract ./usr/lib64/libgtk-4.so.1 \
      /org/gtk/libgtk/theme/Default/Default-dark.css >Default-dark.css
    grep -Fq "window.csd { box-shadow: 0 3px 8px rgba(9, 11, 14, 0.38), 0 22px 52px -15px rgba(9, 11, 14, 0.72), 0 64px 120px -30px rgba(9, 11, 14, 0.82), 0 0 0 1px rgba(255, 255, 255, 0.045)" \
      Default-dark.css
    grep -Fq "windowcontrols { min-height: 20px; padding: 2px; border-spacing: 0; border-radius: 9999px" \
      Default-light.css
    grep -Fq "luma-menu-keycap" Default-light.css
    grep -Fq "luma-menu-keycap" Default-dark.css
    grep -aFq "luma-menu-keycap" ./usr/lib64/libgtk-4.so.1
    ! grep -Fq "overflow: hidden" Default-light.css
    ! grep -Fq "overflow: hidden" Default-dark.css
    grep -Fq ".titlebar:not(headerbar), headerbar { padding: 0 6px; min-height: 42px; border-width: 0;" \
      Default-light.css
    grep -Fq "font-family: \"Figtree\", sans-serif" Default-light.css
    grep -Fq "headerbar menubutton.luma-identity-button" Default-light.css
    grep -Fq "0 10px 20px -14px rgba(25, 27, 31, 0.40)" Default-light.css
    grep -Fq "0 10px 22px -14px rgba(0, 0, 0, 0.70)" Default-dark.css
    grep -aFq "gtk-header-bar-luma-identity-owner" ./usr/lib64/libgtk-4.so.1
    grep -aFq "luma-no-automatic-identity" ./usr/lib64/libgtk-4.so.1
    grep -aFq "luma-identity-display" ./usr/lib64/libgtk-4.so.1
    grep -aFq "notify::application" ./usr/lib64/libgtk-4.so.1
    grep -aFq "luma-native-window" ./usr/lib64/libgtk-4.so.1
    grep -aFq "luma-native-work-surface" ./usr/lib64/libgtk-4.so.1
    grep -aFq "luma-split-surface" ./usr/lib64/libgtk-4.so.1
    grep -aFq "luma-pane-island" ./usr/lib64/libgtk-4.so.1
    grep -Fq "window.luma-native-window:not(.luma-adw-native-window):not(.luma-no-native-surfaces) > .luma-native-work-surface" Default-light.css
    grep -aFq "luma-command-host" ./usr/lib64/libgtk-4.so.1
    grep -aFq "luma-no-command-row" ./usr/lib64/libgtk-4.so.1
    grep -aFq "luma-has-command-row" ./usr/lib64/libgtk-4.so.1
    grep -aFq "gtk-luma-work-surface-overflow" ./usr/lib64/libgtk-4.so.1
    grep -aFq "application-x-executable" ./usr/lib64/libgtk-4.so.1
    grep -Fq "headerbar.luma-command-host > windowhandle { min-height: 42px; padding: 0 8px 0 12px; }" Default-light.css
    grep -Fq "headerbar.luma-command-host > .luma-command-bar { min-height: 40px; margin: 0 9px 8px;" Default-light.css
    grep -Fq "headerbar.luma-command-host > .luma-command-bar { min-height: 40px; margin: 0 9px 8px;" Default-dark.css
    grep -Fq "headerbar windowcontrols { margin-top: 9px; margin-bottom: 9px; padding: 2px; }" Default-light.css
    grep -Fq ".luma-has-command-row > .luma-native-work-surface { margin-top: 0; }" Default-light.css
  '

stable_rpms="$output_dir/RPMS/$architecture"
stable_srpms="$output_dir/SRPMS"
rm -rf "$stable_rpms" "$output_dir/SHA256SUMS"
install -d -m 0755 "$stable_rpms" "$stable_srpms"
install -m 0644 \
  "$rpmbuild_dir/RPMS/$architecture/$package_nevra.rpm" \
  "$stable_rpms/"
install -m 0644 \
  "$rpmbuild_dir/SRPMS/gtk4-4.22.4-${GTK4_LUMA_RELEASE}.fc44.src.rpm" \
  "$stable_srpms/"

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma GTK packages: %s\n' "$output_dir"
