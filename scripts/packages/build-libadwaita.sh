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
  printf 'error: build the Luma libadwaita RPM on the canonical Linux/x86_64 host\n' >&2
  exit 1
fi

architecture=${LUMA_TARGET_ARCHITECTURE:-x86_64}
case "$architecture" in
  x86_64)
    builder_container=$FEDORA_RPM_BUILD_CONTAINER
    package_nevra=$LIBADWAITA_NEVRA
    ;;
  aarch64)
    builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64
    package_nevra=$LIBADWAITA_AARCH64_NEVRA
    ;;
  *)
    printf 'error: unsupported libadwaita target architecture: %s\n' "$architecture" >&2
    exit 1
    ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/libadwaita"
mkdir -p "$cache_dir" "$output_dir"

srpm="$cache_dir/$LIBADWAITA_SRPM"
if [ ! -f "$srpm" ]; then
  command -v dnf5 >/dev/null 2>&1 || {
    printf 'error: dnf5 is required when the admitted libadwaita SRPM is not cached\n' >&2
    exit 1
  }
  dnf5 download --source --destdir "$cache_dir" "libadwaita-1.9.3-1.fc44"
fi

printf '%s  %s\n' "$LIBADWAITA_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: libadwaita source RPM checksum mismatch\n' >&2
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

mv "$extract_dir/libadwaita.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
install -m 0644 \
  "$repo_root/patches/libadwaita/0001-luma-light-design-system.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0002-luma-title-label-metrics.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0003-luma-title-label-baseline.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0004-prairie-generic-headerbar-contract.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0005-luma-application-components.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0006-stylesheet-add-shared-Luma-work-islands.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0007-luma-application-window-elevation.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0008-luma-connected-window-controls.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0009-luma-appkit-component-boundaries.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0010-luma-native-surface-integrity-and-global-chrome.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0011-luma-native-command-surfaces.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0012-luma-generic-application-identity.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0013-luma-stable-backdrop-materials.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0014-luma-native-application-surfaces.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0015-luma-application-identity-resolution.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0016-luma-generic-command-row.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0017-luma-native-surface-rhythm.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0018-luma-identity-title-duplicates.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/libadwaita/0019-luma-dark-design-system.patch" \
  "$repo_root/patches/libadwaita/0020-luma-title-slot-and-sidebar-islands.patch" \
  "$repo_root/patches/libadwaita/0021-luma-split-island-shadows.patch" \
  "$repo_root/patches/libadwaita/0022-luma-sidebar-pane-command-host.patch" \
  "$repo_root/patches/libadwaita/0023-luma-one-title-row-per-window.patch" \
  "$repo_root/patches/libadwaita/0024-luma-menu-contract.patch" \
  "$rpmbuild_dir/SOURCES/"

cp "$repo_root/patches/libadwaita/0025-luma-decision-sheets.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0026-luma-shared-preferences-surfaces.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0027-luma-quiet-workflow-surfaces.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0028-test-fixtures-use-fat-object-links.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0029-luma-adaptive-navigation-contrast.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0030-luma-shared-navigation-islands.patch" "$rpmbuild_dir/SOURCES/"

cp "$repo_root/patches/libadwaita/0031-luma-native-workflow-material-ownership.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0032-luma-focus-invariant-window-elevation.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0033-luma-compact-menu-width.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0034-luma-maximized-window-corners.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0036-luma-tiled-window-corners.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0037-luma-identity-inset-and-pointer-state.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0038-luma-header-ink-and-tiled-stroke.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0039-luma-quiet-filled-controls.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0040-luma-frost-glass-light-title-rows.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0041-luma-frost-glass-frosted-title-band.patch" "$rpmbuild_dir/SOURCES/"
cp "$repo_root/patches/libadwaita/0042-luma-one-glass-material-for-every-window.patch" "$rpmbuild_dir/SOURCES/"

install -m 0644 "$repo_root/patches/libadwaita/0043-luma-preserve-generic-client-layout.patch" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/tests/toolkit-native/generic-client.py" "$rpmbuild_dir/SOURCES/"

(
  cd "$rpmbuild_dir/SPECS"
  # Image-based hosts ship git but not patch; both refuse fuzz here.
  if command -v patch >/dev/null 2>&1; then
    patch --batch --forward --fuzz=0 -p1 \
      <"$repo_root/patches/libadwaita/0000-luma-fedora-spec.patch"
  elif command -v git >/dev/null 2>&1; then
    GIT_CEILING_DIRECTORIES="$rpmbuild_dir" git apply -p1 --whitespace=nowarn "$repo_root/patches/libadwaita/0000-luma-fedora-spec.patch"
  else
    printf 'error: patch or git is needed to apply the libadwaita spec patch\n' >&2
    exit 1
  fi
)
# A spec patch that applied as a no-op builds the stock package; the Release
# says whether it applied (scripts/packages/spec-release.sh).
. "$repo_root/scripts/packages/spec-release.sh"
luma_assert_spec_release "$rpmbuild_dir/SPECS/libadwaita.spec" \
  "$LIBADWAITA_LUMA_RELEASE" || exit 1

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$builder_container" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y builddep SPECS/libadwaita.spec
    rpmbuild -ba --noclean --define "_smp_build_ncpus 4" --define "_topdir $PWD" SPECS/libadwaita.spec

    topdir=$PWD
    main_rpm=$(find "$topdir/RPMS/'"$architecture"'" -maxdepth 1 -type f \
      -name "libadwaita-[0-9]*.rpm" -print -quit)
    test -n "$main_rpm"
    verify_dir=$(mktemp -d)
    cd "$verify_dir"
    rpm2cpio "$main_rpm" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio
    gresource extract ./usr/lib64/libadwaita-1.so.0 \
      /org/gnome/Adwaita/styles/gtk.css >gtk.css
    grep -Fq "@define-color headerbar_bg_color #f2f3f5" gtk.css
    grep -Fq -- "--accent-blue: #4878b8" gtk.css
    grep -Fq "headerbar { min-height: 42px" gtk.css
    grep -Fq ".luma-title-label { min-height: 22px; padding: 4px 0 0; font-family: \"Figtree\"; font-size: 12.5px; font-weight: 600; }" gtk.css
    grep -Fq "@define-color luma_window #f1f2f3" gtk.css
    grep -Fq "@define-color luma_window #21252b" gtk.css
    grep -Fq "@define-color luma_content #fbfbfa" gtk.css
    grep -Fq "@define-color luma_content #2a2e34" gtk.css
    grep -Fq "headerbar .luma-identity-button > button" gtk.css
    grep -Fq "headerbar .luma-identity-icon { min-width: 22px; min-height: 22px; border-radius: 6px; }" gtk.css
    grep -Fq "headerbar .luma-identity-icon > image { -gtk-icon-size: 20px; }" gtk.css
    grep -Fq ".luma-window-body { padding: 0 9px 9px; }" gtk.css
    grep -Fq "0 10px 20px -14px RGB(25 27 31/40%)" gtk.css
    grep -Fq ".luma-island { background-color: @luma_content; border: 1px solid @luma_line; border-radius: 10px; box-shadow: 0 2px 5px RGB(25 27 31/16%), 0 18px 36px -16px RGB(25 27 31/48%); }" gtk.css
    grep -Fq ".luma-command-bar { min-height: 40px; margin: 0 9px 8px; padding: 0 8px; border: 1px solid @luma_line; border-radius: 10px; background-color: @luma_content" gtk.css
    grep -Fq ".luma-command-group > button, .luma-command-group > menubutton > button { min-width: 30px; min-height: 30px" gtk.css
    grep -Fq "window.luma-app-window headerbar.luma-titlebar:backdrop > windowhandle { filter: none; transition: none; }" gtk.css
    grep -Fq "window.luma-app-window .luma-command-bar:backdrop, window.luma-app-window .luma-island:backdrop { background-color: @luma_content" gtk.css
    grep -Fq "window.luma-files-window, window.luma-calculator-window, window.luma-app-window" gtk.css
    grep -Fq "0 54px 110px -28px RGB(25 27 31/56%)" gtk.css
    grep -Fq "0 64px 120px -30px RGB(9 11 14/82%)" gtk.css
    grep -Fq "headerbar windowcontrols, windowcontrols.luma-window-controls" gtk.css
    grep -Fq "background-color: @luma_content" gtk.css
    grep -Fq "margin-top: 9px" gtk.css
    ! grep -Fq "overflow: hidden" gtk.css
    grep -Fq ".luma-island-split > .sidebar-pane, .luma-island-split > .content-pane { background-color: transparent; border: none; outline: none; box-shadow: none; --shade-color: transparent; }" gtk.css
    ! grep -Fq "windowtitle .title, .luma-title-label" gtk.css
    grep -Fq "windowtitle .title { min-height: 16px; }" gtk.css
    grep -Fq "headerbar button.image-button > image, headerbar menubutton > button > image { -gtk-icon-size: 14px; color: var(--headerbar-fg-color); }" gtk.css
    grep -Fq "headerbar windowcontrols > button > image, headerbar windowcontrols > button.image-button > image { -gtk-icon-size: 10px; }" gtk.css
    grep -Fq -- "-gtk-icon-size: 11px" gtk.css
    grep -Fq ".collapse-spacing { padding-top: 0; padding-bottom: 0; }" gtk.css
    grep -Fq "windowcontrols > button, windowcontrols > button.image-button { min-width: 20px; min-height: 20px; padding: 0; }" gtk.css
    grep -Fq "toolbarview > .top-bar { background-color: var(--headerbar-bg-color); color: var(--headerbar-fg-color); }" gtk.css
    # Patch0032 replaced the historical focus-dependent decoration recipe.
    grep -Fq "window.csd.maximized:not(.fullscreen) { --window-radius: 15px; border-radius: var(--window-radius); outline: none; }" gtk.css
    grep -Fq -- "--window-radius: 0px" gtk.css
    grep -Fq "0 54px 110px -28px RGB(25 27 31/56%)" gtk.css
    grep -Fq ".navigation-sidebar > row { border-radius: 8px; min-height: 14px; padding: 7px 11px; margin: 1px 10px; color: var(--sidebar-fg-color); font-size: 12.5px; }" gtk.css
    grep -Fq ".navigation-sidebar > row:selected:hover, .navigation-sidebar > row:selected.has-open-popup { background-color: color-mix(in srgb, currentColor 13%, transparent); }" gtk.css
    grep -aFq "#4878b8" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-automatic-identity" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-no-automatic-identity" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "notify::application" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-identity-display" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-native-window" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-adw-native-window" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-native-work-surface" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-window-toolbar-view" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-split-surface" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-command-title" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-command-host" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-pane-command-host" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-window-title-row" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-pane-toolbar" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-no-command-row" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-has-command-row" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-repeats-identity" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "adw-luma-work-surface-overflow" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "application-x-executable" ./usr/lib64/libadwaita-1.so.0
    grep -Fq "headerbar.luma-command-host { padding: 0; }" gtk.css
    grep -Fq "headerbar.luma-command-host > windowhandle { min-height: 42px; }" gtk.css
    grep -Fq "headerbar.luma-command-host > .luma-command-bar { margin: 0 9px 8px; }" gtk.css
    grep -Fq "headerbar windowtitle.luma-repeats-identity { opacity: 0; }" gtk.css
    grep -Fq "headerbar box.luma-identity-button { min-height: 24px; padding: 5px 8px 5px 4px; border-radius: 10px;" gtk.css
    grep -Fq "@define-color view_bg_color #2a2e34" gtk.css
    grep -Fq "@define-color window_bg_color #21252b" gtk.css
    # Patch0041: frost and glass title rows are a frosted band, not a veil.
    grep -Fq "#161b20" gtk.css
    # Patch0042: one material for every window. The treatment class no longer
    # depends on an application using the kit window, the title band, menus
    # and popovers take one veil, and islands are opaque paper.
    #
    # These are the values, not the word "glass": a rule that names an
    # undefined colour is dropped by GTK without a word, which is how the
    # title band went missing in every application outside the kit. Each of
    # these has been seen to fail against a build without Patch0042.
    #
    # No apostrophes in here. This whole block is a single-quoted argument,
    # and one apostrophe ends it: the container then receives a different
    # command, which is how two icon checks ran against nothing for
    # seventeen releases (docs/development/checks-that-check-nothing.md).
    grep -Fq -- "--luma-veil: rgba(250, 250, 251, 0.85)" gtk.css
    grep -Fq -- "--luma-veil: rgba(248, 249, 250, 0.90)" gtk.css
    grep -Fq -- "--luma-paper: #fbfbfa" gtk.css
    grep -Fq "linear-gradient(to bottom, var(--luma-veil), var(--luma-veil))" gtk.css
    grep -Fq "background-color: var(--luma-paper)" gtk.css
    grep -Fq "background-color: var(--luma-veil)" gtk.css
    # The title band must not name a colour this stylesheet does not define.
    ! grep -Fq "linear-gradient(to bottom, @luma_chrome, @luma_chrome)" gtk.css
    # Plain text on a translucent surface.
    grep -Fq "text-shadow: none; -gtk-icon-shadow: none;" gtk.css
    # The style manager applies the treatment to every toplevel and redefines
    # the palette names for it, which is what makes every existing Luma rule
    # in this sheet follow the treatment without being touched.
    grep -aFq "luma-treatment-glass" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "luma-treatment-frost" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "org.project_luma.shell-state" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "@define-color luma_chrome rgba(250,250,251,0.85);" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "@define-color luma_island #ffffff;" ./usr/lib64/libadwaita-1.so.0
    grep -aFq "@define-color luma_window rgba(255,255,255,0.30);" ./usr/lib64/libadwaita-1.so.0
    # And the title row paints nothing, whether or not the window is csd.
    grep -Fq "window.luma-treatment-frost, window.luma-treatment-glass { --headerbar-bg-color: transparent" gtk.css
  '

stable_rpms="$output_dir/RPMS/$architecture"
stable_srpms="$output_dir/SRPMS"
rm -rf "$stable_rpms" "$output_dir/SHA256SUMS"
install -d -m 0755 "$stable_rpms" "$stable_srpms"
install -m 0644 \
  "$rpmbuild_dir/RPMS/$architecture/$package_nevra.rpm" \
  "$stable_rpms/"
# The rest of the binary packages as well. The platform package builds and
# runs its checks against this toolkit, not Fedora's, so it needs -devel from
# the same build; copying only the library left it silently on the stock one.
# A copy that produced nothing must say so, so the count is asserted.
copied=0
for built in "$rpmbuild_dir/RPMS/$architecture"/libadwaita-*.rpm; do
  [ -f "$built" ] || continue
  install -m 0644 "$built" "$stable_rpms/"
  copied=$((copied + 1))
done
if [ "$copied" -lt 2 ]; then
  printf 'error: copied %s libadwaita RPMs from %s, expected the library and at least -devel\n' \
    "$copied" "$rpmbuild_dir/RPMS/$architecture" >&2
  exit 1
fi
install -m 0644 \
  "$rpmbuild_dir/SRPMS/libadwaita-1.9.3-${LIBADWAITA_LUMA_RELEASE}.fc44.src.rpm" \
  "$stable_srpms/"

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma libadwaita packages: %s\n' "$output_dir"
