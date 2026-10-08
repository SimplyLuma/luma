#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Prairie apps build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ] || {
  printf 'error: build the noarch Prairie apps RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
}

output_dir="$repo_root/build/packages/prairie-core-apps"
platform_rpm="$repo_root/build/packages/luma-developer-platform/x86_64/RPMS/$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA.rpm"
[ -f "$platform_rpm" ] || {
  printf 'error: build the pinned Luma Developer Platform first: %s\n' "$platform_rpm" >&2
  exit 1
}
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
rpmbuild_dir="$work_dir/rpmbuild"
source_dir="$work_dir/prairie-core-apps"
cleanup() {
  if [ -n "${LUMA_RPM_BUILDER_EXISTING:-}" ]; then
    podman exec --user root "$LUMA_RPM_BUILDER_EXISTING" rm -rf "$work_dir"
  else
    rm -rf "$work_dir"
  fi
}
trap cleanup EXIT INT TERM

mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$source_dir" \
  "$rpmbuild_dir/SOURCES/dependencies"
install -m 0644 "$platform_rpm" "$rpmbuild_dir/SOURCES/dependencies/"
# Luma-built packages Fedora does not ship: the Python modules the Connect
# checks import, and the Prairie icon theme the Messages list checks its glyphs
# against. Each is built from its own spec first.
for dependency in python-authlib:python3-authlib python-joserfc:python3-joserfc prairie-icon-theme:prairie-icon-theme; do
  package_dir=${dependency%%:*}
  rpm_name=${dependency##*:}
  found=$(find "$repo_root/build/packages/$package_dir" -path '*/RPMS/noarch/*' \
    -name "$rpm_name-[0-9]*.noarch.rpm" -print 2>/dev/null | sort | tail -n 1)
  [ -n "$found" ] || {
    printf 'error: build %s first (packaging/rpm/%s.spec); no %s RPM under build/packages/%s\n' \
      "$package_dir" "$package_dir" "$rpm_name" "$package_dir" >&2
    exit 1
  }
  install -m 0644 "$found" "$rpmbuild_dir/SOURCES/dependencies/"
done
cp -R "$repo_root/src/prairie-core/prairie_ui" "$source_dir/"
cp -R "$repo_root/src/prairie-core/prairie_apps" "$source_dir/"
cp -R "$repo_root/src/prairie-core/bin" "$repo_root/src/prairie-core/data" \
  "$repo_root/src/prairie-core/style" "$source_dir/"
cp -R "$repo_root/src/prairie-core/tests" "$source_dir/"
tar -C "$work_dir" -czf "$rpmbuild_dir/SOURCES/prairie-core-apps.tar.gz" \
  prairie-core-apps
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
# The runtime theme gate uses the exact production schema on a private backend.
install -m 0644 "$repo_root/src/luma-shell-state/org.project_luma.shell-state.gschema.xml" \
  "$rpmbuild_dir/SOURCES/org.project_luma.shell-state.gschema.xml"
# Source2: Luma Continuity from this same revision, for the Connect checks.
python3 "$repo_root/scripts/packages/build-luma-continuity-source.py" \
  "$rpmbuild_dir/SOURCES/luma-continuity.tar.gz" >/dev/null
install -m 0644 "$repo_root/packaging/rpm/prairie-core-apps.spec" \
  "$rpmbuild_dir/SPECS/"
# Every Source the spec declares must exist before the builder starts, so a
# source nobody produces fails here by name rather than deep inside rpmbuild.
sed -n 's/^Source[0-9]*:[[:space:]]*//p' "$rpmbuild_dir/SPECS/prairie-core-apps.spec" |
  while read -r declared; do
    [ -f "$rpmbuild_dir/SOURCES/${declared##*/}" ] || {
      printf 'error: the spec declares %s but this script does not produce it\n' "$declared" >&2
      exit 1
    }
  done

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    export ATSPI_DBUS_IMPLEMENTATION=dbus-daemon
    build_log=$(mktemp)
    cleanup_log() {
      rm -f "$build_log"
    }
    trap cleanup_log EXIT INT TERM
    # The Luma-built dependencies first, then whatever else the spec asks for
    # from Fedora. No Luma package that itself requires this one is installed.
    if ! dnf5 -q -y --setopt=exclude= install --allowerasing rpm-build dnf5-plugins SOURCES/dependencies/*.rpm >"$build_log" 2>&1 ||
       ! dnf5 -q -y --setopt=exclude= builddep SPECS/prairie-core-apps.spec >>"$build_log" 2>&1; then
      cat "$build_log" >&2
      exit 1
    fi
    if ! rpmbuild -ba --define "_topdir $PWD" SPECS/prairie-core-apps.spec >"$build_log" 2>&1; then
      cat "$build_log" >&2
      exit 1
    fi
    # Keep the real normal package check output with the exported artifacts.
    # A success sentence cannot establish which native checks actually ran.
    install -m 0644 "$build_log" package-check.log
    printf "Prairie core apps RPM build and runtime checks passed.\n"
    # The content checks below are silent greps and tests; trace them so a
    # failure names its line.
    set -x
    rpm_path=$(find RPMS/noarch -name "prairie-core-apps-*.rpm" -print -quit)
    test -n "$rpm_path"
    verify=$(mktemp -d)
    cd "$verify"
    rpm2cpio "$OLDPWD/$rpm_path" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio
    python3 -m py_compile usr/lib/python3*/site-packages/prairie_ui/*.py
    python3 -m py_compile usr/lib/python3*/site-packages/prairie_apps/*.py
    python3 -m py_compile usr/bin/prairie-messages-daemon
    grep -Fq "org.freedesktop.Notifications" usr/bin/prairie-messages-daemon
    grep -Fq '"'"'"mark-read", "Mark read"'"'"' usr/bin/prairie-messages-daemon
    ! grep -Fq "notify-send" usr/bin/prairie-messages-daemon
    test -x usr/bin/prairie-phone-daemon
    grep -Fq "WantedBy=graphical-session.target" \
      usr/lib/systemd/user/prairie-phone-daemon.service
    ! grep -Fq "/run/luma-display" \
      usr/lib/systemd/user/prairie-phone-daemon.service
    test -f usr/share/prairie-core/phone.css
    test -f usr/share/prairie-core/messages.css
    test -f usr/share/prairie-core/camera.css
    test -f usr/share/prairie-core/photos.css
    grep -Fq "class PhoneWindow(AppWindow):" \
      usr/lib/python3*/site-packages/prairie_apps/phone.py
    grep -Fq "org.freedesktop.ModemManager1.Messaging" \
      usr/share/polkit-1/rules.d/60-luma-handheld-telephony.rules
    desktop-file-validate usr/share/applications/org.projectluma.Messages.desktop
    test -f etc/xdg/mimeapps.list
    test ! -e usr/share/applications/mimeapps.list
    grep -Fq "application/x-rpm=org.projectluma.ApplicationInstaller.desktop" etc/xdg/mimeapps.list
    grep -Fq "x-scheme-handler/appstream=org.projectluma.Depot.desktop" etc/xdg/mimeapps.list
    grep -Fq "x-scheme-handler/luma-depot=org.projectluma.Depot.desktop" etc/xdg/mimeapps.list
    grep -Fq "max-width: 639px" usr/lib/python3*/site-packages/prairie_apps/messages.py
    grep -Fq "LUMA_PRESENTATION_MODE" usr/lib/python3*/site-packages/prairie_ui/context.py
  '

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
install -m 0644 "$rpmbuild_dir/package-check.log" "$output_dir/PACKAGE-CHECKS.log"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Prairie core apps package: %s\n' "$output_dir"
