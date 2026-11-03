#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# gdm: Fedora's gdm-50.3-1.fc44.src.rpm with the Luma patch series in patches/gdm
# (0000-luma-fedora-spec.patch adds the others and the .luma release).

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
package=gdm
srpm_name=gdm-50.3-1.fc44.src.rpm
srpm_url=https://kojipkgs.fedoraproject.org/packages/gdm/50.3/1.fc44/src/gdm-50.3-1.fc44.src.rpm
srpm_sha256=5dac7b7cd45bdb8cbba6cb910038c5cc1c74a11f8e9de1c5a7e4848670dc29a3

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

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/$package/$architecture"
mkdir -p "$cache_dir" "$output_dir"
srpm="$cache_dir/$srpm_name"
[ -f "$srpm" ] || curl -fL --retry 3 -o "$srpm" "$srpm_url"
printf '%s  %s\n' "$srpm_sha256" "$srpm" | sha256sum --check --status || {
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
for patch in "$repo_root"/patches/$package/0[0-9][0-9][0-9]-*.patch; do
  case $patch in */0000-luma-fedora-spec.patch) continue ;; esac
  install -m 0644 "$patch" "$rpmbuild_dir/SOURCES/"
done
(
  cd "$rpmbuild_dir/SPECS"
  GIT_CEILING_DIRECTORIES="$rpmbuild_dir" git apply "$repo_root/patches/$package/0000-luma-fedora-spec.patch"
)
. "$repo_root/scripts/packages/spec-release.sh"
luma_assert_spec_release "$rpmbuild_dir/SPECS/$package.spec" \
  "$(luma_patch_release "$repo_root/patches/$package/0000-luma-fedora-spec.patch")" || exit 1

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y builddep SPECS/'"$package"'.spec
    rpmbuild -ba --define "_topdir $PWD" SPECS/'"$package"'.spec || {
      # Rust crates and other generated build requirements: install, then build.
      ls SRPMS/*.buildreqs.nosrc.rpm >/dev/null 2>&1 || exit 1
      dnf5 -y builddep SRPMS/*.buildreqs.nosrc.rpm
      rpmbuild -ba --define "_topdir $PWD" SPECS/'"$package"'.spec
    }
    # The Luma patches are in the source package and the binaries were built.
    srpm=$(find SRPMS -name "*.luma.*.src.rpm" -print -quit)
    test -n "$srpm"
    for patch in SOURCES/0[0-9][0-9][0-9]-*.patch; do
      rpm -qpl "$srpm" | grep -qxF "$(basename "$patch")"
    done
    test -n "$(find "RPMS/${LUMA_RPM_BUILDER_ARCHITECTURE}" -name "*.luma.*.rpm" -print -quit)"
  '

rm -rf "$output_dir/RPMS" "$output_dir/SRPMS"
mkdir -p "$output_dir/RPMS" "$output_dir/SRPMS"
find "$rpmbuild_dir/RPMS" -type f -name '*.rpm' ! -name '*-debuginfo-*' ! -name '*-debugsource-*' \
  -exec install -m 0644 {} "$output_dir/RPMS/" \;
find "$rpmbuild_dir/SRPMS" -type f -name '*.src.rpm' -exec install -m 0644 {} "$output_dir/SRPMS/" \;
( cd "$output_dir" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS )
printf 'Luma %s packages: %s\n' "$package" "$output_dir"
