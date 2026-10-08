#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
patch=$repo_root/patches/linux-milos/0001-drm-msm-a810-disable-ifpc-diagnostic.patch
gmu_diag_patch=$repo_root/patches/linux-milos/0002-drm-msm-a810-gmu-timeout-diagnostics.patch
gmu_fence_patch=$repo_root/patches/linux-milos/0003-drm-msm-a810-use-gen8-3-ahb-fence-range.patch
gmu_bootfreq_patch=$repo_root/patches/linux-milos/0004-drm-msm-a810-use-gen8-3-gmu-boot-rate.patch
builder=$repo_root/scripts/mobile/build-fp6-ifpc-diagnostic.sh
pmos_builder=$repo_root/scripts/mobile/build-fp6-postmarketos-kernel-control.sh
repacker=$repo_root/scripts/mobile/prepare-fp6-ifpc-boot-candidate.sh

bash -n "$builder" "$repacker"
sh -n "$pmos_builder"
grep -Fq 'dfe0125e73541d1984e4d2d37bd069a036bf0451' "$builder"
grep -Fq 'CONFIG_LTO_CLANG_THIN=y' "$builder"
grep -Fq 'CONFIG_CFI=y' "$builder"
grep -Fq 'CONFIG_SHADOW_CALL_STACK=y' "$builder"
grep -Fq 'declared_size=$(od -An -t u8 -j 16 -N 8 "$image"' "$builder"
grep -Fq 'truncate -s "$declared_size" "$image"' "$builder"
grep -Fq 'gzip -9 -n -c "$image"' "$builder"
grep -Fq 'e5422fadf556c3f632f7619a23b13d80f0214a2c' "$pmos_builder"
grep -Fq 'LUMA_PMOS_BUILD_HOST_PROFILE:-cross-x86_64' "$pmos_builder"
grep -Fq 'alpine-minirootfs-20260805-x86_64.tar.gz' "$pmos_builder"
grep -Fq '5acac12c425a0817c1b6a79bdd5df4c73756fc8dc268eab32f8ba830872b835d' \
  "$pmos_builder"
grep -Fq 'alpine-minirootfs-20260805-aarch64.tar.gz' "$pmos_builder"
grep -Fq '/proc/sys/fs/binfmt_misc/rosetta' "$pmos_builder"
grep -Fq 'Alpine clang version 22.1.8' "$pmos_builder"
grep -Fq 'pahole --version | grep -Fx "v1.31"' "$pmos_builder"
grep -Fq 'refuse to compile on the FP6 test target' "$pmos_builder"
grep -Fq '44c332fd51c679d096db679f7f0705b4b0bc0219d4bb3e5d6eeffdfa03bf7e73' \
  "$pmos_builder"
grep -Fq 'd2fe1cb5402c45d2185d0fe36eae3e1ffe3ed8b44561f8d2f0575e23a24e4b15' \
  "$pmos_builder"
grep -Fq 'gmu-diag' "$pmos_builder"
grep -Fq '98c44781d92290a503111d2f5436a09eaff719f12d3a1e5c3234a9aec0afcbae' \
  "$pmos_builder"
grep -Fq 'gmu-fence-diag' "$pmos_builder"
grep -Fq '59afc667c728e0792c154724b225fb59fd74a732e3125708649a5bc5a7cecb88' \
  "$pmos_builder"
grep -Fq 'gmu-bootfreq-diag' "$pmos_builder"
grep -Fq 'GMU_FENCE_PATCH_APPLIED=false' "$pmos_builder"
grep -Fq 'KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos' "$pmos_builder"
grep -Fq 'KBUILD_BUILD_USER=pmos' "$pmos_builder"
grep -Fq 'KBUILD_BUILD_HOST=build' "$pmos_builder"
grep -Fq 'patch -R -p1 --dry-run <../ifpc.patch' "$pmos_builder"
grep -Fq 'touch drivers/gpu/drm/msm/adreno/a6xx_catalog.c' "$pmos_builder"
grep -Fq 'touch drivers/gpu/drm/msm/adreno/a6xx_gmu.c' "$pmos_builder"
grep -Fq 'reject stale output' "$pmos_builder"
grep -Fq 'vmlinuz.efi' "$pmos_builder"
grep -Fq 'BUILD_HOST_PROFILE=' "$pmos_builder"
grep -Fq 'REUSED_LAB=' "$pmos_builder"
grep -Fq 'PHONE_PARTITION_WRITTEN=false' "$pmos_builder"
grep -Fq 'BOOTLOADER_CONTACTED=false' "$pmos_builder"
grep -Fq 'ADRENO_QUIRK_PREEMPTION,' "$patch"
grep -Fq -- $'-\t\t\t  ADRENO_QUIRK_IFPC,' "$patch"
grep -Fq 'This patch does not change power sequencing' "$gmu_diag_patch"
grep -Fq 'A810 GMU start timeout: boot=%s' "$gmu_diag_patch"
grep -Fq 'pm_runtime_active(gmu->dev)' "$gmu_diag_patch"
grep -Fq 'This is a one-variable behavior test' "$gmu_fence_patch"
grep -Fq 'adreno_is_a810(adreno_gpu) ? 0x8a0 : 0x8c0' "$gmu_fence_patch"
grep -Fq 'This is a one-variable behavior test' "$gmu_bootfreq_patch"
grep -Fq 'adreno_is_a810(adreno_gpu) ? 650000000 : 200000000' \
  "$gmu_bootfreq_patch"
grep -Fq '5885e62324115ae9cce929318df24251992ef705952dcbe6b16cef549a505efb' \
  "$repacker"
grep -Fq 'cmp "$control_boot" "$roundtrip"' "$repacker"
grep -Fq 'cmp "$control_dir/ramdisk" "$candidate_dir/ramdisk"' "$repacker"
grep -Fq 'cmp "$control_dir/dtb" "$candidate_dir/dtb"' "$repacker"

if grep -Eiq '(^|[[:space:]])(adb|fastboot|scp|ssh)([[:space:]]|$)' \
  "$builder" "$pmos_builder" "$repacker"; then
  printf 'FAIL: artifact builders must not contain device-access commands\n' >&2
  exit 1
fi

printf 'PASS: FP6 kernel diagnostic remains pinned and artifact-only\n'
