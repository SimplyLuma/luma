#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build the isolated FP6 Simple-pipeline libcamera runtime from exact sources.
# The destination is a caller-selected prefix; this script never replaces the
# distribution libcamera packages or writes to a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-camera.env"

work=${1:?usage: build-fp6-libcamera.sh WORK_DIR PREFIX}
prefix=${2:?usage: build-fp6-libcamera.sh WORK_DIR PREFIX}
jobs=${LUMA_LIBCAMERA_BUILD_JOBS:-2}

case "$jobs" in
  ''|*[!0-9]*) printf 'error: invalid job count: %s\n' "$jobs" >&2; exit 1 ;;
esac
[ "$jobs" -ge 1 ] || { printf 'error: job count must be positive\n' >&2; exit 1; }
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || {
  printf 'error: build the FP6 libcamera runtime on Linux/aarch64\n' >&2
  exit 1
}
[ ! -e "$work" ] || {
  printf 'error: work directory already exists: %s\n' "$work" >&2
  exit 1
}
[ ! -e "$prefix" ] || {
  printf 'error: install prefix already exists: %s\n' "$prefix" >&2
  exit 1
}

for tool in git meson ninja patch python3; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: missing build tool: %s\n' "$tool" >&2
    exit 1
  }
done
for module in jinja2 yaml ply; do
  python3 -c "import $module" >/dev/null 2>&1 || {
    printf 'error: missing Python build module: %s\n' "$module" >&2
    exit 1
  }
done

mkdir -p "$work"
work=$(CDPATH= cd -- "$work" && pwd)
case "$prefix" in
  /*) ;;
  *) prefix=$PWD/$prefix ;;
esac

source_tree=$work/libcamera
patchset_tree=$work/fp6-img
git clone --filter=blob:none --no-checkout "$FP6_LIBCAMERA_URL" "$source_tree"
git -C "$source_tree" -c advice.detachedHead=false checkout --detach \
  "$FP6_LIBCAMERA_COMMIT"
[ "$(git -C "$source_tree" rev-parse HEAD)" = "$FP6_LIBCAMERA_COMMIT" ]

git clone --filter=blob:none --no-checkout \
  "$FP6_LIBCAMERA_FP6_PATCHSET_URL" "$patchset_tree"
git -C "$patchset_tree" -c advice.detachedHead=false checkout --detach \
  "$FP6_LIBCAMERA_FP6_PATCHSET_COMMIT"
[ "$(git -C "$patchset_tree" rev-parse HEAD)" = \
  "$FP6_LIBCAMERA_FP6_PATCHSET_COMMIT" ]

for number in $(seq 1 15); do
  printf -v patch_prefix '%04d' "$number"
  matches=("$patchset_tree"/aports/temp/libcamera/"$patch_prefix"-*.patch)
  [ "${#matches[@]}" -eq 1 ] && [ -f "${matches[0]}" ] || {
    printf 'error: expected exactly one prerequisite patch %s\n' "$number" >&2
    exit 1
  }
  git -C "$source_tree" apply --check "${matches[0]}"
  git -C "$source_tree" apply "${matches[0]}"
done

for patch_file in "$repo_root"/patches/libcamera/*.patch; do
  git -C "$source_tree" apply --check "$patch_file"
  git -C "$source_tree" apply "$patch_file"
done
git -C "$source_tree" diff --check

meson setup "$source_tree/output" "$source_tree" \
  --buildtype=release \
  --prefix="$prefix" \
  -Dpipelines=simple \
  -Dipas=simple \
  -Dcam=disabled \
  -Ddocumentation=disabled \
  -Dgstreamer=enabled \
  -Dlc-compliance=disabled \
  -Dlibdw=disabled \
  -Dlibunwind=disabled \
  -Dpycamera=disabled \
  -Dqcam=disabled \
  -Dsoftisp-gpu=disabled \
  -Dtest=false \
  -Dtracing=disabled \
  -Dv4l2=disabled \
  -Dcpp_args=-Wno-error=array-bounds
ninja -C "$source_tree/output" -j "$jobs"
meson install -C "$source_tree/output"

tuning_dir=$prefix/share/libcamera/ipa/simple
mkdir -p "$tuning_dir"
install -m 0644 "$repo_root"/config/mobile/fp6-camera/*.yaml "$tuning_dir/"

printf 'FP6 libcamera runtime installed at %s\n' "$prefix"
printf 'source commit: %s\n' "$FP6_LIBCAMERA_COMMIT"
printf 'FP6 prerequisite patchset: %s\n' "$FP6_LIBCAMERA_FP6_PATCHSET_COMMIT"
