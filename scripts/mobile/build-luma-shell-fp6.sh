#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build the existing Luma GNOME Shell downstream natively for Fedora 44
# AArch64. This creates an offline package bundle only; it never contacts a
# phone, installs a package, or changes a session.

set -euo pipefail
umask 022

build_jobs=${LUMA_RPM_BUILD_JOBS:-4}
case "$build_jobs" in
  ''|*[!0-9]*)
    printf 'error: LUMA_RPM_BUILD_JOBS must be a positive integer\n' >&2
    exit 1
    ;;
esac
[ "$build_jobs" -ge 1 ] || {
  printf 'error: LUMA_RPM_BUILD_JOBS must be at least 1\n' >&2
  exit 1
}

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio curl dnf5 git mktemp rpm rpm2cpio rpmbuild sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required native AArch64 build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != aarch64 ]; then
  printf 'error: build the FP6 Luma Shell bundle on native Linux/aarch64\n' >&2
  exit 1
fi

output_dir=${1:-$repo_root/build/mobile/fp6-physical/luma-shell-aarch64}
cache_dir=${LUMA_FP6_SHELL_CACHE_DIR:-$repo_root/build/cache/srpm}
mkdir -p "$cache_dir" "$output_dir"

srpm="$cache_dir/$GNOME_SHELL_SRPM"
if [ ! -f "$srpm" ]; then
  curl -fL --retry 3 -o "$srpm" "$GNOME_SHELL_SRPM_URL"
fi

printf '%s  %s\n' "$GNOME_SHELL_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: GNOME Shell source RPM checksum mismatch\n' >&2
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
  rpm2cpio "$srpm" | cpio -idm --quiet
)

mv "$extract_dir/gnome-shell.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f \
  -exec mv -t "$rpmbuild_dir/SOURCES" {} +

for patch_name in \
  0001-luma-panel-layout.patch \
  0002-prairie-login-lock.patch \
  0003-luma-desktop-first-session.patch \
  0004-prairie-login-spinner-row.patch \
  0005-prairie-application-shortcuts.patch \
  0006-prairie-auth-entry-and-progress.patch \
  0007-luma-handheld-posture.patch \
  0008-luma-handheld-activity-view.patch \
  0009-luma-notification-system.patch \
  0010-luma-notification-lifecycle-stack.patch \
  0011-luma-presence-login.patch; do
  install -m 0644 "$repo_root/patches/gnome-shell/$patch_name" \
    "$rpmbuild_dir/SOURCES/$patch_name"
done

(
  cd "$rpmbuild_dir/SPECS"
  git apply "$repo_root/patches/gnome-shell/0000-luma-fedora-spec.patch"
)

(
  cd "$rpmbuild_dir"
  dnf5 -y builddep SPECS/gnome-shell.spec
  command -v gresource >/dev/null 2>&1 || {
    printf 'error: GNOME Shell build dependencies did not provide gresource\n' >&2
    exit 1
  }
  rpmbuild -ba \
    --define "_topdir $PWD" \
    --define "_smp_build_ncpus $build_jobs" \
    SPECS/gnome-shell.spec
)

shell_filename="gnome-shell-50.3-${GNOME_SHELL_LUMA_RELEASE}.fc44.aarch64.rpm"
common_filename="gnome-shell-common-50.3-${GNOME_SHELL_LUMA_RELEASE}.fc44.noarch.rpm"
shell_rpm="$rpmbuild_dir/RPMS/aarch64/$shell_filename"
common_rpm="$rpmbuild_dir/RPMS/noarch/$common_filename"
srpm_output="$rpmbuild_dir/SRPMS/gnome-shell-50.3-${GNOME_SHELL_LUMA_RELEASE}.fc44.src.rpm"

for artifact in "$shell_rpm" "$common_rpm" "$srpm_output"; do
  [ -f "$artifact" ] || {
    printf 'error: expected build artifact is missing: %s\n' "$artifact" >&2
    exit 1
  }
done

verify_dir=$(mktemp -d "$output_dir/verify.XXXXXX")
(
  cd "$verify_dir"
  rpm2cpio "$shell_rpm" | cpio -idm --quiet
  gresource extract usr/share/gnome-shell/gnome-shell-theme.gresource \
    /org/gnome/shell/theme/gnome-shell-light.css >gnome-shell-light.css
  grep -Fq '.luma-secondary-status-text' gnome-shell-light.css
  grep -Fq '#panel.luma-handheld' gnome-shell-light.css
  grep -Fq 'height: 42px' gnome-shell-light.css
  grep -Fq 'min-height: 38px' gnome-shell-light.css
  grep -Fq 'icon-size: 17px' gnome-shell-light.css
  grep -Fq 'background-color: #ffffff' gnome-shell-light.css
  grep -Fq '.luma-handheld .keyboard-key:active' gnome-shell-light.css
  grep -Fxq '.luma-handheld .keyboard-key:active {' gnome-shell-light.css
  ! grep -Fq '  .luma-handheld .keyboard-key:active' gnome-shell-light.css
  grep -Fq 'transition-duration: 0ms' gnome-shell-light.css
  grep -Fq '.prairie-login-clock-time' gnome-shell-light.css
  grep -aFq 'showOverviewOnStartup: false' usr/lib64/gnome-shell/libshell-18.so
  grep -aFq 'BeginApplicationDrag' usr/lib64/gnome-shell/libshell-18.so
  grep -aFq 'screen-keyboard-enabled' usr/lib64/gnome-shell/libshell-18.so
  grep -aFq 'LUMA_DEVICE_CLASS' usr/lib64/gnome-shell/libshell-18.so
  grep -aFq 'HANDHELD_CARD_STEP_RATIO' usr/lib64/gnome-shell/libshell-18.so
  grep -aFq 'requestClose()' usr/lib64/gnome-shell/libshell-18.so
  grep -aFq 'setHandheldMode(handheldMode)' usr/lib64/gnome-shell/libshell-18.so
  grep -aFq 'HANDHELD_ACTIVITY_VIEW_USES_DESKTOP_CHROME' \
    usr/lib64/gnome-shell/libshell-18.so
  grep -aFq 'const NOTIFICATION_TIMEOUT = 7000' \
    usr/lib64/gnome-shell/libshell-18.so
  grep -aFq 'const cutoutHalfWidth = this._handheldPosture ? 28 * scaleFactor : 0' \
    usr/lib64/gnome-shell/libshell-18.so
)

bundle_dir="$output_dir/bundle"
install -d -m 0755 "$bundle_dir"
install -m 0644 "$shell_rpm" "$bundle_dir/$shell_filename"
install -m 0644 "$common_rpm" "$bundle_dir/$common_filename"
install -m 0644 "$srpm_output" "$output_dir/$(basename "$srpm_output")"

shell_sha=$(sha256sum "$bundle_dir/$shell_filename" | awk '{print $1}')
common_sha=$(sha256sum "$bundle_dir/$common_filename" | awk '{print $1}')
srpm_sha=$(sha256sum "$output_dir/$(basename "$srpm_output")" | awk '{print $1}')
runtime_sha=$(sha256sum "$verify_dir/usr/lib64/gnome-shell/libshell-18.so" | awk '{print $1}')

{
  printf 'LUMA_FP6_SHELL_BUILD_VERSION=1\n'
  printf 'FEDORA_RELEASE=44\n'
  printf 'ARCHITECTURE=aarch64\n'
  printf 'GNOME_SHELL_VERSION=50.3\n'
  printf 'LUMA_RELEASE=%s\n' "$GNOME_SHELL_LUMA_RELEASE"
  printf 'SOURCE_RPM_SHA256=%s\n' "$GNOME_SHELL_SRPM_SHA256"
  printf 'SHELL_RPM=%s\n' "$shell_filename"
  printf 'SHELL_RPM_SHA256=%s\n' "$shell_sha"
  printf 'COMMON_RPM=%s\n' "$common_filename"
  printf 'COMMON_RPM_SHA256=%s\n' "$common_sha"
  printf 'SRPM_SHA256=%s\n' "$srpm_sha"
  printf 'RUNTIME_LIBSHELL_SHA256=%s\n' "$runtime_sha"
  printf 'NATIVE_OSK_PRESENT=true\n'
  printf 'STEVIA_RESCUE_REQUIRED=true\n'
  printf 'RPM_SIGNATURE=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PACKAGES_INSTALLED=false\n'
  printf 'SESSION_STARTED=false\n'
} >"$output_dir/manifest.env"

chmod 0644 "$output_dir/manifest.env"
printf 'FP6 Luma Shell AArch64 bundle: %s\n' "$output_dir"
