#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
[ "$(uname -s)" = Linux ] || { printf 'error: Fedora Linux builder required\n' >&2; exit 1; }
architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in x86_64|aarch64) ;; *) printf 'error: unsupported architecture\n' >&2; exit 1 ;; esac
required_tools=(git gzip tar sha256sum realpath)
if [ "${LUMA_RPM_BUILDER_DIRECT:-0}" = 1 ]; then
  required_tools+=(rpm rpmbuild)
else
  required_tools+=(podman)
fi
for tool in "${required_tools[@]}"; do
  command -v "$tool" >/dev/null || { printf 'error: missing %s\n' "$tool" >&2; exit 1; }
done

# Each invocation owns a new output tree and retains failure evidence.
output_dir=${1:-$repo_root/build/packages/luma-esim-broker/$architecture}
output_dir=$(realpath -m "$output_dir")
case "$output_dir" in "$repo_root"/build/*) ;; *) printf 'error: output must be beneath repository build directory\n' >&2; exit 1 ;; esac
[ ! -e "$output_dir" ] && [ ! -L "$output_dir" ] || { printf 'error: output already exists: %s\n' "$output_dir" >&2; exit 1; }
install -d -m 0700 "$output_dir"
output_dir=$(cd "$output_dir" && pwd)
case "$output_dir" in "$repo_root"/build/*) ;; *) printf 'error: output must be beneath repository build directory\n' >&2; exit 1 ;; esac
topdir=$output_dir/rpmbuild
source_dir=$output_dir/luma-esim-broker-0.1.0
install -d -m 0700 "$topdir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$source_dir"
cp -R "$repo_root/src/luma-esim-broker/." "$source_dir/"
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct)}
tar --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 --numeric-owner \
  -C "$output_dir" -cf - luma-esim-broker-0.1.0 | gzip -n >"$topdir/SOURCES/luma-esim-broker-0.1.0.tar.gz"
install -m 0644 "$repo_root/packaging/rpm/luma-esim-broker.spec" "$topdir/SPECS/"

build_command='set -euo pipefail
rpmbuild -ba --define "_topdir $PWD" --define "_smp_build_ncpus 2" SPECS/luma-esim-broker.spec'
if [ "${LUMA_RPM_BUILDER_DIRECT:-0}" = 1 ]; then
  [ "$(uname -m)" = "$architecture" ] || { printf 'error: direct builder must be native\n' >&2; exit 1; }
  # shellcheck disable=SC1091
  . /etc/os-release
  [ "${ID:-}:${VERSION_ID:-}" = fedora:44 ] || { printf 'error: direct builder requires Fedora 44\n' >&2; exit 1; }
  # Dependencies must already be installed in the isolated direct builder.
  (cd "$topdir"; /bin/bash -lc "$build_command")
else
  builder_container=$FEDORA_RPM_BUILD_CONTAINER
  if [ "$architecture" = aarch64 ]; then builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64; fi
  export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture
  "$repo_root/scripts/packages/run-in-rpm-builder.sh" "$topdir" "$builder_container" \
    'set -euo pipefail
dnf5 -y install gcc glib2-devel json-glib-devel meson ninja-build pkgconf-pkg-config dbus-daemon systemd-rpm-macros rpm-build redhat-rpm-config
rpmbuild -ba --define "_topdir $PWD" --define "_smp_build_ncpus 2" SPECS/luma-esim-broker.spec'
fi
install -d -m 0700 "$output_dir/RPMS" "$output_dir/SRPMS"
find "$topdir/RPMS" -type f -name '*.rpm' -exec install -m 0644 {} "$output_dir/RPMS/" \;
find "$topdir/SRPMS" -type f -name '*.src.rpm' -exec install -m 0644 {} "$output_dir/SRPMS/" \;
(cd "$output_dir"; find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
sha256sum "$topdir/SOURCES/luma-esim-broker-0.1.0.tar.gz" "$topdir/SPECS/luma-esim-broker.spec" >"$output_dir/SOURCE-SHA256SUMS"
printf 'Native eSIM broker package outputs: %s\n' "$output_dir"
