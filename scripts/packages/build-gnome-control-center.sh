#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

# Build Settings at the revision pinned by this source tree.
surface_candidate_release=$GNOME_CONTROL_CENTER_LUMA_RELEASE

for tool in cpio dnf5 git mktemp podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required RPM build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] || {
  printf 'error: build the Luma Settings RPM on a Fedora Linux builder\n' >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported Settings architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/gnome-control-center"
mkdir -p "$cache_dir" "$output_dir"

srpm="$cache_dir/$GNOME_CONTROL_CENTER_SRPM"
if [ ! -f "$srpm" ]; then
  dnf5 download --source --destdir "$cache_dir" \
    "gnome-control-center-50.4-1.fc44"
fi

printf '%s  %s\n' "$GNOME_CONTROL_CENTER_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: GNOME Control Center source RPM checksum mismatch\n' >&2
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

mv "$work_dir/gnome-control-center.spec" "$rpmbuild_dir/SPECS/"
mv "$work_dir/gnome-control-center-50.4.tar.xz" "$rpmbuild_dir/SOURCES/"
# Stage the complete numbered series; the mandatory patch-series gate below
# verifies every declared patch, including the latest phone and display fixes.
for patch_file in "$repo_root"/patches/gnome-control-center/0[0-9][0-9][0-9]-*.patch; do
  [ "${patch_file##*/}" = 0000-luma-fedora-spec.patch ] && continue
  install -m 0644 "$patch_file" "$rpmbuild_dir/SOURCES/"
done

install -m 0644 "$repo_root/tests/gnome-control-center/session-identity/check.py" \
  "$rpmbuild_dir/SOURCES/luma-settings-session-identity-check.py"
install -m 0644 "$repo_root/src/luma-shell-state/org.project_luma.shell-state.gschema.xml" \
  "$rpmbuild_dir/SOURCES/org.project_luma.shell-state.gschema.xml"
install -m 0644 "$repo_root/patches/mutter/0019-input-Adjustable-scroll-speed-for-mice-and-touchpads.patch" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/tests/gnome-control-center/scroll-speed/extract-scroll-schema.py" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/tests/fixtures/settings-v70.json" "$rpmbuild_dir/SOURCES/"

# Settings builds against the Luma platform release this tree's inputs.env
# names (LUMA_DEVELOPER_PLATFORM_RELEASE); its LumaUI pages need that kit. The
# two RPMs go in this directory first.
platform_rpm_dir="$repo_root/build/packages/luma-developer-platform/$architecture/RPMS"
platform_rpm=$(find "$platform_rpm_dir" -maxdepth 1 -type f \
  -name "luma-developer-platform-0.1.0-${LUMA_DEVELOPER_PLATFORM_RELEASE}*.${architecture}.rpm" \
  -print -quit)
platform_devel_rpm=$(find "$platform_rpm_dir" -maxdepth 1 -type f \
  -name "luma-developer-platform-devel-0.1.0-${LUMA_DEVELOPER_PLATFORM_RELEASE}*.${architecture}.rpm" \
  -print -quit)
if [ -z "$platform_rpm" ] || [ -z "$platform_devel_rpm" ]; then
  printf 'error: put luma-developer-platform and -devel %s for %s in %s first\n' \
    "$LUMA_DEVELOPER_PLATFORM_RELEASE" "$architecture" "$platform_rpm_dir" >&2
  exit 1
fi
install -m 0644 "$platform_rpm" "$platform_devel_rpm" "$rpmbuild_dir/SOURCES/"

(
  cd "$rpmbuild_dir/SPECS"
  GIT_CEILING_DIRECTORIES="$rpmbuild_dir" git apply "$repo_root/patches/gnome-control-center/0000-luma-fedora-spec.patch"
)
# A spec patch that applied as a no-op builds the stock package; the Release
# says whether it applied (scripts/packages/spec-release.sh).
. "$repo_root/scripts/packages/spec-release.sh"
luma_assert_spec_release "$rpmbuild_dir/SPECS/gnome-control-center.spec" \
  "$surface_candidate_release" || exit 1
. "$repo_root/scripts/packages/patch-series.sh"
luma_assert_patch_series gnome-control-center "$rpmbuild_dir/SPECS/gnome-control-center.spec" \
  "$rpmbuild_dir/SOURCES" \
  "0024-users-Saved-passwords-follow-a-password-change.patch:byte-identical earlier copy of 0025; applying both fails" || exit 1

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$builder_container" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    # Only the two platform RPMs the host step resolved are in SOURCES.
    dnf5 -y install SOURCES/luma-developer-platform-0.1.0-*.rpm \
      SOURCES/luma-developer-platform-devel-0.1.0-*.rpm
    dnf5 -y builddep SPECS/gnome-control-center.spec
    rpmbuild -ba --noclean --define "_topdir $PWD" SPECS/gnome-control-center.spec
    # Name any failed packaged-content assertion in the build log.
    set -x
    gcc_rpm=$(find RPMS -type f -name "gnome-control-center-50.4-*.rpm" -print -quit)
    check_dir=$(mktemp -d)
    rpm2cpio "$gcc_rpm" | cpio -i --quiet --to-stdout \
      ./usr/share/applications/gnome-dash-panel.desktop >"$check_dir/gnome-dash-panel.desktop"
    test -s "$check_dir/gnome-dash-panel.desktop"
    desktop-file-validate "$check_dir/gnome-dash-panel.desktop"
    grep -Fxq "Exec=gnome-control-center dash" "$check_dir/gnome-dash-panel.desktop"
    # Allow free placement in Dash settings; the dead status-order switch gone (0022).
    rpm2cpio "$gcc_rpm" | cpio -i --quiet --to-stdout ./usr/bin/gnome-control-center >"$check_dir/gcc-bin"
    grep -aFq "shelf-free-placement" "$check_dir/gcc-bin"
    grep -aFq "Allow free placement" "$check_dir/gcc-bin"
    ! grep -aFq "Swap the clock and quick controls" "$check_dir/gcc-bin"
    # Span and Float follow Use islands (0023).
    grep -aFq "Always on when islands are off" "$check_dir/gcc-bin"
    # Dock folders (0024): the section, its two switches, the per-folder
    # arrangement, and the ways on and off the dock.
    grep -aFq "Dock folders" "$check_dir/gcc-bin"
    grep -aFq "Show the pinned folders in the Dash" "$check_dir/gcc-bin"
    grep -aFq "Pin a folder to show it" "$check_dir/gcc-bin"
    grep -aFq "Give the folders a surface of their own beside the dock" "$check_dir/gcc-bin"
    grep -aFq "Add folder" "$check_dir/gcc-bin"
    grep -aFq "Remove from the dock" "$check_dir/gcc-bin"
    grep -aFq "Opens as" "$check_dir/gcc-bin"
    grep -aFq "dock-folders-visible" "$check_dir/gcc-bin"
    grep -aFq "dock-folders-separate" "$check_dir/gcc-bin"
    grep -aFq "dock-folders-views" "$check_dir/gcc-bin"
    grep -aFq "dock-folders-seen" "$check_dir/gcc-bin"
    grep -aFq "dock-folders-detached" "$check_dir/gcc-bin"
    # Saved passwords follow a password change (0025): the keyring is asked
    # rather than assumed, and the person is told when it stayed behind.
    grep -aFq "Saved passwords still use your old password" "$check_dir/gcc-bin"
    grep -aFq "org.gnome.keyring.InternalUnsupportedGuiltRiddenInterface" "$check_dir/gcc-bin"
    # Scroll Speed on Mouse and Touchpad (0026).
    grep -aFq "org.projectluma.peripherals.touchpad" "$check_dir/gcc-bin"
    grep -aFq "Reset Scroll Speed" "$check_dir/gcc-bin"
    rm -rf "$check_dir"
  '

find "$rpmbuild_dir/RPMS" "$rpmbuild_dir/SRPMS" -type f -name '*.rpm' \
  -exec sha256sum {} + | sort >"$work_dir/SHA256SUMS"

stable_rpms="$output_dir/RPMS"
stable_srpms="$output_dir/SRPMS"
install -d -m 0755 "$stable_rpms/$architecture" "$stable_rpms/noarch" "$stable_srpms"
install -m 0644 \
  "$rpmbuild_dir/RPMS/$architecture/gnome-control-center-50.4-${surface_candidate_release}.fc44.${architecture}.rpm" \
  "$stable_rpms/$architecture/"
install -m 0644 \
  "$rpmbuild_dir/RPMS/noarch/gnome-control-center-filesystem-50.4-${surface_candidate_release}.fc44.noarch.rpm" \
  "$stable_rpms/noarch/"
install -m 0644 \
  "$rpmbuild_dir/SRPMS/gnome-control-center-50.4-${surface_candidate_release}.fc44.src.rpm" \
  "$stable_srpms/"

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma GNOME Control Center packages: %s\n' "$output_dir"
