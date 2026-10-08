#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
# Fedora's IBus with Luma's patches: the symbol picker (Super+.) finds
# accented letters by name, "e acute" and "eacute" (patches/ibus/README.md).
srpm_name=ibus-1.5.34-4.fc44.src.rpm
srpm_url=https://kojipkgs.fedoraproject.org/packages/ibus/1.5.34/4.fc44/src/ibus-1.5.34-4.fc44.src.rpm
srpm_sha256=114696cae9c98cfea17329e6682ab4bd64346c24bbded49b90702e5090875920

for tool in cpio curl git mktemp podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required IBus build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] || {
  printf 'error: build the Luma IBus on a Fedora Linux builder\n' >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported IBus architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/ibus/$architecture"
mkdir -p "$cache_dir" "$output_dir"
srpm="$cache_dir/$srpm_name"
if [ ! -f "$srpm" ]; then
  curl -fL --retry 3 -o "$srpm" "$srpm_url"
fi
printf '%s  %s\n' "$srpm_sha256" "$srpm" | sha256sum --check --status || {
  printf 'error: IBus source RPM checksum mismatch\n' >&2
  exit 1
}

work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
extract_dir="$work_dir/srpm"
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$extract_dir" "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
rpm2cpio "$srpm" >"$work_dir/ibus.srpm.cpio"
(
  cd "$extract_dir"
  cpio -idm --quiet <"$work_dir/ibus.srpm.cpio"
)
mv "$extract_dir/ibus.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
patch_count=0
for patch in "$repo_root"/patches/ibus/00[0-9][0-9]-*.patch; do
  install -m 0644 "$patch" "$rpmbuild_dir/SOURCES/"
  patch_count=$((patch_count + 1))
done
# 0000 (spec) and 0001 (picker names); a glob that matched nothing is a failure.
[ "$patch_count" -ge 2 ] || {
  printf 'error: expected at least 2 IBus patches, found %s\n' "$patch_count" >&2
  exit 1
}
(
  cd "$rpmbuild_dir/SPECS"
  git apply "$repo_root/patches/ibus/0000-luma-fedora-spec.patch"
)

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y builddep SPECS/ibus.spec
    rpmbuild -ba --define "_topdir $PWD" SPECS/ibus.spec 2>&1 | tee rpmbuild.log
    # The one-shot builder passes no environment in; ask rpm.
    architecture=$(rpm --eval "%{_arch}")
    release=$(rpmspec -q --srpm --qf "%{release}" SPECS/ibus.spec)
    fail() { printf "FAIL: %s\n" "$1" >&2; exit 1; }
    # %check ran the picker test against this build and it passed.
    grep -Fq "all 11 lookups passed" rpmbuild.log || fail "the Luma picker test did not pass in %check"
    # Every subpackage a Luma image or machine has installed: ibus requires
    # python3-ibus and ibus-libs of the same release, so the set ships whole.
    for name in ibus ibus-libs ibus-gtk3 ibus-gtk4 python3-ibus; do
      rpm="RPMS/$architecture/$name-1.5.34-${release}.${architecture}.rpm"
      test -f "$rpm" || fail "not built: $rpm"
    done
    test -f "RPMS/noarch/ibus-setup-1.5.34-${release}.noarch.rpm" || fail "not built: ibus-setup"
    verify_dir=$(mktemp -d "$PWD/verify.XXXXXX")
    (
      cd "$verify_dir"
      rpm2cpio "$OLDPWD/RPMS/$architecture/ibus-1.5.34-${release}.${architecture}.rpm" |
        cpio -idm --quiet ./usr/libexec/ibus-extension-gtk3 ./usr/share/ibus/dicts/unicode-names.dict
      test -x ./usr/libexec/ibus-extension-gtk3 || fail "ibus-extension-gtk3 not packaged"
      test -s ./usr/share/ibus/dicts/unicode-names.dict || fail "unicode-names.dict not packaged"
    )
    rm -rf "$verify_dir"
    printf "Packaged IBus picker letter names: PASS\n"
  '

rm -rf "$output_dir/RPMS" "$output_dir/SRPMS"
mkdir -p "$output_dir/RPMS" "$output_dir/SRPMS"
find "$rpmbuild_dir/RPMS" -type f -name '*.rpm' -exec install -m 0644 {} "$output_dir/RPMS/" \;
find "$rpmbuild_dir/SRPMS" -type f -name '*.rpm' -exec install -m 0644 {} "$output_dir/SRPMS/" \;
rpm_count=$(find "$output_dir/RPMS" -type f -name '*.rpm' | wc -l)
[ "$rpm_count" -ge 5 ] || {
  printf 'error: expected the IBus RPMs in %s, found %s\n' "$output_dir/RPMS" "$rpm_count" >&2
  exit 1
}
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)
printf 'Luma IBus: %s (%s RPMs)\n' "$output_dir" "$rpm_count"
