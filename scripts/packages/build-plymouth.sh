#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio curl git mktemp podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Plymouth build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Luma Plymouth RPMs on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/plymouth"
mkdir -p "$cache_dir" "$output_dir"
srpm="$cache_dir/$PLYMOUTH_SRPM"

if [ ! -f "$srpm" ]; then
  curl -fL --retry 3 -o "$srpm" "$PLYMOUTH_SRPM_URL"
fi
printf '%s  %s\n' "$PLYMOUTH_SRPM_SHA256" "$srpm" |
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
mv "$extract_dir/plymouth.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
install -m 0644 "$repo_root/patches/plymouth/0001-prairie-light-details.patch" \
  "$repo_root/patches/plymouth/0002-script-firmware-background.patch" \
  "$repo_root/patches/plymouth/0003-script-get-time.patch" \
  "$repo_root/patches/plymouth/0004-device-manager-retry-drm-outputs.patch" \
  "$rpmbuild_dir/SOURCES/"
(
  cd "$rpmbuild_dir/SPECS"
  GIT_CEILING_DIRECTORIES="$rpmbuild_dir" git apply "$repo_root/patches/plymouth/0000-luma-fedora-spec.patch"
)
# A spec patch that applied as a no-op builds the stock package; the Release
# says whether it applied (scripts/packages/spec-release.sh).
. "$repo_root/scripts/packages/spec-release.sh"
luma_assert_spec_release "$rpmbuild_dir/SPECS/plymouth.spec" \
  "$PLYMOUTH_LUMA_RELEASE" || exit 1

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y builddep SPECS/plymouth.spec
    rpmbuild -ba --define "_topdir $PWD" SPECS/plymouth.spec
    rpm_path=$(find RPMS/x86_64 -type f \
      -name "plymouth-24.004.60-'"$PLYMOUTH_LUMA_RELEASE"'.fc44.x86_64.rpm" -print -quit)
    verify_dir=$(mktemp -d "$PWD/verify.XXXXXX")
    cd "$verify_dir"
    rpm2cpio "$OLDPWD/$rpm_path" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio
    # grep -q would close the pipe early and fail it under pipefail.
    # Luma selects its splash in /usr, not in the /etc file an administrator edits.
    grep -Fx Theme=luma-loading usr/share/plymouth/plymouthd.defaults >/dev/null
    strings usr/lib64/plymouth/details.so |
      grep -F "using Prairie light details palette" >/dev/null
    script_rpm=$(find "$OLDPWD/RPMS/x86_64" -type f \
      -name "plymouth-plugin-script-24.004.60-'"$PLYMOUTH_LUMA_RELEASE"'.fc44.x86_64.rpm" -print -quit)
    test -n "$script_rpm"
    mkdir script-plugin
    # Through a file, as above: cpio stops at the trailer and would leave
    # rpm2cpio writing into a closed pipe.
    rpm2cpio "$script_rpm" >script-plugin.cpio
    (cd script-plugin && cpio -idm --quiet <../script-plugin.cpio)
    rm -f script-plugin.cpio
    for symbol in SetFirmwareBackgroundOpacity GetFirmwareBackgroundOpacity HasFirmwareBackground \
      GetTime "keeping %dx%d firmware boot logo"; do
      strings script-plugin/usr/lib64/plymouth/script.so | grep -F -- "$symbol" >/dev/null
    done
  '

install -d -m 0755 "$output_dir/RPMS/x86_64" "$output_dir/SRPMS"
find "$rpmbuild_dir/RPMS/x86_64" -type f -name '*.rpm' \
  -exec install -m 0644 -t "$output_dir/RPMS/x86_64" {} +
install -m 0644 "$rpmbuild_dir/SRPMS/plymouth-24.004.60-${PLYMOUTH_LUMA_RELEASE}.fc44.src.rpm" \
  "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma Plymouth packages: %s\n' "$output_dir"
