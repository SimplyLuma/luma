#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required shell-state package build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Luma shell-state RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

output_dir="$repo_root/build/packages/luma-shell-state"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

for source in \
  luma_shell_state.py \
  luma-shell-state-service \
  luma-shell-statectl \
  org.project_luma.shell-state.gschema.xml \
  luma-shell-state.service \
  org.project_luma.ShellState1.service \
  gtk.css \
  gtk-dark.css \
  gtk4.css \
  gtk4-dark.css \
  luma-common.css \
  index.theme \
  index-dark.theme \
  gtk-translucent.css \
  gtk4-translucent.css \
  index-translucent.theme \
  README.md; do
  install -m 0644 "$repo_root/src/luma-shell-state/$source" \
    "$rpmbuild_dir/SOURCES/$source"
done
chmod 0755 \
  "$rpmbuild_dir/SOURCES/luma-shell-state-service" \
  "$rpmbuild_dir/SOURCES/luma-shell-statectl"
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
install -m 0644 "$repo_root/packaging/rpm/luma-shell-state.spec" \
  "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install glib2-devel python3 rpm-build systemd-rpm-macros
    glib-compile-schemas --strict --dry-run SOURCES
    python3 -m py_compile \
      SOURCES/luma_shell_state.py \
      SOURCES/luma-shell-state-service \
      SOURCES/luma-shell-statectl
    rpmbuild -ba --define "_topdir $PWD" SPECS/luma-shell-state.spec
    rpm=$(find RPMS/noarch -name "luma-shell-state-*.rpm" -print -quit)
    verify=$(mktemp -d)
    cd "$verify"
    rpm2cpio "$OLDPWD/$rpm" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm payload.cpio
    test -x usr/libexec/luma-shell-state-service
    test -x usr/bin/luma-shell-statectl
    test -f usr/share/glib-2.0/schemas/org.project_luma.shell-state.gschema.xml
    test -L usr/lib/systemd/user/graphical-session.target.wants/luma-shell-state.service
    test -f usr/share/themes/Luma/gtk-3.0/gtk.css
    test -f usr/share/themes/Luma/gtk-4.0/gtk.css
    test -f usr/share/themes/Luma/gtk-4.0/gtk-dark.css
    test -f usr/share/themes/Luma-dark/gtk-4.0/gtk.css
    test -f usr/share/themes/Luma-dark/gtk-4.0/gtk-dark.css
    test -f usr/share/themes/Luma/gtk-3.0/luma-common.css
    test -f usr/share/themes/Luma-dark/gtk-3.0/gtk.css
    test -f usr/share/themes/Luma/gtk-3.0/gtk-dark.css
    test -f usr/share/themes/Luma-dark/gtk-3.0/gtk-dark.css
    # Frost and Glass for toolkits that cannot take part in a treatment.
    test -f usr/share/themes/Luma-translucent/gtk-3.0/gtk.css
    test -f usr/share/themes/Luma-translucent/gtk-3.0/luma-base.css
    test -f usr/share/themes/Luma-translucent/gtk-3.0/luma-common.css
    test -f usr/share/themes/Luma-translucent/gtk-4.0/gtk.css
    test -f usr/share/themes/Luma-translucent/gtk-4.0/luma-base.css
    test -f usr/share/themes/Luma-translucent/index.theme
    grep -Fq "@define-color luma_header_opaque #f7f8f8;" usr/share/themes/Luma-translucent/gtk-3.0/gtk.css
    grep -Fq "@define-color luma_header_opaque #f7f8f8;" usr/share/themes/Luma-translucent/gtk-4.0/gtk.css
    grep -Fq "GtkTheme=Luma-translucent" usr/share/themes/Luma-translucent/index.theme
    grep -Fq "gtk-contained-dark.css" usr/share/themes/Luma/gtk-3.0/gtk-dark.css
    grep -Fq "headerbar.luma-pane-toolbar" usr/share/themes/Luma/gtk-3.0/luma-common.css
    test -f usr/share/themes/Luma-dark/gtk-3.0/luma-common.css
    grep -Fq "gtk-contained-dark.css" usr/share/themes/Luma-dark/gtk-3.0/gtk.css
    grep -Fq "headerbar button.titlebutton" usr/share/themes/Luma/gtk-3.0/luma-common.css
    grep -Fq "headerbar button.luma-identity-button" usr/share/themes/Luma/gtk-3.0/luma-common.css
    grep -Fq "headerbar box.luma-identity-button" usr/share/themes/Luma/gtk-3.0/luma-common.css
    grep -Fq "window.luma-hdy-native-window:not(.luma-no-native-surfaces) .luma-native-work-surface" usr/share/themes/Luma/gtk-3.0/luma-common.css
    grep -Fq "  margin: 9px 9px 9px;" usr/share/themes/Luma/gtk-3.0/luma-common.css
    grep -Fq "  padding: 0 8px 0 12px;" usr/share/themes/Luma/gtk-3.0/luma-common.css
    grep -Fq "headerbar .luma-identity-label" usr/share/themes/Luma/gtk-3.0/luma-common.css
    ! grep -Fq "font-weight: 650" usr/share/themes/Luma/gtk-3.0/luma-common.css
    grep -Fq "phosh-top-panel" usr/share/themes/Luma/gtk-3.0/luma-common.css
    grep -Fq "headerbar .luma-command-bar {" usr/share/themes/Luma/gtk-3.0/luma-common.css
    grep -Fq "headerbar .luma-command-group > button:first-child:dir(rtl)" usr/share/themes/Luma/gtk-3.0/luma-common.css
    grep -Fq "leaflet.luma-split-leaflet > *:not(separator)" usr/share/themes/Luma/gtk-3.0/luma-common.css
    grep -Fq "<key name=\"shelf-arrangement\" type=\"aa{sv}\">" \
      usr/share/glib-2.0/schemas/org.project_luma.shell-state.gschema.xml
    grep -Fq "def normalize_arrangement" usr/libexec/luma-shell-state/luma_shell_state.py
    grep -Fq "<key name=\"shelf-free-placement\" type=\"b\">" \
      usr/share/glib-2.0/schemas/org.project_luma.shell-state.gschema.xml
    grep -Fq "arrangement=self._arrangement()" usr/libexec/luma-shell-state-service
    grep -Fq "org.project_luma.ShellState1" \
      usr/share/dbus-1/services/org.project_luma.ShellState1.service
  '

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma shell-state package: %s\n' "$output_dir"
