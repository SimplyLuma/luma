#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build one of the pure-Python LumaUI application RPMs in the Fedora 44 builder,
# against one named Luma Developer Platform RPM:
#
#   scripts/packages/build-luma-python-app.sh PACKAGE
#
# PACKAGE is luma-terminal, luma-calculator, luma-disks, luma-maps or
# luma-monitor; each has a build-<package>.sh wrapper. The platform RPM is
# LUMA_PLATFORM_RPM when set (so a candidate can be built against the pool's
# or an incoming drop's exact AppKit), otherwise the newest this checkout has
# built under build/packages/luma-developer-platform/<arch>/RPMS.
#
# The source tarball mirrors the repository layout (src/<package>, plus the
# tests/unit files and tests/fixtures the app's tests read by path), so every
# test runs in %check exactly as it does from a checkout.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$repo_root"

package=${1:-}
case "$package" in
  luma-terminal)
    title=Terminal
    extra_paths=()
    builder_packages='vte291-gtk4 bash'
    ;;
  luma-calculator)
    title=Calculator
    extra_paths=(tests/fixtures/calc-v70.json tests/fixtures/calc-v71.json)
    builder_packages=''
    ;;
  luma-disks)
    title=Disks
    extra_paths=(tests/fixtures/disks-v70.json tests/fixtures/disks)
    for test_file in tests/unit/test_luma_disks_*.py; do extra_paths+=("$test_file"); done
    builder_packages='udisks2'
    ;;
  luma-maps)
    title=Maps
    extra_paths=(tests/fixtures/maps-v70.json tests/fixtures/maps-v70 src/luma-shell-state/org.project_luma.shell-state.gschema.xml)
    for test_file in tests/unit/test_maps_*.py; do extra_paths+=("$test_file"); done
    builder_packages='libshumate python3-cairo libportal libportal-gtk4'
    ;;
  luma-monitor)
    title=Monitor
    extra_paths=(tests/fixtures/monitor-v70.json LICENSE.md)
    builder_packages=''
    ;;
  *)
    printf 'usage: %s luma-terminal|luma-calculator|luma-disks|luma-maps|luma-monitor\n' "$0" >&2
    exit 2
    ;;
esac

# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman rpm sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required %s build tool is missing: %s\n' "$title" "$tool" >&2
    exit 1
  }
done
[ "$(uname -s)" = Linux ] || {
  printf 'error: build the architecture-independent %s RPM on a Fedora Linux builder\n' "$title" >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported %s build architecture: %s\n' "$title" "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture
export LUMA_RPM_BUILDER_NAME=${LUMA_RPM_BUILDER_NAME:-luma-python-apps-rpm-builder-f44}

platform_rpm=${LUMA_PLATFORM_RPM:-}
if [ -n "$platform_rpm" ]; then
  [ -f "$platform_rpm" ] || {
    printf 'error: LUMA_PLATFORM_RPM is not a file: %s\n' "$platform_rpm" >&2
    exit 1
  }
else
  platform_rpm_dir="$repo_root/build/packages/luma-developer-platform/$architecture/RPMS"
  platform_rpm=$(find "$platform_rpm_dir" -maxdepth 1 -type f \
    -name "luma-developer-platform-[0-9]*.${architecture}.rpm" 2>/dev/null | sort -V | tail -n 1)
  [ -n "$platform_rpm" ] || {
    printf 'error: no luma-developer-platform RPM for %s in %s\n' "$architecture" "$platform_rpm_dir" >&2
    printf 'hint: build the platform first, or set LUMA_PLATFORM_RPM to a built one\n' >&2
    exit 1
  }
fi
platform_nvra=$(rpm -qp --qf '%{NVRA}' "$platform_rpm")
platform_name=$(rpm -qp --qf '%{NAME}' "$platform_rpm")
[ "$platform_name" = luma-developer-platform ] || {
  printf 'error: %s is not a luma-developer-platform runtime RPM (header says %s)\n' "$platform_rpm" "$platform_nvra" >&2
  exit 1
}
printf '%s builds against %s\n' "$title" "$platform_nvra"

spec="$repo_root/packaging/rpm/$package.spec"
version=$(awk '/^Version:/ {print $2; exit}' "$spec")
release=$(awk '/^Release:/ {sub(/%\{\?dist\}/, "", $2); print $2; exit}' "$spec")
[ -n "$version" ] && [ -n "$release" ] || {
  printf 'error: could not read Version/Release from %s\n' "$spec" >&2
  exit 1
}

output_dir="$repo_root/build/packages/$package"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
source_dir="$work_dir/$package"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS,platform} "$source_dir/src"
install -m 0644 "$platform_rpm" "$rpmbuild_dir/platform/"
cp -R "src/$package" "$source_dir/src/"
for path in "${extra_paths[@]}"; do
  [ -e "$path" ] || { printf 'error: %s needs %s, which is missing\n' "$package" "$path" >&2; exit 1; }
  mkdir -p "$source_dir/$(dirname "$path")"
  cp -R "$path" "$source_dir/$path"
done
install -m 0755 scripts/packages/counted-unittest.py "$source_dir/counted-unittest.py"
find "$source_dir" -type d -name __pycache__ -prune -exec rm -rf {} +
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
tar -C "$work_dir" --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 \
  --numeric-owner -cf - "$package" | gzip -n >"$rpmbuild_dir/SOURCES/$package.tar.gz"
install -m 0644 "$spec" "$rpmbuild_dir/SPECS/"

# rpmbuild only checks BuildRequires; everything the spec declares is
# installed here. nice applies inside the container, where the work runs.
"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    dnf5 -q -y install \
      \$PWD/platform/$(basename "$platform_rpm") \
      appstream dbus-daemon desktop-file-utils gtk4 libadwaita libappstream-glib \
      python3-devel python3-gobject rpm-build shared-mime-info xorg-x11-server-Xvfb \
      $builder_packages
    nice -n 10 rpmbuild -ba --define \"_topdir \$PWD\" --define '_smp_mflags -j6' SPECS/$package.spec
  "

# Copy exactly the release the spec names and fail if it is not there: a glob
# that matches nothing, or matches an older build, must not pass silently.
expected="$package-$version-$release"
shopt -s nullglob
binaries=("$rpmbuild_dir"/RPMS/noarch/"$expected".*.noarch.rpm)
sources=("$rpmbuild_dir"/SRPMS/"$expected".*.src.rpm)
shopt -u nullglob
[ "${#binaries[@]}" -eq 1 ] && [ "${#sources[@]}" -eq 1 ] || {
  printf 'error: expected one %s binary RPM and one SRPM, found %d and %d\n' \
    "$expected" "${#binaries[@]}" "${#sources[@]}" >&2
  exit 1
}
for rpm_file in "${binaries[@]}" "${sources[@]}"; do
  header=$(rpm -qp --qf '%{NAME}-%{VERSION}-%{RELEASE}' "$rpm_file")
  case "$header" in
    "$expected".*) ;;
    *) printf 'error: %s carries %s, not %s\n' "$rpm_file" "$header" "$expected" >&2; exit 1 ;;
  esac
done
rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "${binaries[@]}" "$output_dir/RPMS/noarch/"
install -m 0644 "${sources[@]}" "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)
printf '%s packages: %s\n' "$title" "$output_dir"
