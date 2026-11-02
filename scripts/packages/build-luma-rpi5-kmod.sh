#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build luma-rpi5-kmod (Raspberry Pi 5 HDMI audio modules) for the kernel in
# config/kernel/rpi5-kmod.env, which must be the ARM runtime kernel.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
# shellcheck disable=SC1091
. "$repo_root/config/kernel/rpi5-kmod.env"
# shellcheck disable=SC1091
. "$repo_root/config/install/atlas/runtime-kernel-aarch64.env"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
for tool in curl mktemp podman sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || fail "required Pi 5 kmod build tool is missing: $tool"
done
[ "$(uname -s)" = Linux ] || fail "build the Pi 5 kmods on a Fedora Linux builder"
[ "$RPI5_KMOD_KERNEL_NVR" = "$ATLAS_RUNTIME_KERNEL_NVR" ] ||
  fail "kmod kernel $RPI5_KMOD_KERNEL_NVR is not the ARM runtime kernel $ATLAS_RUNTIME_KERNEL_NVR"
[ "${LUMA_TARGET_ARCHITECTURE:-aarch64}" = aarch64 ] || fail "the Pi 5 kmods are aarch64 only"
export LUMA_RPM_BUILDER_ARCHITECTURE=aarch64

nvr=$RPI5_KMOD_KERNEL_NVR
cache="$repo_root/build/cache/rpi5-kmod/$nvr"
mkdir -p "$cache"
fetch() {  # url file sha256
  if [ ! -f "$cache/$2" ] || [ "$(sha256sum "$cache/$2" | cut -d' ' -f1)" != "$3" ]; then
    curl -fsSL --retry 3 -o "$cache/$2.part" "$1/$2"
    mv "$cache/$2.part" "$cache/$2"
  fi
  [ "$(sha256sum "$cache/$2" | cut -d' ' -f1)" = "$3" ] || fail "$2: sha256 mismatch"
}
fetch "$RPI5_KMOD_KOJI_URL/src" "kernel-$nvr.src.rpm" "$RPI5_KMOD_SRPM_SHA256"
fetch "$RPI5_KMOD_KOJI_URL/$RPI5_KMOD_KERNEL_ARCH" "kernel-devel-$nvr.$RPI5_KMOD_KERNEL_ARCH.rpm" \
  "$RPI5_KMOD_DEVEL_SHA256"

output_dir="$repo_root/build/packages/luma-rpi5-kmod"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$work_dir/repo"
# The builder sees only the work directory: copy in what the prepare step reads.
mkdir -p "$work_dir/repo/scripts/devices" "$work_dir/repo/src" "$work_dir/repo/patches"
cp "$repo_root/scripts/devices/prepare-rpi5-kmod-source.sh" "$work_dir/repo/scripts/devices/"
cp -R "$repo_root/src/luma-rpi5-kmod" "$work_dir/repo/src/"
cp -R "$repo_root/patches/linux-rpi5" "$work_dir/repo/patches/"
cp "$cache/kernel-$nvr.src.rpm" "$cache/kernel-devel-$nvr.$RPI5_KMOD_KERNEL_ARCH.rpm" "$work_dir/"
install -m 0644 "$repo_root/packaging/rpm/luma-rpi5-kmod.spec" "$rpmbuild_dir/SPECS/"
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$work_dir" "$FEDORA_RPM_BUILD_CONTAINER_AARCH64" "
    set -euo pipefail
    dnf5 -q -y install --allowerasing cpio elfutils-libelf-devel gcc kmod make patch \
      rpm-build xz ./kernel-devel-$nvr.$RPI5_KMOD_KERNEL_ARCH.rpm
    repo/scripts/devices/prepare-rpi5-kmod-source.sh kernel-$nvr.src.rpm \
      $RPI5_KMOD_SERIES src-tree/luma-rpi5-kmod
    tar -C src-tree --sort=name --mtime=@$source_date_epoch --owner=0 --group=0 \
      --numeric-owner -cf - luma-rpi5-kmod | gzip -n >rpmbuild/SOURCES/luma-rpi5-kmod.tar.gz
    cd rpmbuild
    rpmbuild -ba --define \"_topdir \$PWD\" --define \"kernel_nvr $nvr\" SPECS/luma-rpi5-kmod.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/aarch64" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/aarch64/*.rpm "$output_dir/RPMS/aarch64/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)
printf 'Pi 5 kmod packages: %s\n' "$output_dir"
