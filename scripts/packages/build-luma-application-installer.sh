#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

platform_rpm=${LUMA_PLATFORM_RPM:-$repo_root/build/packages/luma-developer-platform/x86_64/RPMS/$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA.rpm}
[ -f "$platform_rpm" ] || {
  printf 'error: application installer requires the built platform RPM: %s\n' "$platform_rpm" >&2
  exit 1
}

for tool in mktemp podman sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required application-installer build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ] || {
  printf 'error: build the noarch application-installer RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
}

output_dir="$repo_root/build/packages/luma-application-installer"
mkdir -p "$(dirname -- "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
# The tarball mirrors the repository (src/luma-installer and tests/fixtures) because
# the tests find their fixtures by path from the repository root; the spec's
# %autosetup -n points into src/luma-installer.
source_dir="$work_dir/luma-application-installer/src/luma-installer"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$source_dir"
mkdir -p "$rpmbuild_dir/platform"
install -m 0644 "$platform_rpm" "$rpmbuild_dir/platform/"
cp -R "$repo_root/src/luma-installer/luma_installer" "$source_dir/"
cp -R "$repo_root/src/luma-installer/bin" "$repo_root/src/luma-installer/data" \
  "$repo_root/src/luma-installer/tests" "$source_dir/"
cp -R "$repo_root/src/luma-depot/luma_depot" "$source_dir/"
cp "$repo_root/src/luma-depot/data/depot.css" "$source_dir/data/depot.css"
# Depot's code ships inside this package, so Depot's tests have to come with
# it. They did not, which meant the only package that ships luma_depot could
# not run a single test of it: tools/check-unreached-tests.py found this.
cp "$repo_root/src/luma-depot/tests/"test_*.py "$source_dir/tests/"
install -d "$source_dir/tests/fixtures"
install -m 0644 "$repo_root/tests/fixtures/depot-v70.json" \
  "$repo_root/tests/fixtures/valet-v70.json" "$source_dir/tests/fixtures/"
find "$source_dir/luma_depot" "$source_dir/luma_installer" "$source_dir/tests" \
  -type d -name __pycache__ -prune -exec rm -rf {} +
# Project Luma's own listings stay private until each is public; only the seed
# generated from them is packaged, and it leaves every private entry out.
mkdir -p "$work_dir/luma-application-installer/tests/fixtures"
cp -R "$repo_root/tests/fixtures/valet-v70.json" "$work_dir/luma-application-installer/tests/fixtures/"
cp -R "$repo_root/tests/fixtures/valet-v70" "$work_dir/luma-application-installer/tests/fixtures/"
cp -R "$repo_root/tests/fixtures/depot-v70.json" "$work_dir/luma-application-installer/tests/fixtures/"
rm -f "$source_dir/data/depot-first-party.json" "$source_dir/data/depot-verified.json"
tar -C "$work_dir" -czf "$rpmbuild_dir/SOURCES/luma-application-installer.tar.gz" \
  luma-application-installer
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
install -m 0644 "$repo_root/src/luma-shell-state/org.project_luma.shell-state.gschema.xml" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/packaging/rpm/luma-application-installer.spec" \
  "$rpmbuild_dir/SPECS/"
chmod -R a+rX "$rpmbuild_dir"

bash "$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install platform/*.rpm desktop-file-utils rpm-build shared-mime-info python3-devel python3-gobject python3-cairo flatpak-libs ostree-libs glib2 dconf systemd-rpm-macros gtk4 libadwaita xorg-x11-server-Xvfb dbus-daemon
    rpmbuild -ba --define "_topdir $PWD" SPECS/luma-application-installer.spec
    rpm_path=$(find RPMS/noarch -name "luma-application-installer-*.rpm" -print -quit)
    test -n "$rpm_path"
    verify=$(mktemp -d)
    cd "$verify"
    rpm2cpio "$OLDPWD/$rpm_path" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm payload.cpio
    python3 -m py_compile usr/lib/python3*/site-packages/luma_installer/*.py
    test -x usr/bin/luma-installer
    test -x usr/bin/luma-depot
    test -f usr/share/applications/org.projectluma.Depot.desktop
    test -f usr/share/luma/installer/depot-catalog.json
    test -f usr/share/luma/installer/depot-catalog-4.json
    ! grep -Fq "\"visibility\": \"private\"" usr/share/luma/installer/depot-catalog-4.json
    test ! -e usr/share/luma/installer/depot-first-party.json
    test ! -e usr/share/luma/installer/depot-verified.json
    grep -Fq "x-scheme-handler/appstream" usr/share/applications/org.projectluma.Depot.desktop
    test -f usr/share/dbus-1/services/org.projectluma.Depot.service
    test -f usr/lib/systemd/user/luma-depot-provision.service
    test -L usr/lib/systemd/user/graphical-session.target.wants/luma-depot-provision.service
    test -f usr/lib/systemd/user-preset/60-luma-depot.preset
    test -f usr/lib/systemd/user/luma-depot-app-updates.timer
    test -L usr/lib/systemd/user/graphical-session.target.wants/luma-depot-app-updates.timer
    grep -Fq "Exec=luma-depot --view updates" usr/share/applications/org.projectluma.SoftwareUpdate.desktop
    python3 -m py_compile usr/lib/python3*/site-packages/luma_depot/*.py
    test -f usr/lib/python3*/site-packages/luma_depot/firmware_probe.py
    test -f usr/lib/python3*/site-packages/luma_depot/release_notes_sheet.py
    test -f usr/lib/python3*/site-packages/luma_depot/channels.py
    test -f usr/lib/python3*/site-packages/luma_installer/depot_channels.py
    test -f usr/lib/python3*/site-packages/luma_installer/depot_deb_repository.py
    grep -Fq "\"deb_repository\"" usr/share/luma/installer/depot-catalog-4.json
    grep -Fq "snap-install" usr/lib/python3*/site-packages/luma_installer/system_helper.py
    grep -Fq "\"sources\"" usr/share/luma/installer/depot-catalog-4.json
    ! grep -Fq "\"id\": \"sticky-notes\"" usr/share/luma/installer/depot-catalog-4.json
    grep -Fq ".dp-source" usr/share/luma-depot/depot.css
    grep -Fq "def show_release_notes" usr/lib/python3*/site-packages/luma_depot/window.py
    ! grep -Fq "_link_button" usr/lib/python3*/site-packages/luma_depot/window.py
    grep -Fq ".dp-rn-title" usr/share/luma-depot/depot.css
    ! grep -Fq "Fwupd.Client" usr/lib/python3*/site-packages/luma_depot/system_updates.py
    grep -Fq "class FailureWindow" usr/lib/python3*/site-packages/luma_depot/window.py
    grep -Fq "faulthandler.enable" usr/lib/python3*/site-packages/luma_depot/window.py
    grep -Fq "def lifecycle" usr/lib/python3*/site-packages/luma_installer/depot_errors.py
    grep -Fq "GLib.MAXINT" usr/lib/python3*/site-packages/luma_depot/system_updates.py
    # Every listed app draws an icon or its monogram, never an empty box: the
    # icon policy is its own module, the window goes through it, and the
    # application metadata every Flathub icon comes from is downloaded.
    grep -Fq "def choose" usr/lib/python3*/site-packages/luma_installer/depot_icons.py
    grep -Fq "IMAGE_SUFFIXES" usr/lib/python3*/site-packages/luma_installer/depot_icons.py
    grep -Fq "icons.choose_for" usr/lib/python3*/site-packages/luma_depot/window.py
    grep -Fq "def theme_predicate" usr/lib/python3*/site-packages/luma_depot/icons.py
    grep -Fq "def request_metadata" usr/lib/python3*/site-packages/luma_depot/native_metadata.py
    grep -Fq "def retry_now" usr/lib/python3*/site-packages/luma_depot/media.py
    ! grep -Fq "Gtk.Image.new_from_file" usr/lib/python3*/site-packages/luma_depot/window.py
    test -f usr/lib/systemd/system/luma-depot-appstream.service
    test -f usr/lib/systemd/system/luma-depot-appstream.timer
    test -L usr/lib/systemd/system/timers.target.wants/luma-depot-appstream.timer
    # A first boot with no network must not leave a failed unit behind.
    grep -Fq "ExecStart=-/usr/bin/flatpak" usr/lib/systemd/system/luma-depot-appstream.service
    grep -Fq "def restart_outcome" usr/lib/python3*/site-packages/luma_installer/depot_system_update.py
    test -x usr/bin/luma-appctl
    test -x usr/bin/luma-capsule-launch
    grep -Fq "def capsule_declared_schemes" usr/lib/python3*/site-packages/luma_installer/desktop.py
    grep -Fq "SYSTEM_SCHEMES = frozenset" usr/lib/python3*/site-packages/luma_installer/desktop.py
    test -x usr/libexec/luma-installer-system
    test -x usr/lib/systemd/system-generators/luma-snap-mount-generator
    sh -n usr/lib/systemd/system-generators/luma-snap-mount-generator
    test -f usr/lib/systemd/system/luma-installer-reconcile.service
    test -L usr/lib/systemd/system/multi-user.target.wants/luma-installer-reconcile.service
    test ! -e snap
    test ! -e usr/lib/systemd/system/snap.mount
    ! grep -Fq '"snap.mount"' usr/lib/python3*/site-packages/luma_installer/system_helper.py
    grep -Fq "application/vnd.debian.binary-package" usr/share/applications/org.projectluma.ApplicationInstaller.desktop
    grep -Fq "auth_admin_keep" usr/share/polkit-1/actions/org.projectluma.application-installer.policy
  '

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)
printf 'Luma application installer package: %s\n' "$output_dir"
