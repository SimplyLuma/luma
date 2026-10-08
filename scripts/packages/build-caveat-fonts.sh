#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in curl mktemp podman sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Caveat package build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] || {
  printf 'error: build the Caveat RPM on a Fedora Linux builder\n' >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported Caveat builder architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

output_dir="$repo_root/build/packages/caveat-fonts"
mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir/BUILD" "$rpmbuild_dir/BUILDROOT" \
  "$rpmbuild_dir/RPMS" "$rpmbuild_dir/SOURCES" \
  "$rpmbuild_dir/SPECS" "$rpmbuild_dir/SRPMS"

download_and_verify() {
  destination=$1
  url=$2
  expected=$3
  curl -L --fail --silent --show-error -o "$destination" "$url"
  printf '%s  %s\n' "$expected" "$destination" | sha256sum --check --status
}

source_base="https://raw.githubusercontent.com/google/fonts/$CAVEAT_GOOGLE_FONTS_COMMIT/ofl/caveat"
download_and_verify "$rpmbuild_dir/SOURCES/Caveat[wght].ttf" \
  "$source_base/Caveat%5Bwght%5D.ttf" "$CAVEAT_VARIABLE_SHA256"
download_and_verify "$rpmbuild_dir/SOURCES/OFL.txt" \
  "$source_base/OFL.txt" "$CAVEAT_OFL_SHA256"

install -m 0644 "$repo_root/packaging/rpm/google-caveat-fonts.spec" \
  "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$builder_container" '
    set -euo pipefail
    dnf5 -y install rpm-build cpio fontconfig fonts-rpm-macros
    rpmbuild -ba --define "_topdir $PWD" SPECS/google-caveat-fonts.spec
    rpm_path=$(find RPMS/noarch -maxdepth 1 -type f \
      -name "google-caveat-fonts-*.rpm" -print -quit)
    test -n "$rpm_path"
    verify_dir=$(mktemp -d)
    cd "$verify_dir"
    rpm2cpio "$OLDPWD/$rpm_path" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio
    fc-scan --format "%{family[0]}\n" ./usr/share/fonts/google-caveat-fonts/*.ttf |
      grep -Fxq Caveat
    # The variable weight axis is what the note body relies on for its bold
    # title weight; a static-only drop upstream must fail this build.
    fc-scan --format "%{variable}\n" \
      ./usr/share/fonts/google-caveat-fonts/*.ttf | grep -Fxq True
    grep -Fq "SIL OPEN FONT LICENSE Version 1.1" \
      ./usr/share/licenses/google-caveat-fonts/OFL.txt
    install_root=$(mktemp -d)
    dnf5 -y --installroot="$install_root" --releasever=44 --use-host-config \
      install fontconfig "$OLDPWD/$rpm_path"
    HOME=/tmp chroot "$install_root" \
      /usr/bin/fc-match --format="%{family[0]}" Caveat |
      grep -Fxq Caveat
    # Caveat must not take over any generic. This package ships no fontconfig
    # alias, so sans-serif has to keep resolving somewhere else.
    HOME=/tmp chroot "$install_root" \
      /usr/bin/fc-match --format="%{family[0]}" sans-serif |
      grep -Fxvq Caveat
  '

stable_rpms="$output_dir/RPMS/noarch"
stable_srpms="$output_dir/SRPMS"
install -d -m 0755 "$stable_rpms" "$stable_srpms"
install -m 0644 "$rpmbuild_dir/RPMS/noarch/$CAVEAT_NEVRA.rpm" "$stable_rpms/"
install -m 0644 "$rpmbuild_dir"/SRPMS/google-caveat-fonts-*.src.rpm \
  "$stable_srpms/"

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Caveat font package: %s\n' "$output_dir"
