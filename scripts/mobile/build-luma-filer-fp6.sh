#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Rebuild the already validated, shared Luma Filer source RPM natively for
# Fedora 44 AArch64. This creates an offline package bundle only; it never
# contacts a phone, installs a package, or changes a session.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio dnf5 mktemp rpm rpm2cpio rpmbuild sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required native AArch64 build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != aarch64 ]; then
  printf 'error: build the FP6 Luma Filer bundle on native Linux/aarch64\n' >&2
  exit 1
fi

output_dir=${1:-$repo_root/build/mobile/fp6-physical/luma-apps-aarch64/filer}
source_rpm=${2:-$repo_root/build/packages/nautilus/SRPMS/nautilus-50.2.2-${NAUTILUS_LUMA_RELEASE}.fc44.src.rpm}

[ -f "$source_rpm" ] || {
  printf 'error: validated shared Filer source RPM is missing: %s\n' "$source_rpm" >&2
  exit 1
}

mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"

dnf5 -y builddep "$source_rpm"
rpmbuild --rebuild --define "_topdir $rpmbuild_dir" "$source_rpm"

main_name="nautilus-50.2.2-${NAUTILUS_LUMA_RELEASE}.fc44.aarch64.rpm"
extensions_name="nautilus-extensions-50.2.2-${NAUTILUS_LUMA_RELEASE}.fc44.aarch64.rpm"
main_rpm="$rpmbuild_dir/RPMS/aarch64/$main_name"
extensions_rpm="$rpmbuild_dir/RPMS/aarch64/$extensions_name"

for artifact in "$main_rpm" "$extensions_rpm"; do
  [ -f "$artifact" ] || {
    printf 'error: expected Filer artifact is missing: %s\n' "$artifact" >&2
    exit 1
  }
done

verify_dir=$(mktemp -d "$output_dir/verify.XXXXXX")
(
  cd "$verify_dir"
  rpm2cpio "$main_rpm" | cpio -idm --quiet
  grep -aFq 'You’ve reached the system root' usr/bin/nautilus
  grep -aFq 'personal-storage-only' usr/bin/nautilus
  grep -aFq 'org.projectluma.ApplicationInstaller1' usr/bin/nautilus
  grep -aFq 'RequestInstall' usr/bin/nautilus
  glib-compile-schemas usr/share/glib-2.0/schemas
  test "$(GSETTINGS_SCHEMA_DIR=usr/share/glib-2.0/schemas \
    gsettings get org.gnome.nautilus.preferences personal-storage-only)" = false
)

bundle_dir="$output_dir/bundle"
install -d -m 0755 "$bundle_dir"
install -m 0644 "$main_rpm" "$bundle_dir/$main_name"
install -m 0644 "$extensions_rpm" "$bundle_dir/$extensions_name"

source_sha=$(sha256sum "$source_rpm" | awk '{print $1}')
main_sha=$(sha256sum "$bundle_dir/$main_name" | awk '{print $1}')
extensions_sha=$(sha256sum "$bundle_dir/$extensions_name" | awk '{print $1}')

{
  printf 'LUMA_FP6_FILER_BUILD_VERSION=1\n'
  printf 'FEDORA_RELEASE=44\n'
  printf 'ARCHITECTURE=aarch64\n'
  printf 'NAUTILUS_VERSION=50.2.2\n'
  printf 'LUMA_RELEASE=%s\n' "$NAUTILUS_LUMA_RELEASE"
  printf 'SOURCE_RPM_SHA256=%s\n' "$source_sha"
  printf 'MAIN_RPM=%s\n' "$main_name"
  printf 'MAIN_RPM_SHA256=%s\n' "$main_sha"
  printf 'EXTENSIONS_RPM=%s\n' "$extensions_name"
  printf 'EXTENSIONS_RPM_SHA256=%s\n' "$extensions_sha"
  printf 'PERSONAL_STORAGE_DEFAULT=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PACKAGES_INSTALLED=false\n'
} >"$output_dir/manifest.env"

chmod 0644 "$output_dir/manifest.env"
printf 'FP6 Luma Filer AArch64 bundle: %s\n' "$output_dir"
