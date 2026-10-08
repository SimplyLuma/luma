#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

# Build the complete Filer series at the revision pinned by this source tree.
surface_candidate_release=$NAUTILUS_LUMA_RELEASE
# An explicitly recorded private candidate can qualify a newer shared toolkit
# before its successful installed checks promote the global image pin.
platform_release=${LUMA_FILER_PLATFORM_RELEASE_OVERRIDE:-$LUMA_DEVELOPER_PLATFORM_RELEASE}
[[ "$platform_release" =~ ^1\.luma\.[0-9]+\.[A-Za-z0-9.-]+$ ]] || {
  printf 'error: malformed Filer platform candidate release\n' >&2
  exit 1
}

# The Filer release carries patches 0001 through filer_series_last in filename
# order; later files stay in the tree for the next release and are held with
# the reason below. filer_series_holds names any other held file, with why.
filer_series_last=0154
filer_series_hold_reason='beyond the complete creator series through 0154'
filer_series_holds=()
for tool in cpio git mktemp podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required RPM build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] || {
  printf 'error: build the Luma Filer RPM on a Fedora Linux builder\n' >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-x86_64}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported Filer architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/nautilus"
mkdir -p "$cache_dir" "$output_dir"

srpm="$cache_dir/$NAUTILUS_SRPM"
if [ ! -f "$srpm" ]; then
  command -v dnf5 >/dev/null 2>&1 || {
    printf 'error: dnf5 is required when the admitted Nautilus SRPM is not cached\n' >&2
    exit 1
  }
  dnf5 download --source --destdir "$cache_dir" \
    "nautilus-50.2.2-2.fc44"
fi

printf '%s  %s\n' "$NAUTILUS_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: Nautilus source RPM checksum mismatch\n' >&2
    exit 1
  }

work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
# Retain the native object cache and logs for incremental preview builds.
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

mv "$work_dir/nautilus.spec" "$rpmbuild_dir/SPECS/"
mv "$work_dir/nautilus-50.2.2.tar.xz" "$rpmbuild_dir/SOURCES/"
mv "$work_dir/default-terminal.patch" "$rpmbuild_dir/SOURCES/"
# Every numbered patch file goes to SOURCES; the spec decides the series and
# luma_assert_patch_series below proves it matches, file by file.
for patch_file in "$repo_root"/patches/nautilus/0[0-9][0-9][0-9]-*.patch; do
  cp "$patch_file" "$rpmbuild_dir/SOURCES/"
done





# Filer links LumaUI parts (reorder hint, table header, mode switch, details
# pane) from the platform release this tree's inputs.env names. A fresh
# builder container has no platform, so supply the two RPMs explicitly.
platform_rpm_dir="$repo_root/build/packages/luma-developer-platform/$architecture/RPMS"
platform_rpm=$(find "$platform_rpm_dir" -maxdepth 1 -type f \
  -name "luma-developer-platform-0.1.0-${platform_release}*.${architecture}.rpm" \
  -print -quit)
platform_devel_rpm=$(find "$platform_rpm_dir" -maxdepth 1 -type f \
  -name "luma-developer-platform-devel-0.1.0-${platform_release}*.${architecture}.rpm" \
  -print -quit)
if [ -z "$platform_rpm" ] || [ -z "$platform_devel_rpm" ]; then
  printf 'error: put luma-developer-platform and -devel %s for %s in %s first\n' \
    "$platform_release" "$architecture" "$platform_rpm_dir" >&2
  exit 1
fi
install -m 0644 "$platform_rpm" "$platform_devel_rpm" "$rpmbuild_dir/SOURCES/"
# Private package-test appearance schema; no installed Shell build dependency.
install -m 0644 "$repo_root/src/luma-shell-state/org.project_luma.shell-state.gschema.xml" "$rpmbuild_dir/SOURCES/"
# Filer's stylesheet tests need the pinned Luma Figtree build (BuildRequires
# google-figtree-fonts), which no Fedora repository carries.
figtree_rpm="$repo_root/build/packages/figtree-fonts/RPMS/noarch/$FIGTREE_NEVRA.rpm"
test -f "$figtree_rpm" || {
  printf 'error: put %s.rpm in %s first\n' "$FIGTREE_NEVRA" "${figtree_rpm%/*}" >&2
  exit 1
}
install -m 0644 "$figtree_rpm" "$rpmbuild_dir/SOURCES/"


(
  cd "$rpmbuild_dir/SPECS"
  # Keep the historical spec patch immutable. This complete successor also
  # lists every patch added after 0056 and runs the picker checks in series.
  git apply --check "$repo_root/packaging/nautilus/filer-spec-complete.patch"
  git apply "$repo_root/packaging/nautilus/filer-spec-complete.patch"
)
# A spec patch that applied as a no-op builds the stock package; the Release
# says whether it applied (scripts/packages/spec-release.sh).
. "$repo_root/scripts/packages/spec-release.sh"
luma_assert_spec_release "$rpmbuild_dir/SPECS/nautilus.spec" \
  "$surface_candidate_release" || exit 1
. "$repo_root/scripts/packages/patch-series.sh"
filer_held=("${filer_series_holds[@]}")
for patch_file in "$repo_root"/patches/nautilus/0[0-9][0-9][0-9]-*.patch; do
  patch_name=${patch_file##*/}
  patch_number=${patch_name%%-*}
  [ "$((10#$patch_number))" -gt "$((10#$filer_series_last))" ] || continue
  filer_held+=("$patch_name:held after $filer_series_last: $filer_series_hold_reason")
done
luma_assert_patch_series nautilus "$rpmbuild_dir/SPECS/nautilus.spec" \
  "$rpmbuild_dir/SOURCES" "${filer_held[@]}" || exit 1

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$builder_container" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y install SOURCES/luma-developer-platform-0.1.0-*.rpm \
      SOURCES/luma-developer-platform-devel-0.1.0-*.rpm \
      SOURCES/google-figtree-fonts-*.rpm
    # The Luma SDK is supplied as a just-built local candidate rather than an
    # enabled public repository. Assert it explicitly, then let builddep fetch
    # every repository-owned requirement; rpmbuild remains the final complete
    # BuildRequires gate.
    rpm -q luma-developer-platform-devel >/dev/null
    dnf5 -y builddep --skip-unavailable SPECS/nautilus.spec
    rpmbuild -ba --noclean --define "_topdir $PWD" SPECS/nautilus.spec
    # Name any failed packaged-content assertion in the build log.
    set -x

    topdir=$PWD
    build_arch=$(rpm --eval "%{_arch}")
    main_rpm=$(find "$topdir/RPMS/$build_arch" -maxdepth 1 -type f \
      -name "nautilus-[0-9]*.rpm" -print -quit)
    test -n "$main_rpm"
    verify_dir=$(mktemp -d)
    cd "$verify_dir"
    rpm2cpio "$main_rpm" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio
    gresource extract ./usr/bin/nautilus \
      /org/gnome/nautilus/ui/nautilus-toolbar.ui >toolbar.ui
    gresource extract ./usr/bin/nautilus \
      /org/gnome/nautilus/ui/nautilus-window.ui >window.ui
    gresource extract ./usr/bin/nautilus \
      /org/gnome/nautilus/ui/nautilus-view-controls.ui >view-controls.ui
    grep -Fq "slot.search-global" toolbar.ui
    ! grep -Fq "slot.search-visible" toolbar.ui
    ! grep -Fq "slot.search-global" window.ui
    grep -Fq "class=\"GtkBox\" id=\"sidebar_resize_handle\"" window.ui
    grep -Fq "name=\"title-widget\"" window.ui
    grep -Fq "luma-titlebar" window.ui
    grep -Fq "luma-window-toolbar-view" window.ui
    ! grep -Fq " — Filer" window.ui
    # Shared Adw.HeaderBar owns automatic identity; no local imitation remains.
    ! grep -Fq "luma-identity-button" window.ui
    ! grep -Fq "luma-no-automatic-identity" window.ui
    grep -Fq "luma-island" window.ui
    grep -aFq "org.gnome.Nautilus" ./usr/bin/nautilus
    grep -Fq "id=\"app_menu\"" window.ui
    grep -Fq ":minimize,maximize,close" window.ui
    grep -Fq "luma-secondary-toolbar" toolbar.ui
    grep -Fq "luma-compact-toolbar-button" toolbar.ui
    grep -Fq "bind-property=\"compact\"" toolbar.ui
    # The v71 phone tier is bounded at559px (0139).
    grep -Fq "max-width: 559px" window.ui
    ! grep -Fq "max-width: 639px" window.ui
    ! grep -Fq "max-width: 1023px and min-width: 640px" window.ui
    ! grep -Fq "max-width: 682sp" window.ui
    ! grep -Fq "name=\"width-request\">174<" toolbar.ui
    ! grep -Fq "luma-main-menu" toolbar.ui
    ! grep -Fq "name=\"tooltip-text\">Search<" toolbar.ui
    ! grep -Fq "name=\"tooltip-text\">Main Menu<" toolbar.ui
    grep -Fq "name=\"main-menu\"" window.ui
    ! grep -Fq "class=\"GtkSeparator\"" window.ui
    ! grep -Fq "class=\"AdwWindowTitle\"" window.ui
    gresource extract ./usr/bin/nautilus \
      /org/gnome/nautilus/style.css >style.css
    gresource extract ./usr/bin/nautilus \
      /org/gnome/nautilus/menu/nautilus-files-view-context-menus.ui >context-menus.ui
    grep -Fq "The distro-wide palette and generic controls" style.css
    grep -Fq "nautilus-column-view" style.css
    grep -Fq "luma-column-header-icon" style.css
    grep -Fq "luma-sidebar-section-heading" style.css
    grep -Fq ".luma-files-window .luma-window-title" style.css
    strings ./usr/bin/nautilus >nautilus.strings
    grep -Fq "Column view of the current location" nautilus.strings
    grep -Fq "Share" nautilus.strings
    grep -Fq "min-height: 40px" style.css
    grep -Fq "padding-top: 0" style.css
    # Native center-box spacing replaces the former CSS right-margin offset.
    grep -Fq "name=\"spacing\">6</property>" toolbar.ui
    grep -Fq "history_controls_stack" toolbar.ui
    grep -Fq ".nautilus-path-button label" style.css
    grep -Fq "transform: translate(0, 1px)" style.css
    ! grep -Fq "statuspage.luma-empty-folder" style.css
    grep -aFq "luma_empty_state_configure" ./usr/bin/nautilus
    grep -aFq "Nothing here this app can open" ./usr/bin/nautilus
    grep -Fq ".luma-path-menu > button" style.css
    grep -Fq "border-left: 1px solid @luma_line" style.css
    ! grep -Fq "@define-color luma_files_" style.css
    grep -Fq "background-color: @luma_content" style.css
    grep -Fq ".nautilus-grid-view gridview" style.css
    grep -Fq ".nautilus-list-view columnview" style.css
    grep -Fq "font-feature-settings: \"tnum\"" style.css
    grep -Fq "background-image: linear-gradient" style.css
    grep -Fq ".luma-files-window.luma-treatment-light:not(.luma-native-file-chooser) headerbar.luma-titlebar" style.css
    grep -Fq "luma-island-split" window.ui
    grep -Fq "<property name=\"overflow\">1</property>" window.ui
    grep -Fq "<property name=\"margin-top\">0</property>" window.ui
    grep -Fq ".luma-sidebar-surface.luma-island" style.css
    grep -Fq "Name=Filer" ./usr/share/applications/org.gnome.Nautilus.desktop
    grep -aFq "luma-path-menu" ./usr/bin/nautilus
    grep -aFq "gtk_scrolled_window_set_propagate_natural_width" ./usr/bin/nautilus
    # 0081: an empty folder is titled by its location.
    grep -aFq "%s is empty" ./usr/bin/nautilus
    grep -aFq "prairie-folder-empty" ./usr/bin/nautilus
    grep -aFq "open-ancestor" ./usr/bin/nautilus
    grep -aFq "Grid View" ./usr/bin/nautilus
    grep -aFq "nautilus_name_cell_set_column_mode" ./usr/bin/nautilus
    grep -aFq "applications-directory-provider" ./usr/bin/nautilus
    grep -aFq "applications:///" ./usr/bin/nautilus
    grep -aFq "nautilus_file_is_in_applications" ./usr/bin/nautilus
    grep -aFq "application/x-rootwindow-drop" ./usr/bin/nautilus
    grep -aFq "BeginApplicationDrag" ./usr/bin/nautilus
    grep -aFq "BeginFileDrag" ./usr/bin/nautilus
    grep -aFq "EndFileDrag" ./usr/bin/nautilus
    grep -aFq "Installation unavailable" ./usr/bin/nautilus
    grep -aFq "personal-storage-only" ./usr/bin/nautilus
    grep -aFq "org.projectluma.ApplicationInstaller2" ./usr/bin/nautilus
    grep -aFq "RequestInstall" ./usr/bin/nautilus
    grep -Fq "applications-background-menu" context-menus.ui
    grep -Fq "applications-selection-menu" context-menus.ui
    grep -Fq "Show Application _File" context-menus.ui
    # The LumaUI series (0057-0072) replaced the local title label, view
    # picker, compact sidebar button, details tags and root notices with kit
    # parts; assert Filer links those parts instead.
    for part in luma_table_header_for_column_view luma_mode_switch_new \
        luma_details_pane_new luma_reorder_hint_begin \
        luma_sidebar_toggle_new luma_application_window_frame_adopt \
        luma_navigation_sidebar_adapt_live; do
      grep -aFq "$part" ./usr/bin/nautilus || { echo "missing LumaUI part: $part" >&2; exit 1; }
    done
    glib-compile-schemas ./usr/share/glib-2.0/schemas
    test "$(GSETTINGS_SCHEMA_DIR=./usr/share/glib-2.0/schemas \
      gsettings get org.gnome.nautilus.window-state sidebar-width)" = 178
    test "$(GSETTINGS_SCHEMA_DIR=./usr/share/glib-2.0/schemas \
      gsettings get org.gnome.nautilus.window-state initial-size)" = "(920, 598)"
    test "$(GSETTINGS_SCHEMA_DIR=./usr/share/glib-2.0/schemas \
      gsettings get org.gnome.nautilus.icon-view default-zoom-level | tr -d "\\047")" = medium
    test "$(GSETTINGS_SCHEMA_DIR=./usr/share/glib-2.0/schemas \
      gsettings get org.gnome.nautilus.preferences personal-storage-only)" = false
  '

stable_rpms="$output_dir/RPMS/$architecture"
stable_srpms="$output_dir/SRPMS"
install -d -m 0755 "$stable_rpms" "$stable_srpms"
install -m 0644 \
  "$rpmbuild_dir/RPMS/$architecture/nautilus-50.2.2-${surface_candidate_release}.fc44.${architecture}.rpm" \
  "$stable_rpms/"
install -m 0644 \
  "$rpmbuild_dir/RPMS/$architecture/nautilus-extensions-50.2.2-${surface_candidate_release}.fc44.${architecture}.rpm" \
  "$stable_rpms/"
install -m 0644 \
  "$rpmbuild_dir/SRPMS/nautilus-50.2.2-${surface_candidate_release}.fc44.src.rpm" \
  "$stable_srpms/"

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma Files packages: %s\n' "$output_dir"
