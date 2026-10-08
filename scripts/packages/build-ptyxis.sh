#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio dnf5 git mktemp podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required RPM build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Luma Terminal RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/ptyxis"
mkdir -p "$cache_dir" "$output_dir"

srpm="$cache_dir/$PTYXIS_SRPM"
if [ ! -f "$srpm" ]; then
  dnf5 download --source --destdir "$cache_dir" "ptyxis-50.1-2.fc44"
fi

printf '%s  %s\n' "$PTYXIS_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: Ptyxis source RPM checksum mismatch\n' >&2
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
  rpm2cpio "$srpm" >payload.cpio
  cpio -idm --quiet <payload.cpio
  rm -f payload.cpio
)

mv "$extract_dir/ptyxis.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
for patch in \
  "$repo_root/patches/ptyxis/0001-luma-terminal-native-kit.patch" \
  "$repo_root/patches/ptyxis/0002-luma-terminal-flat-toolbar.patch"; do
  install -m 0644 "$patch" "$rpmbuild_dir/SOURCES/"
done

# The spec lists the patches and carries the Luma release; %autosetup -p1
# applies them.
(
  cd "$rpmbuild_dir/SPECS"
  git apply "$repo_root/patches/ptyxis/0000-luma-fedora-spec.patch"
)

# The emulator is aarch64; the desktop image is x86_64. The builder container
# has to match whichever is being asked for.
builder_image=$FEDORA_RPM_BUILD_CONTAINER
if [ "${LUMA_RPM_BUILDER_ARCHITECTURE:-}" = aarch64 ]; then
  builder_image=$FEDORA_RPM_BUILD_CONTAINER_AARCH64
fi

# Consume the already built shared C platform, including its matching headers.
#
# The release comes from the architecture's own pin in config/desktop/inputs.env,
# not from LUMA_DEVELOPER_PLATFORM_RELEASE. That variable is the release this
# tree's spec builds, and an architecture may legitimately sit on an older
# qualified platform (aarch64 does): keying off it made this script look for a
# release that architecture never had, which is a build failure for no reason
# the package requires.
platform_arch=${LUMA_RPM_BUILDER_ARCHITECTURE:-x86_64}
case "$platform_arch" in
  x86_64) platform_nevra=$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA ;;
  aarch64) platform_nevra=$LUMA_DEVELOPER_PLATFORM_AARCH64_NEVRA ;;
  *) printf 'error: unsupported Luma Terminal platform architecture: %s\n' "$platform_arch" >&2; exit 1 ;;
esac
[ -n "${platform_nevra:-}" ] || {
  printf 'error: config/desktop/inputs.env pins no Luma Developer Platform for %s\n' "$platform_arch" >&2
  exit 1
}
platform_release=${platform_nevra#luma-developer-platform-0.1.0-}
platform_release=${platform_release%".fc44.$platform_arch"}
[ "$platform_release" != "$platform_nevra" ] || {
  printf 'error: cannot read a release out of the pinned platform package: %s\n' "$platform_nevra" >&2
  exit 1
}
platform_rpms="$repo_root/build/packages/luma-developer-platform/$platform_arch/RPMS"
[ -d "$platform_rpms" ] || {
  printf 'error: the Luma Developer Platform output directory is missing for %s: %s\n' \
    "$platform_arch" "$platform_rpms" >&2
  printf 'build %s before the Luma Terminal package\n' "$platform_nevra" >&2
  exit 1
}
mkdir -p "$rpmbuild_dir/BUILD-DEPS"
for name in luma-developer-platform luma-developer-platform-devel; do
  package=$(find "$platform_rpms" -maxdepth 1 -name "$name-0.1.0-$platform_release.fc44.$platform_arch.rpm" -print -quit)
  test -n "$package" || {
    printf 'error: build %s-0.1.0-%s for %s before the Luma Terminal package\n' \
      "$name" "$platform_release" "$platform_arch" >&2
    printf 'that is the release config/desktop/inputs.env pins; this build uses no other\n' >&2
    exit 1
  }
  install -m 0644 "$package" "$rpmbuild_dir/BUILD-DEPS/"
done

# Through bash rather than executed: a build tree can sit on a noexec mount,
# which is where this machine keeps the space.
bash "$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$builder_image" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y install BUILD-DEPS/*.rpm
    dnf5 -y builddep SPECS/ptyxis.spec

    # The spec retains Fedora hardening/LTO and caps internal LTO links.
    rpmbuild -ba --noclean --define "_topdir $PWD" \
      --define "_smp_mflags -j2" --define "_smp_build_ncpus 2" SPECS/ptyxis.spec

    topdir=$PWD
    main_rpm=$(find "$topdir/RPMS" -maxdepth 2 -type f \
      -name "ptyxis-[0-9]*.rpm" -print -quit)
    test -n "$main_rpm"
    verify_dir=$(mktemp -d)
    cd "$verify_dir"
    rpm2cpio "$main_rpm" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio

    # The window wears the kit, the strip exists, the palette ships, and the
    # shell calls it Terminal.
    gresource extract ./usr/bin/ptyxis /org/gnome/Ptyxis/style.css >style.css
    gresource extract ./usr/bin/ptyxis /org/gnome/Ptyxis/ptyxis-window.ui >window.ui
    grep -Fq "luma-titlebar" window.ui
    grep -Fq "luma-identity-button" window.ui
    grep -Fq "AdwHeaderBar" window.ui
    grep -Fq "luma-app-window" window.ui
    ! grep -Fq "GtkWindowControls" window.ui
    grep -Fq "luma-island" window.ui
    grep -Fq "LumaTabStrip" window.ui
    grep -Fq "lumatabstrip" style.css
    ! grep -Fq "!important" style.css
    gresource extract ./usr/bin/ptyxis /org/gnome/Ptyxis/palettes/luma.palette >luma.palette
    grep -Fq "TitlebarBackground=#21252b" luma.palette
    grep -Fq "Name=Terminal" ./usr/share/applications/org.gnome.Ptyxis.desktop
    glib-compile-schemas ./usr/share/glib-2.0/schemas
  '

stable_rpms="$output_dir/RPMS/${LUMA_RPM_BUILDER_ARCHITECTURE:-x86_64}"
stable_srpms="$output_dir/SRPMS"
install -d -m 0755 "$stable_rpms" "$stable_srpms"
find "$rpmbuild_dir/RPMS" -type f -name '*.rpm' -exec install -m 0644 {} "$stable_rpms/" \;
find "$rpmbuild_dir/SRPMS" -type f -name '*.rpm' -exec install -m 0644 {} "$stable_srpms/" \;

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma Terminal packages: %s\n' "$output_dir"
