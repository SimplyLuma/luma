#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio dnf5 git mktemp podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required RPM build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Luma Calendar RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/gnome-calendar"
mkdir -p "$cache_dir" "$output_dir"

srpm="$cache_dir/$GNOME_CALENDAR_SRPM"
if [ ! -f "$srpm" ]; then
  dnf5 download --source --destdir "$cache_dir" \
    "gnome-calendar-50.0-1.fc44"
fi

printf '%s  %s\n' "$GNOME_CALENDAR_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: GNOME Calendar source RPM checksum mismatch\n' >&2
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

mv "$extract_dir/gnome-calendar.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
install -m 0644 \
  "$repo_root/patches/gnome-calendar/0001-luma-calendar-aesthetic-v1.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gnome-calendar/0002-luma-title-label-metrics.patch" \
  "$rpmbuild_dir/SOURCES/"

(
  cd "$rpmbuild_dir/SPECS"
  GIT_CEILING_DIRECTORIES="$rpmbuild_dir" git apply "$repo_root/patches/gnome-calendar/0000-luma-fedora-spec.patch"
)
# A spec patch that applied as a no-op builds the stock package; the Release
# says whether it applied (scripts/packages/spec-release.sh).
. "$repo_root/scripts/packages/spec-release.sh"
luma_assert_spec_release "$rpmbuild_dir/SPECS/gnome-calendar.spec" \
  "$GNOME_CALENDAR_LUMA_RELEASE" || exit 1

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y builddep SPECS/gnome-calendar.spec
    rpmbuild -ba --define "_topdir $PWD" SPECS/gnome-calendar.spec

    topdir=$PWD
    main_rpm=$(find "$topdir/RPMS/x86_64" -maxdepth 1 -type f \
      -name "gnome-calendar-[0-9]*.rpm" -print -quit)
    test -n "$main_rpm"
    verify_dir=$(mktemp -d)
    cd "$verify_dir"
    rpm2cpio "$main_rpm" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio
    gresource extract ./usr/bin/gnome-calendar \
      /org/gnome/calendar/ui/gui/gcal-window.ui >gcal-window.ui
    gresource extract ./usr/bin/gnome-calendar \
      /org/gnome/calendar/ui/gui/gcal-calendar-navigation-button.ui >navigation.ui
    gresource extract ./usr/bin/gnome-calendar \
      /org/gnome/calendar/style.css >style.css
    gresource extract ./usr/bin/gnome-calendar \
      /org/gnome/calendar/events.css >events.css
    grep -Fq "luma-calendar-titlebar" gcal-window.ui
    grep -Fq "luma-title-label" gcal-window.ui
    grep -Fq "luma-calendar-view-menu" gcal-window.ui
    grep -Fq "luma-calendar-year-title" navigation.ui
    grep -Fq "Project Luma Calendar composition" style.css
    grep -Fq "#d9544a" style.css
    grep -Fq "var(--event-bg-color)" events.css
    ! grep -Fq "!important" style.css
    grep -Fq "Name=Calendar" ./usr/share/applications/org.gnome.Calendar.desktop
    glib-compile-schemas ./usr/share/glib-2.0/schemas
  '

stable_rpms="$output_dir/RPMS/x86_64"
stable_srpms="$output_dir/SRPMS"
install -d -m 0755 "$stable_rpms" "$stable_srpms"
install -m 0644 \
  "$rpmbuild_dir/RPMS/x86_64/$GNOME_CALENDAR_NEVRA.rpm" \
  "$stable_rpms/"
install -m 0644 \
  "$rpmbuild_dir/SRPMS/gnome-calendar-50.0-${GNOME_CALENDAR_LUMA_RELEASE}.fc44.src.rpm" \
  "$stable_srpms/"

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma Calendar packages: %s\n' "$output_dir"
