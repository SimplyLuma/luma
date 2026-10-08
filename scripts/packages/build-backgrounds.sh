#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required backgrounds package build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the backgrounds RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

prism_asset="$repo_root/assets/wallpapers/prism.png"
handheld_prism_asset="$repo_root/assets/wallpapers/luma-prism.png"
meadow_asset="$repo_root/assets/wallpapers/meadow.png"
ember_asset="$repo_root/assets/wallpapers/ember.png"
printf '%s  %s\n' "$LUMA_PRISM_HANDHELD_SHA256" "$handheld_prism_asset" |
  sha256sum --check --status || {
    printf 'error: handheld Prism asset checksum mismatch\n' >&2
    exit 1
  }
printf '%s  %s\n' "$LUMA_PRISM_SHA256" "$prism_asset" |
  sha256sum --check --status || {
    printf 'error: Prism checksum mismatch\n' >&2
    exit 1
  }
printf '%s  %s\n' "$LUMA_MEADOW_SHA256" "$meadow_asset" |
  sha256sum --check --status || {
    printf 'error: Meadow checksum mismatch\n' >&2
    exit 1
  }
printf '%s  %s\n' "$LUMA_EMBER_SHA256" "$ember_asset" |
  sha256sum --check --status || {
    printf 'error: Ember checksum mismatch\n' >&2
    exit 1
  }

output_dir="$repo_root/build/packages/backgrounds"
mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir/BUILD" "$rpmbuild_dir/BUILDROOT" \
  "$rpmbuild_dir/RPMS" "$rpmbuild_dir/SOURCES" \
  "$rpmbuild_dir/SPECS" "$rpmbuild_dir/SRPMS"

install -m 0644 "$prism_asset" "$rpmbuild_dir/SOURCES/prism.png"
install -m 0644 "$handheld_prism_asset" "$rpmbuild_dir/SOURCES/luma-prism.png"
install -m 0644 "$meadow_asset" "$rpmbuild_dir/SOURCES/meadow.png"
install -m 0644 "$ember_asset" "$rpmbuild_dir/SOURCES/ember.png"
install -m 0644 "$repo_root/assets/wallpapers/luma-backgrounds.xml" \
  "$rpmbuild_dir/SOURCES/luma-backgrounds.xml"
install -m 0644 "$repo_root/assets/wallpapers/README.md" \
  "$rpmbuild_dir/SOURCES/README.md"
install -m 0644 "$repo_root/packaging/rpm/luma-backgrounds.spec" \
  "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install rpm-build cpio
    rpmbuild -ba --define "_topdir $PWD" SPECS/luma-backgrounds.spec
    rpm_path=$(find RPMS/noarch -maxdepth 1 -type f \
      -name "luma-backgrounds-*.rpm" -print -quit)
    test -n "$rpm_path"
    verify_dir=$(mktemp -d)
    cd "$verify_dir"
    rpm2cpio "$OLDPWD/$rpm_path" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio
    printf "%s  %s\n" \
      74cf2d0da550e8f9cb4027974bea11b743ecf5ef6c5986bd1c6975a93780781d \
      ./usr/share/backgrounds/luma/prism.png |
      sha256sum --check --status
    printf "%s  %s\n" \
      298a0a1ee9d8f6cb03352a2c19c797ac9ded267b3f4088300703693ea3e65a98 \
      ./usr/share/backgrounds/luma/meadow.png |
      sha256sum --check --status
    printf "%s  %s\n" \
      3c69d755e32eaeddd526572abb3a87cb0051dc935eae9d24b2b5fa47e874dd12 \
      ./usr/share/backgrounds/luma/ember.png |
      sha256sum --check --status
    test ! -e ./usr/share/backgrounds/luma/luma-mesh-gradient.jpg
    test -s ./usr/share/backgrounds/luma/luma-prism.png
    test "$(grep -c "<wallpaper deleted=" ./usr/share/gnome-background-properties/luma-backgrounds.xml)" = 3
    grep -Fq "<name>Prism</name>" ./usr/share/gnome-background-properties/luma-backgrounds.xml
    grep -Fq "<name>Meadow</name>" ./usr/share/gnome-background-properties/luma-backgrounds.xml
    grep -Fq "<name>Ember</name>" ./usr/share/gnome-background-properties/luma-backgrounds.xml

  '

stable_rpms="$output_dir/RPMS/noarch"
stable_srpms="$output_dir/SRPMS"
install -d -m 0755 "$stable_rpms" "$stable_srpms"
install -m 0644 "$rpmbuild_dir/RPMS/noarch/$LUMA_BACKGROUNDS_NEVRA.rpm" \
  "$stable_rpms/"
install -m 0644 "$rpmbuild_dir"/SRPMS/luma-backgrounds-*.src.rpm \
  "$stable_srpms/"

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma backgrounds package: %s\n' "$output_dir"
