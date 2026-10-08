#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Build intel-crashlog (Intel's iclg, MIT) from the pinned upstream release with
# its Rust dependencies vendored, then build the RPM offline. The vendor archive
# is made once per release and cached; it goes into the SRPM like any source.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

version=1.2.0
tarball=crashlog-$version.tar.gz
tarball_sha256=44bbb2272386c3241c9cb14271ef89429be1d5aecffa7aba3542ca0968348301
vendor=crashlog-$version-vendor.tar.xz

for tool in curl mktemp podman sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported build architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

cache_dir="$repo_root/build/sources"
mkdir -p "$cache_dir"
if ! printf '%s  %s\n' "$tarball_sha256" "$cache_dir/$tarball" | sha256sum -c --status 2>/dev/null; then
  curl -fL --retry 3 -o "$cache_dir/$tarball.part" \
    "https://github.com/intel/crashlog/archive/v$version/$tarball"
  printf '%s  %s\n' "$tarball_sha256" "$cache_dir/$tarball.part" | sha256sum -c --status || {
    printf 'error: %s does not match its pinned SHA-256\n' "$tarball" >&2
    rm -f "$cache_dir/$tarball.part"
    exit 1
  }
  mv "$cache_dir/$tarball.part" "$cache_dir/$tarball"
fi

output_dir="$repo_root/build/packages/intel-crashlog"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
install -m 0644 "$cache_dir/$tarball" "$rpmbuild_dir/SOURCES/"
[ ! -f "$cache_dir/$vendor" ] || install -m 0644 "$cache_dir/$vendor" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/packaging/rpm/intel-crashlog.spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    dnf5 -q -y install --setopt=install_weak_deps=False \
      cargo cargo-rpm-macros cargo-vendor-filterer gcc rpm-build rust
    if [ ! -f SOURCES/$vendor ]; then
      # The only networked step: resolve the locked crates for Linux targets.
      tar -xzf SOURCES/$tarball -C BUILD
      (cd BUILD/crashlog-$version &&
        cargo vendor-filterer --platform x86_64-unknown-linux-gnu \
          --platform aarch64-unknown-linux-gnu --manifest-path app/Cargo.toml \
          --sync lib/Cargo.toml --versioned-dirs vendor >/dev/null &&
        tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf - vendor |
          xz -9 >../../SOURCES/$vendor)
      rm -rf BUILD/crashlog-$version
    fi
    printf '%%_smp_build_ncpus 4\n' >\"\$HOME/.rpmmacros\"
    rpmbuild -ba --define \"_topdir \$PWD\" SPECS/intel-crashlog.spec
  "

[ -f "$cache_dir/$vendor" ] || install -m 0644 "$rpmbuild_dir/SOURCES/$vendor" "$cache_dir/"
rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS" "$output_dir/SRPMS"
find "$rpmbuild_dir/RPMS" -name '*.rpm' -exec install -m 0644 {} "$output_dir/RPMS/" \;
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'intel-crashlog packages: %s\n' "$output_dir"
