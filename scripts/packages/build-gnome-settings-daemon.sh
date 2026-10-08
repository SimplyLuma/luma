#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
# Fedora's gnome-settings-daemon with Luma's patches: a volume change from the
# keyboard makes no sound, and the memory notice names the app systemd-oomd
# stopped, once (patches/gnome-settings-daemon/README.md).
srpm_name=gnome-settings-daemon-50.1-1.fc44.src.rpm
srpm_url=https://kojipkgs.fedoraproject.org/packages/gnome-settings-daemon/50.1/1.fc44/src/gnome-settings-daemon-50.1-1.fc44.src.rpm
srpm_sha256=d9847e1c2234ac5f22a4cb9157785662eaddad9d0d6588ddd2d8cd23f30037c9

for tool in cpio curl git mktemp podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required gnome-settings-daemon build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] || {
  printf 'error: build the Luma gnome-settings-daemon on a Fedora Linux builder\n' >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported gnome-settings-daemon architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/gnome-settings-daemon/$architecture"
mkdir -p "$cache_dir" "$output_dir"
srpm="$cache_dir/$srpm_name"
if [ ! -f "$srpm" ]; then
  curl -fL --retry 3 -o "$srpm" "$srpm_url"
fi
printf '%s  %s\n' "$srpm_sha256" "$srpm" | sha256sum --check --status || {
  printf 'error: gnome-settings-daemon source RPM checksum mismatch\n' >&2
  exit 1
}

work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
extract_dir="$work_dir/srpm"
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$extract_dir" "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
rpm2cpio "$srpm" >"$work_dir/gnome-settings-daemon.srpm.cpio"
(
  cd "$extract_dir"
  cpio -idm --quiet <"$work_dir/gnome-settings-daemon.srpm.cpio"
)
mv "$extract_dir/gnome-settings-daemon.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
for patch in "$repo_root"/patches/gnome-settings-daemon/00[0-9][0-9]-*.patch; do
  install -m 0644 "$patch" "$rpmbuild_dir/SOURCES/"
done
(
  cd "$rpmbuild_dir/SPECS"
  GIT_CEILING_DIRECTORIES="$rpmbuild_dir" git apply "$repo_root/patches/gnome-settings-daemon/0000-luma-fedora-spec.patch"
)
# A spec patch that applied as a no-op builds the stock package; the Release
# says whether it applied (scripts/packages/spec-release.sh).
. "$repo_root/scripts/packages/spec-release.sh"
# The spec patch is the only place this release is kept.
luma_assert_spec_release "$rpmbuild_dir/SPECS/gnome-settings-daemon.spec" \
  "$(luma_patch_release "$repo_root/patches/gnome-settings-daemon/0000-luma-fedora-spec.patch")" || exit 1

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y builddep SPECS/gnome-settings-daemon.spec
    rpmbuild -ba --define "_topdir $PWD" SPECS/gnome-settings-daemon.spec
    # The one-shot builder passes no environment in; ask rpm.
    architecture=$(rpm --eval "%{_arch}")
    release=$(rpmspec -q --srpm --qf "%{release}" SPECS/gnome-settings-daemon.spec)
    rpm="RPMS/$architecture/gnome-settings-daemon-50.1-${release}.${architecture}.rpm"
    test -f "$rpm"
    verify_dir=$(mktemp -d "$PWD/verify.XXXXXX")
    (
      cd "$verify_dir"
      rpm2cpio "$OLDPWD/$rpm" | cpio -idm --quiet ./usr/libexec/gsd-media-keys ./usr/libexec/gsd-housekeeping
      # Every check says what failed; a bare command under set -e would end
      # the build with no output, and a bare "! grep" never fails at all.
      need() { grep -aFq "$1" "$2" || { printf "FAIL: missing \"%s\" in %s\n" "$1" "$2" >&2; exit 1; }; }
      refuse() { if grep -aFq "$1" "$2"; then printf "FAIL: still present \"%s\" in %s\n" "$1" "$2" >&2; exit 1; fi; }
      for binary in gsd-media-keys gsd-housekeeping; do
        test -x "./usr/libexec/$binary" || { printf "FAIL: not packaged: %s\n" "$binary" >&2; exit 1; }
      done
      # The volume keys still change the volume and show the level...
      need "volume-step" ./usr/libexec/gsd-media-keys
      need "volume-up-quiet" ./usr/libexec/gsd-media-keys
      # ...and no longer play the volume change sound.
      refuse "audio-volume-change" ./usr/libexec/gsd-media-keys
      refuse "volume changed through key press" ./usr/libexec/gsd-media-keys
      # The memory notice names what was stopped and comes from Monitor...
      need "%s was stopped because memory was full." ./usr/libexec/gsd-housekeeping
      need "A background task" ./usr/libexec/gsd-housekeeping
      need "io.luma.Monitor" ./usr/libexec/gsd-housekeeping
      # ...instead of the anonymous upstream text.
      refuse "An application was using a lot of memory" ./usr/libexec/gsd-housekeeping
    )
    rm -rf "$verify_dir"
    printf "Packaged gnome-settings-daemon volume sound removal and memory notice: PASS\n"
  '

rm -rf "$output_dir/RPMS" "$output_dir/SRPMS"
mkdir -p "$output_dir/RPMS" "$output_dir/SRPMS"
find "$rpmbuild_dir/RPMS" -type f -name '*.rpm' -exec install -m 0644 {} "$output_dir/RPMS/" \;
find "$rpmbuild_dir/SRPMS" -type f -name '*.rpm' -exec install -m 0644 {} "$output_dir/SRPMS/" \;
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)
printf 'Luma gnome-settings-daemon: %s\n' "$output_dir"
