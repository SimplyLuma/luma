#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Package kernel-luma-rpi from a finished cross build (configure-rpi.sh, then
# `make all` in BUILD) and write the source RPM with the full build path.
# Run in a Fedora toolroot with the aarch64 cross toolchain, Rust and rpm-build.
# usage: package-rpi.sh SOURCE BUILD FEDORA_CONFIG CACHE OUTPUT
set -euo pipefail
repo=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
source_dir=$(realpath "${1:?usage: package-rpi.sh SOURCE BUILD FEDORA_CONFIG CACHE OUTPUT}")
build_dir=$(realpath "${2:?}")
base_config=$(realpath "${3:?}")
cache=$(realpath "${4:?}")
output=$(realpath -m "${5:?}")
lock="$repo/config/kernel/rpi-7.2.json"
field() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): d=d[k]
print(d)' "$lock" "$1"; }
tarball=$(field sources.linux.name)
[ "$(sha256sum "$cache/$tarball" | cut -d' ' -f1)" = "$(field sources.linux.sha256)" ] ||
  { echo "$tarball: sha256 mismatch" >&2; exit 1; }
[ "$(sha256sum "$base_config" | cut -d' ' -f1)" = "$(field base_config.sha256)" ] ||
  { echo 'Fedora configuration differs from the source lock' >&2; exit 1; }
[ -s "$build_dir/arch/arm64/boot/vmlinuz.efi" ] || { echo 'kernel build is not complete' >&2; exit 1; }
[ ! -e "$output" ] || { echo "refusing to reuse $output" >&2; exit 1; }
[ -z "$(git -C "$repo" status --porcelain --untracked-files=no)" ] ||
  { echo 'commit the source before packaging' >&2; exit 1; }
mkdir -p "$output"/{SOURCES,SPECS,BUILD,BUILDROOT,RPMS,SRPMS,tmp}
git -C "$repo" archive --format=tar.gz HEAD config/kernel/rpi-7.2.json config/kernel/rpi.config \
  config/kernel/rpi-config-exceptions.txt scripts/kernel/configure-rpi.sh \
  scripts/kernel/merge-rpi-config.py >"$output/SOURCES/luma-rpi-kernel-sources.tar.gz"
cp "$cache/$tarball" "$output/SOURCES/"
cp "$base_config" "$output/SOURCES/fedora-base.config"
cp "$repo/packaging/rpm/kernel-luma-rpi.spec" "$output/SPECS/"
export SOURCE_DATE_EPOCH=$(git -C "$repo" log -1 --format=%ct)
cross=
[ "$(uname -m)" = aarch64 ] || cross=aarch64-linux-gnu-
rpmbuild -bs --target aarch64 --define "_topdir $output" --define 'dist .fc44' \
  "$output/SPECS/kernel-luma-rpi.spec" >"$output/source-package.log" 2>&1
rpmbuild -bb --target aarch64 --nodeps --define "_topdir $output" --define "_tmppath $output/tmp" \
  --define 'dist .fc44' --define '_smp_mflags -j16' --define "_luma_cross_compile $cross" \
  --define "_luma_prepared_source $source_dir" --define "_luma_prepared_output $build_dir" \
  "$output/SPECS/kernel-luma-rpi.spec" >"$output/binary-package.log" 2>&1
(cd "$output" && find RPMS SRPMS -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
cat "$output/SHA256SUMS"
