#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Fedora's xdg-desktop-portal with Luma's OpenURI policy for package files
# (patches/xdg-desktop-portal). Rebuilt from the SRPM pinned in
# config/desktop/inputs.env, with the upstream OpenURI integration tests as
# %check.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
package=xdg-desktop-portal

for tool in cpio curl git mktemp podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required %s build tool is missing: %s\n' "$package" "$tool" >&2
    exit 1
  }
done
[ "$(uname -s)" = Linux ] || {
  printf 'error: build %s on a Fedora Linux builder\n' "$package" >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported %s architecture: %s\n' "$package" "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture
nevr=$package-$XDG_DESKTOP_PORTAL_VERSION-$XDG_DESKTOP_PORTAL_LUMA_RELEASE.fc44

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/$package/$architecture"
mkdir -p "$cache_dir" "$output_dir"
srpm="$cache_dir/$XDG_DESKTOP_PORTAL_SRPM"
[ -f "$srpm" ] || curl -fL --retry 3 -o "$srpm" "$XDG_DESKTOP_PORTAL_SRPM_URL"
printf '%s  %s\n' "$XDG_DESKTOP_PORTAL_SRPM_SHA256" "$srpm" | sha256sum --check --status || {
  printf 'error: %s source RPM checksum mismatch\n' "$package" >&2
  exit 1
}

work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
extract_dir="$work_dir/srpm"
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$extract_dir" "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
rpm2cpio "$srpm" >"$work_dir/srpm.cpio"
( cd "$extract_dir" && cpio -idm --quiet <"$work_dir/srpm.cpio" )
mv "$extract_dir/$package.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
install -m 0644 "$repo_root/scripts/packages/check-rpm-directory-modes.py" "$rpmbuild_dir/SOURCES/"
for patch in "$repo_root"/patches/$package/0[0-9][0-9][0-9]-*.patch; do
  case $patch in */0000-luma-fedora-spec.patch) continue ;; esac
  install -m 0644 "$patch" "$rpmbuild_dir/SOURCES/"
done
(
  cd "$rpmbuild_dir/SPECS"
  GIT_CEILING_DIRECTORIES="$rpmbuild_dir" git apply "$repo_root/patches/$package/0000-luma-fedora-spec.patch"
)
# A spec patch that applied as a no-op builds the stock package; the Release
# says whether it applied (scripts/packages/spec-release.sh).
. "$repo_root/scripts/packages/spec-release.sh"
luma_assert_spec_release "$rpmbuild_dir/SPECS/$package.spec" \
  "$XDG_DESKTOP_PORTAL_LUMA_RELEASE" || exit 1

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" '
    set -euo pipefail
    architecture=$(rpm --eval "%{_arch}")
    dnf5 -y install rpm-build dnf5-plugins cpio
    dnf5 -y builddep SPECS/'"$package"'.spec
    rpmbuild -ba --define "_topdir $PWD" SPECS/'"$package"'.spec
    # The Luma patch is in the source package and the binaries were built.
    rpm -qpl "SRPMS/'"$nevr"'.src.rpm" |
      grep -qxF 0001-open-uri-open-package-files-in-the-system-installer.patch
    rpm -qpl "SRPMS/'"$nevr"'.src.rpm" |
      grep -qxF 0002-request-run-disconnect-off-the-main-loop.patch
    rpm -qpl "SRPMS/'"$nevr"'.src.rpm" |
      grep -qxF 0003-open-uri-open-files-in-their-chosen-default.patch
    test -f "RPMS/$architecture/'"$nevr"'.${architecture}.rpm"
    # The portal reads its impl files as the user: no build-workspace modes.
    while IFS= read -r rpm_path; do
      rpm -qp --qf "[%{FILEMODES:octal} %{FILENAMES}\n]" "$rpm_path" >payload-modes.txt
      python3 SOURCES/check-rpm-directory-modes.py payload-modes.txt
    done < <(find RPMS -type f -name "*.rpm" ! -name "*-debuginfo-*" ! -name "*-debugsource-*" -print)
    # The portal binary carries the policy.
    verify_dir=$(mktemp -d "$PWD/verify.XXXXXX")
    ( cd "$verify_dir" &&
      rpm2cpio "$OLDPWD/RPMS/$architecture/'"$nevr"'.${architecture}.rpm" | cpio -idm --quiet )
    grep -qaF /usr/share/luma/open-directly.list "$verify_dir/usr/libexec/xdg-desktop-portal"
    rm -rf "$verify_dir"
  '

rm -rf "$output_dir/RPMS" "$output_dir/SRPMS"
mkdir -p "$output_dir/RPMS" "$output_dir/SRPMS"
find "$rpmbuild_dir/RPMS" -type f -name '*.rpm' ! -name '*-debuginfo-*' ! -name '*-debugsource-*' \
  -exec install -m 0644 {} "$output_dir/RPMS/" \;
install -m 0644 "$rpmbuild_dir/SRPMS/$nevr.src.rpm" "$output_dir/SRPMS/"
( cd "$output_dir" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS )
printf 'Luma %s packages: %s\n' "$package" "$output_dir"
