#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
# This is a bounded non-release candidate while ADR-003 and native graphical
# evidence remain open. It deliberately does not update the accepted tuple.
srpm_name=mutter-50.4-1.fc44.src.rpm
srpm_url=https://kojipkgs.fedoraproject.org/packages/mutter/50.4/1.fc44/src/mutter-50.4-1.fc44.src.rpm
srpm_sha256=e11d5b0cb6d7736b10bbbdc6867037dc2a32783e0576f38a11563f1a47b9d3e2
protocol_sha256=e463b7863c97d7be05489b52b15cd0b3a5d8290b51340f62023b47d03b4217e3

for tool in cpio curl git mktemp podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Mutter build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] || {
  printf 'error: build the Luma Mutter candidate on a Fedora Linux builder\n' >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported Mutter architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/mutter/$architecture"
mkdir -p "$cache_dir" "$output_dir"
srpm="$cache_dir/$srpm_name"
if [ ! -f "$srpm" ]; then
  curl -fL --retry 3 -o "$srpm" "$srpm_url"
fi
printf '%s  %s\n' "$srpm_sha256" "$srpm" | sha256sum --check --status || {
  printf 'error: Mutter source RPM checksum mismatch\n' >&2
  exit 1
}
protocol="$repo_root/src/luma-platform/protocols/ext-background-effect-v1.xml"
printf '%s  %s\n' "$protocol_sha256" "$protocol" | sha256sum --check --status || {
  printf 'error: standardized background-effect protocol checksum mismatch\n' >&2
  exit 1
}

work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
extract_dir="$work_dir/srpm"
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$extract_dir" "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
rpm2cpio "$srpm" >"$work_dir/mutter.srpm.cpio"
(
  cd "$extract_dir"
  cpio -idm --quiet <"$work_dir/mutter.srpm.cpio"
)
mv "$extract_dir/mutter.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
install -m 0644 "$protocol" "$rpmbuild_dir/SOURCES/ext-background-effect-v1.xml"
for patch in "$repo_root"/patches/mutter/00[0-9][0-9]-*.patch; do
  install -m 0644 "$patch" "$rpmbuild_dir/SOURCES/"
done
(
  cd "$rpmbuild_dir/SPECS"
  GIT_CEILING_DIRECTORIES="$rpmbuild_dir" git apply "$repo_root/patches/mutter/0000-luma-fedora-spec.patch"
)
# A spec patch that applied as a no-op builds the stock package; the Release
# says whether it applied (scripts/packages/spec-release.sh).
. "$repo_root/scripts/packages/spec-release.sh"
# The spec patch is the only place this release is kept.
luma_assert_spec_release "$rpmbuild_dir/SPECS/mutter.spec" \
  "$(luma_patch_release "$repo_root/patches/mutter/0000-luma-fedora-spec.patch")" || exit 1

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y builddep SPECS/mutter.spec
    rpmbuild -ba --noclean --define "_lto_cflags -flto=2" \
      --define "_topdir $PWD" SPECS/mutter.spec
    architecture=${LUMA_RPM_BUILDER_ARCHITECTURE}
    release=$(rpmspec -q --srpm --qf "%{release}" SPECS/mutter.spec)
    rpm=$(find "RPMS/$architecture" -type f -name "mutter-50.4-${release}.*.rpm" \
      ! -name "*-devel-*" ! -name "*-tests-*" ! -name "*-debuginfo-*" \
      ! -name "*-debugsource-*" -print -quit)
    test -n "$rpm"
    rpm -qpl "$rpm" >/dev/null
    verify_dir=$(mktemp -d "$PWD/verify.XXXXXX")
    (
      cd "$verify_dir"
      rpm2cpio "$OLDPWD/$rpm" |
        cpio -idm --quiet ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "LUMA_MUTTER_ALLOW_SOFTWARE_BLUR" \
        ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "meta_window_set_externally_tiled" \
        ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "meta_display_set_work_area_inset" \
        ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "Sending pointer-focus modifiers" \
        ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "on the monitor of its parent" \
        ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "inside work area" \
        ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "Background blur backdrop cache disabled by environment" \
        ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "meta_display_set_monitor_edge_reservation" \
        ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "meta_display_clear_monitor_edge_reservations" \
        ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "org.projectluma.peripherals.touchpad" \
        ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "scroll speed stays at the default" \
        ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "the shell took or gave back the keyboard" \
        ./usr/lib64/libmutter-18.so.0.0.0
      grep -aFq "keeps its pre-configured frame" \
        ./usr/lib64/libmutter-18.so.0.0.0
      if grep -aFq "Dropped %u key event" ./usr/lib64/libmutter-18.so.0.0.0; then
        echo "error: this Mutter still drops keys typed during a focus handover" >&2
        exit 1
      fi
    )
    rm -rf "$verify_dir"
  '

rm -rf "$output_dir/RPMS" "$output_dir/SRPMS"
mkdir -p "$output_dir/RPMS" "$output_dir/SRPMS"
find "$rpmbuild_dir/RPMS" -type f -name '*.rpm' -exec install -m 0644 {} "$output_dir/RPMS/" \;
find "$rpmbuild_dir/SRPMS" -type f -name '*.rpm' -exec install -m 0644 {} "$output_dir/SRPMS/" \;
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)
printf 'Non-release Mutter surface candidate: %s\n' "$output_dir"
