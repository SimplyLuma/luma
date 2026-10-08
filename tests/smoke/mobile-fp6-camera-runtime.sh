#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
builder=$repo_root/scripts/mobile/build-fp6-libcamera.sh
inputs=$repo_root/config/mobile/fp6-camera.env

bash -n "$builder"
# shellcheck disable=SC1090
. "$inputs"
[ "$FP6_LIBCAMERA_COMMIT" = 191e202178f02430b5942397c70d215cdd2056fa ]
[ "$FP6_LIBCAMERA_FP6_PATCHSET_COMMIT" = \
  738148b51af05a0a5fe1608ff289180cf46470e2 ]
[ "$(find "$repo_root/patches/libcamera" -maxdepth 1 -type f -name '*.patch' | wc -l)" -eq 16 ]
for number in $(seq 16 31); do
  printf -v prefix '%04d' "$number"
  matches=("$repo_root"/patches/libcamera/"$prefix"-*.patch)
  [ "${#matches[@]}" -eq 1 ] && [ -s "${matches[0]}" ]
done
grep -Fq 'for number in $(seq 1 15)' "$builder"
grep -Fq 'for patch_file in "$repo_root"/patches/libcamera/*.patch' "$builder"
grep -Fq 'for module in jinja2 yaml ply' "$builder"
grep -Fq -- '-Dpipelines=simple' "$builder"
grep -Fq -- '-Dipas=simple' "$builder"
grep -Fq -- '-Dsoftisp-gpu=disabled' "$builder"
grep -Fq 'config/mobile/fp6-camera/*.yaml' "$builder"
grep -Fq 'std::array<uint64_t, 9> sharpnessGrid' \
  "$repo_root/patches/libcamera/0027-simple-regional-touch-metering.patch"
grep -Fq 'focus-point-x' \
  "$repo_root/patches/libcamera/0027-simple-regional-touch-metering.patch"
grep -Fq 'maxExposureStep' \
  "$repo_root/patches/libcamera/0028-simple-ipa-tunable-exposure-step.patch"
grep -Fq 'refinementRadius' \
  "$repo_root/patches/libcamera/0029-simple-ipa-narrow-local-focus-refinement.patch"
grep -Fq 'Ignoring incomplete controls during stop' \
  "$repo_root/patches/libcamera/0030-simple-ipa-ignore-incomplete-teardown-stats.patch"
grep -Fq 'Ignoring empty controls during stop' \
  "$repo_root/patches/libcamera/0031-simple-ipa-ignore-empty-teardown-controls.patch"

if grep -Eiq '(^|[[:space:]])(adb|fastboot|scp|ssh|rm)([[:space:]]|$)' "$builder"; then
  printf 'FAIL: libcamera builder must not access a device or delete paths\n' >&2
  exit 1
fi

printf 'PASS: FP6 libcamera runtime remains pinned, isolated, and source-managed\n'
