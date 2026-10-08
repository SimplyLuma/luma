#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Fedora's fwupd with Luma's modem-manager fix (patches/fwupd). Rebuilt from the
# pinned Fedora SRPM with its full %check.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio curl git mktemp podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required fwupd build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Luma fwupd RPMs on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/fwupd"
mkdir -p "$cache_dir" "$output_dir"
srpm="$cache_dir/$FWUPD_SRPM"

if [ ! -f "$srpm" ]; then
  curl -fL --retry 3 -o "$srpm" "$FWUPD_SRPM_URL"
fi
printf '%s  %s\n' "$FWUPD_SRPM_SHA256" "$srpm" |
  sha256sum --check --status

work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
extract_dir="$work_dir/srpm"
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$extract_dir" "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
(
  cd "$extract_dir"
  rpm2cpio "$srpm" >payload.cpio
  cpio -idm --quiet <payload.cpio
  rm -f payload.cpio
)
mv "$extract_dir/fwupd.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
install -m 0644 \
  "$repo_root/patches/fwupd/0001-modem-manager-mhi-firehose-prepare-before-detach.patch" \
  "$rpmbuild_dir/SOURCES/"
(
  cd "$rpmbuild_dir/SPECS"
  GIT_CEILING_DIRECTORIES="$rpmbuild_dir" git apply "$repo_root/patches/fwupd/0000-luma-fedora-spec.patch"
)
# A spec patch that applied as a no-op builds the stock package; the Release
# says whether it applied (scripts/packages/spec-release.sh).
. "$repo_root/scripts/packages/spec-release.sh"
luma_assert_spec_release "$rpmbuild_dir/SPECS/fwupd.spec" \
  "$FWUPD_LUMA_RELEASE" || exit 1

bash "$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins binutils
    dnf5 -y builddep SPECS/fwupd.spec
    # Build inside the container: meson runs the programs it compiles, and a
    # shared build volume may be mounted noexec.
    top=$(mktemp -d /tmp/fwupd-rpmbuild.XXXXXX)
    mkdir -p "$top"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
    cp -a SOURCES/. "$top/SOURCES/"
    cp -a SPECS/. "$top/SPECS/"
    rpmbuild -ba --define "_topdir $top" "$top/SPECS/fwupd.spec"
    cp -a "$top/RPMS/." RPMS/
    cp -a "$top/SRPMS/." SRPMS/
    rm -rf "$top"
    for name in fwupd fwupd-plugin-modem-manager; do
      test -f "RPMS/x86_64/$name-'"$FWUPD_VERSION-$FWUPD_LUMA_RELEASE"'.fc44.x86_64.rpm"
    done
    verify_dir=$(mktemp -d "$PWD/verify.XXXXXX")
    cd "$verify_dir"
    for rpm in "$OLDPWD"/RPMS/x86_64/fwupd-'"$FWUPD_VERSION-$FWUPD_LUMA_RELEASE"'.fc44.x86_64.rpm \
               "$OLDPWD"/RPMS/x86_64/fwupd-plugin-modem-manager-'"$FWUPD_VERSION-$FWUPD_LUMA_RELEASE"'.fc44.x86_64.rpm; do
      rpm2cpio "$rpm" >payload.cpio
      cpio -idm --quiet <payload.cpio
      rm -f payload.cpio
    done
    # The fixed detach names its failure instead of leaving it unset.
    found=0
    while IFS= read -r -d "" file; do
      if strings "$file" | grep -F "Firehose prog was not loaded from the firmware archive" >/dev/null; then
        found=1
      fi
    done < <(find . -type f \( -name "*.so*" -o -path "*/libexec/fwupd/*" \) -print0)
    test "$found" = 1
  '

install -d -m 0755 "$output_dir/RPMS" "$output_dir/SRPMS"
find "$rpmbuild_dir/RPMS" -type f -name '*.rpm' \
  -exec install -m 0644 -t "$output_dir/RPMS" {} +
install -m 0644 "$rpmbuild_dir/SRPMS/fwupd-${FWUPD_VERSION}-${FWUPD_LUMA_RELEASE}.fc44.src.rpm" \
  "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma fwupd packages: %s\n' "$output_dir"
