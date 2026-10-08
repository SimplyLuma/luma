#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Maintain a clean, committed Android source state for incremental Prairie
# windowing builds. Waydroid's official patch driver and the Prairie deltas are
# applied once, recorded by exact hashes, and left in place between builds.
# The canonical builder calls --reset before a clean release build.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck source=/dev/null
source "$repo_root/config/android/waydroid-source.env"

source_root=${LUMA_WAYDROID_SOURCE_ROOT:-$repo_root/build/android/lineage-20}
state_file="$source_root/.repo/luma-prairie-windowing-state"
waydroid_patch_script="$source_root/vendor/extra/waydroid-patches/apply-patches.sh"
hardware_root="$source_root/hardware/waydroid"
lineage_root="$source_root/lineage-sdk"
framework_root="$source_root/frameworks/base"
native_root="$source_root/frameworks/native"
device_root="$source_root/device/waydroid/waydroid"
mesa_build_patch="$repo_root/patches/android-mesa/0001-bound-nested-build-jobs.patch"
soong_gc_patch="$repo_root/patches/android-soong/0001-pass-build-host-go-gc-budget.patch"
hardware_patch="$repo_root/patches/android-hardware-waydroid/0001-prairie-native-android-window-chrome.patch"
hardware_pointer_patch="$repo_root/patches/android-hardware-waydroid/0002-prairie-host-pointer-policy.patch"
hardware_frame_patch="$repo_root/patches/android-hardware-waydroid/0003-luma-appkit-content-island.patch"
hardware_treatment_patch="$repo_root/patches/android-hardware-waydroid/0004-luma-surface-treatment-frame.patch"
hardware_lumaui_frame_patch="$repo_root/patches/android-hardware-waydroid/0007-lumaui-current-inset-frame.patch"
hardware_window_behavior_patch="$repo_root/patches/android-hardware-waydroid/0008-luma-desktop-window-behavior-recovery.patch"
hardware_chrome_ownership_patch="$repo_root/patches/android-hardware-waydroid/0009-luma-frame-buffer-thread-ownership.patch"
hardware_task_lifetime_patch="$repo_root/patches/android-hardware-waydroid/0010-luma-authoritative-task-removal.patch"
hardware_initial_resize_patch="$repo_root/patches/android-hardware-waydroid/0011-luma-mandatory-initial-task-resize.patch"
hardware_rpc_threadpool_patch="$repo_root/patches/android-hardware-waydroid/0012-luma-shared-composer-rpc-threadpool.patch"
hardware_buffer_bounds_patch="$repo_root/patches/android-hardware-waydroid/0013-luma-software-buffer-allocation-bounds.patch"
lineage_task_lifetime_patch="$repo_root/patches/android-lineage-sdk/0001-luma-authoritative-task-removal.patch"
framework_patch="$repo_root/patches/android-frameworks-base/0001-prairie-host-owned-window-chrome.patch"
device_patch="$repo_root/patches/android-device-waydroid/0001-prairie-host-window-product-property.patch"
native_patch="$repo_root/patches/android-frameworks-native/0001-luma-task-layer-ownership.patch"

test -d "$source_root/.repo" || {
  printf 'error: synchronize the pinned Android source first\n' >&2
  exit 1
}
test -x "$waydroid_patch_script"

tree_snapshot() {
  (
    cd "$source_root"
    repo forall -c 'printf "%s %s\n" "$REPO_PATH" "$(git rev-parse HEAD)"'
  ) | sort | sha256sum | awk '{print $1}'
}

dirty_projects() {
  (
    cd "$source_root"
    repo forall -c \
      'test -z "$(git status --porcelain)" || printf "%s\n" "$REPO_PATH"'
  )
}

official_input_sha() {
  {
    printf '%s\n' \
      "$LUMA_WAYDROID_LINEAGE_BRANCH" \
      "$LUMA_WAYDROID_VENDOR_REVISION" \
      "$LUMA_WAYDROID_HARDWARE_REVISION" \
      "$LUMA_ANDROID_FRAMEWORKS_BASE_REVISION" \
      "$LUMA_WAYDROID_DEVICE_REVISION"
    git -C "$source_root/vendor/extra" ls-tree -r HEAD waydroid-patches
  } | sha256sum | awk '{print $1}'
}

patch_sha() {
  sha256sum "$1" | awk '{print $1}'
}

write_state() {
  local temporary
  temporary=$(mktemp "$state_file.XXXXXX")
  {
    printf 'STATE_VERSION=1\n'
    printf 'OFFICIAL_INPUT_SHA=%s\n' "$(official_input_sha)"
    printf 'TREE_SNAPSHOT=%s\n' "$(tree_snapshot)"
    printf 'HARDWARE_OFFICIAL_HEAD=%s\n' "$hardware_official_head"
    printf 'LINEAGE_OFFICIAL_HEAD=%s\n' "$lineage_official_head"
    printf 'LINEAGE_TASK_LIFETIME_PATCH_SHA=%s\n' "$(patch_sha "$lineage_task_lifetime_patch")"
    printf 'HARDWARE_TASK_LIFETIME_PATCH_SHA=%s\n' "$(patch_sha "$hardware_task_lifetime_patch")"
    printf 'HARDWARE_INITIAL_RESIZE_PATCH_SHA=%s\n' "$(patch_sha "$hardware_initial_resize_patch")"
    printf 'HARDWARE_RPC_THREADPOOL_PATCH_SHA=%s\n' "$(patch_sha "$hardware_rpc_threadpool_patch")"
    printf 'HARDWARE_BUFFER_BOUNDS_PATCH_SHA=%s\n' "$(patch_sha "$hardware_buffer_bounds_patch")"
    printf 'FRAMEWORK_OFFICIAL_HEAD=%s\n' "$framework_official_head"
    printf 'NATIVE_OFFICIAL_HEAD=%s\n' "$native_official_head"
    printf 'NATIVE_PATCH_SHA=%s\n' "$(patch_sha "$native_patch")"
    printf 'DEVICE_OFFICIAL_HEAD=%s\n' "$device_official_head"
    printf 'HARDWARE_PATCH_SHA=%s\n' "$(patch_sha "$hardware_patch")"
    printf 'HARDWARE_POINTER_PATCH_SHA=%s\n' "$(patch_sha "$hardware_pointer_patch")"
    printf 'HARDWARE_FRAME_PATCH_SHA=%s\n' "$(patch_sha "$hardware_frame_patch")"
    printf 'HARDWARE_TREATMENT_PATCH_SHA=%s\n' "$(patch_sha "$hardware_treatment_patch")"
    printf 'HARDWARE_CHROME_OWNERSHIP_PATCH_SHA=%s\n' "$(patch_sha "$hardware_chrome_ownership_patch")"
    printf 'HARDWARE_WINDOW_BEHAVIOR_PATCH_SHA=%s\n' "$(patch_sha "$hardware_window_behavior_patch")"
    printf 'HARDWARE_LUMAUI_FRAME_PATCH_SHA=%s\n' "$(patch_sha "$hardware_lumaui_frame_patch")"
    printf 'ALLOCATOR_PATCH_SHA=%s\n' "$(patch_sha "$repo_root/patches/android-hardware-libhardware/0001-luma-software-yuv-buffers.patch")"
    printf 'MESA_BUILD_PATCH_SHA=%s\n' "$(patch_sha "$mesa_build_patch")"
    printf 'SOONG_GC_PATCH_SHA=%s\n' "$(patch_sha "$soong_gc_patch")"
    printf 'FRAMEWORK_PATCH_SHA=%s\n' "$(patch_sha "$framework_patch")"
    printf 'DEVICE_PATCH_SHA=%s\n' "$(patch_sha "$device_patch")"
  } >"$temporary"
  mv "$temporary" "$state_file"
}

commit_patch() {
  local project=$1 message=$2
  git -C "$project" add -A
  GIT_AUTHOR_NAME='Project Luma Build' \
    GIT_AUTHOR_EMAIL='build@projectluma.invalid' \
    GIT_COMMITTER_NAME='Project Luma Build' \
    GIT_COMMITTER_EMAIL='build@projectluma.invalid' \
    git -C "$project" commit -q -m "$message"
}

reset_prepared_tree() {
  if [ ! -f "$state_file" ]; then
    test -z "$(dirty_projects)" || {
      printf 'error: refusing to reset an untracked dirty Android tree\n' >&2
      exit 1
    }
    return
  fi
  # shellcheck source=/dev/null
  source "$state_file"
  [ "${STATE_VERSION:-}" = 1 ]
  [ "$(tree_snapshot)" = "${TREE_SNAPSHOT:-}" ] || {
    printf 'error: prepared Android tree drifted; refusing automatic reset\n' >&2
    exit 1
  }
  test -z "$(dirty_projects)" || {
    printf 'error: prepared Android tree has uncommitted changes\n' >&2
    exit 1
  }
  (
    cd "$source_root"
    repo forall -c 'git reset --hard -q "$REPO_LREV"; git clean -fdq'
  )
  rm -f "$state_file"
  printf 'Prairie Waydroid source returned to the pinned clean manifest\n'
}

if [ "${1:-}" = --reset ]; then
  [ "$#" -eq 1 ] || exit 2
  reset_prepared_tree
  exit 0
fi
[ "$#" -eq 0 ] || {
  printf 'usage: %s [--reset]\n' "$0" >&2
  exit 2
}

if [ ! -f "$state_file" ]; then
  test -z "$(dirty_projects)" || {
    printf 'error: Android source tree has uncommitted changes\n' >&2
    exit 1
  }
  test "$(git -C "$hardware_root" rev-parse HEAD)" = \
    "$LUMA_WAYDROID_HARDWARE_REVISION"
  test "$(git -C "$framework_root" rev-parse HEAD)" = \
    "$LUMA_ANDROID_FRAMEWORKS_BASE_REVISION"
  test "$(git -C "$device_root" rev-parse HEAD)" = \
    "$LUMA_WAYDROID_DEVICE_REVISION"

  (
    cd "$source_root"
    GIT_COMMITTER_NAME='Project Luma Build' \
      GIT_COMMITTER_EMAIL='build@projectluma.invalid' \
      TERM=${TERM:-xterm} \
      bash "$waydroid_patch_script"
  )
  hardware_official_head=$(git -C "$hardware_root" rev-parse HEAD)
  lineage_official_head=$(git -C "$lineage_root" rev-parse HEAD)
  framework_official_head=$(git -C "$framework_root" rev-parse HEAD)
  native_official_head=$(git -C "$native_root" rev-parse HEAD)
  device_official_head=$(git -C "$device_root" rev-parse HEAD)

  git -C "$hardware_root" apply --binary "$hardware_patch"
  git -C "$hardware_root" apply "$hardware_pointer_patch"
  git -C "$hardware_root" apply "$hardware_frame_patch"
  git -C "$hardware_root" apply "$hardware_treatment_patch"
  git -C "$hardware_root" apply "$hardware_lumaui_frame_patch"
  git -C "$hardware_root" apply "$hardware_window_behavior_patch"
  git -C "$hardware_root" apply "$hardware_chrome_ownership_patch"
  git -C "$hardware_root" apply "$hardware_task_lifetime_patch"
  git -C "$hardware_root" apply "$hardware_initial_resize_patch"
  git -C "$hardware_root" apply "$hardware_rpc_threadpool_patch"
  git -C "$hardware_root" apply "$hardware_buffer_bounds_patch"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-rgb-copy-bounds.h" "$hardware_root/hwcomposer/luma-rgb-copy-bounds.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-task-lifetime.h" "$hardware_root/hwcomposer/luma-task-lifetime.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-window-callbacks.h" "$hardware_root/hwcomposer/luma-window-callbacks.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-task-resize.h" "$hardware_root/hwcomposer/luma-task-resize.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/Figtree-OFL.txt" "$hardware_root/hwcomposer/fonts/OFL.txt"
  for header in luma-window-frame.h luma-frame-tokens.h luma-frame-glyphs.h; do
    cmp "$repo_root/src/luma-platform/compat/$header" "$hardware_root/hwcomposer/$header"
  done
  commit_patch "$hardware_root" 'Prairie native Android window chrome'
  git -C "$lineage_root" apply "$lineage_task_lifetime_patch"
  commit_patch "$lineage_root" 'Luma authoritative Android task removal'
  git -C "$framework_root" apply "$framework_patch"
  commit_patch "$framework_root" 'Prairie host-owned Android window chrome'
  git -C "$native_root" apply "$native_patch"
  commit_patch "$native_root" 'Luma inherited Android task layer ownership'
  git -C "$device_root" apply "$device_patch"
  commit_patch "$device_root" 'Prairie Android product window policy'
  python3 "$repo_root/scripts/android/integrate-software-gralloc.py" "$source_root"
  commit_patch "$source_root/hardware/libhardware" 'Luma software-YUV allocator'
  git -C "$source_root/external/mesa" apply "$mesa_build_patch"
  commit_patch "$source_root/external/mesa" 'Bound nested Mesa jobs to the admitted build budget'
  git -C "$source_root/build/soong" apply "$soong_gc_patch"
  commit_patch "$source_root/build/soong" 'Preserve explicit host Go GC tuning in the isolated compiler'
  write_state
  printf 'Prairie Waydroid incremental source prepared\n'
  exit 0
fi

# shellcheck source=/dev/null
source "$state_file"
[ "${STATE_VERSION:-}" = 1 ]
[ "$(official_input_sha)" = "${OFFICIAL_INPUT_SHA:-}" ] || {
  printf 'error: pinned Waydroid inputs changed; run a clean release build first\n' >&2
  exit 1
}
[ "$(tree_snapshot)" = "${TREE_SNAPSHOT:-}" ] || {
  printf 'error: prepared Android tree commit state drifted\n' >&2
  exit 1
}
test -z "$(dirty_projects)" || {
  printf 'error: prepared Android tree has uncommitted changes\n' >&2
  exit 1
}

hardware_official_head=$HARDWARE_OFFICIAL_HEAD
lineage_official_head=${LINEAGE_OFFICIAL_HEAD:-$(git -C "$lineage_root" rev-parse HEAD)}
framework_official_head=$FRAMEWORK_OFFICIAL_HEAD
native_official_head=${NATIVE_OFFICIAL_HEAD:-$(git -C "$native_root" rev-parse HEAD)}
device_official_head=$DEVICE_OFFICIAL_HEAD

changed=0
if [ -n "${SOONG_GC_PATCH_SHA:-}" ] && [ "$(patch_sha "$soong_gc_patch")" != "$SOONG_GC_PATCH_SHA" ]; then
  printf 'error: Soong compiler policy changed; run a clean release build first\n' >&2
  exit 1
fi
if [ -z "${SOONG_GC_PATCH_SHA:-}" ]; then
  git -C "$source_root/build/soong" apply "$soong_gc_patch"
  commit_patch "$source_root/build/soong" 'Preserve explicit host Go GC tuning in the isolated compiler'
  changed=1
fi
if [ "$(patch_sha "$native_patch")" != "${NATIVE_PATCH_SHA:-}" ]; then
  if [ -n "${NATIVE_PATCH_SHA:-}" ]; then
    git -C "$native_root" reset --hard -q "$native_official_head"
    git -C "$native_root" clean -fdq
  fi
  git -C "$native_root" apply "$native_patch"
  commit_patch "$native_root" 'Luma inherited Android task layer ownership'
  changed=1
fi
if [ -n "${MESA_BUILD_PATCH_SHA:-}" ] && [ "$(patch_sha "$mesa_build_patch")" != "$MESA_BUILD_PATCH_SHA" ]; then
  printf 'error: Mesa build policy changed; run a clean release build first\n' >&2
  exit 1
fi
if [ -z "${MESA_BUILD_PATCH_SHA:-}" ]; then
  git -C "$source_root/external/mesa" apply "$mesa_build_patch"
  commit_patch "$source_root/external/mesa" 'Bound nested Mesa jobs to the admitted build budget'
  changed=1
fi
if [ -n "${ALLOCATOR_PATCH_SHA:-}" ] &&
    [ "$(patch_sha "$repo_root/patches/android-hardware-libhardware/0001-luma-software-yuv-buffers.patch")" != "$ALLOCATOR_PATCH_SHA" ]; then
  printf 'error: allocator source changed; run a clean release build first\n' >&2
  exit 1
fi
if [ -z "${ALLOCATOR_PATCH_SHA:-}" ]; then
  python3 "$repo_root/scripts/android/integrate-software-gralloc.py" "$source_root"
  commit_patch "$source_root/hardware/libhardware" 'Luma software-YUV allocator'
  changed=1
fi
if [ "$(patch_sha "$hardware_patch")" != "${HARDWARE_PATCH_SHA:-}" ] ||
    [ "$(patch_sha "$hardware_pointer_patch")" != "${HARDWARE_POINTER_PATCH_SHA:-}" ] ||
    [ "$(patch_sha "$hardware_frame_patch")" != "${HARDWARE_FRAME_PATCH_SHA:-}" ] ||
    [ "$(patch_sha "$hardware_treatment_patch")" != "${HARDWARE_TREATMENT_PATCH_SHA:-}" ] ||
    [ "$(patch_sha "$hardware_lumaui_frame_patch")" != "${HARDWARE_LUMAUI_FRAME_PATCH_SHA:-}" ] ||
    [ "$(patch_sha "$hardware_window_behavior_patch")" != "${HARDWARE_WINDOW_BEHAVIOR_PATCH_SHA:-}" ] ||
    [ "$(patch_sha "$hardware_chrome_ownership_patch")" != "${HARDWARE_CHROME_OWNERSHIP_PATCH_SHA:-}" ] ||
    [ "$(patch_sha "$hardware_task_lifetime_patch")" != "${HARDWARE_TASK_LIFETIME_PATCH_SHA:-}" ] ||
    [ "$(patch_sha "$hardware_initial_resize_patch")" != "${HARDWARE_INITIAL_RESIZE_PATCH_SHA:-}" ] ||
    [ "$(patch_sha "$hardware_rpc_threadpool_patch")" != "${HARDWARE_RPC_THREADPOOL_PATCH_SHA:-}" ] ||
    [ "$(patch_sha "$hardware_buffer_bounds_patch")" != "${HARDWARE_BUFFER_BOUNDS_PATCH_SHA:-}" ]; then
  git -C "$hardware_root" reset --hard -q "$HARDWARE_OFFICIAL_HEAD"
  git -C "$hardware_root" clean -fdq
  git -C "$hardware_root" apply --binary "$hardware_patch"
  git -C "$hardware_root" apply "$hardware_pointer_patch"
  git -C "$hardware_root" apply "$hardware_frame_patch"
  git -C "$hardware_root" apply "$hardware_treatment_patch"
  git -C "$hardware_root" apply "$hardware_lumaui_frame_patch"
  git -C "$hardware_root" apply "$hardware_window_behavior_patch"
  git -C "$hardware_root" apply "$hardware_chrome_ownership_patch"
  git -C "$hardware_root" apply "$hardware_task_lifetime_patch"
  git -C "$hardware_root" apply "$hardware_initial_resize_patch"
  git -C "$hardware_root" apply "$hardware_rpc_threadpool_patch"
  git -C "$hardware_root" apply "$hardware_buffer_bounds_patch"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-rgb-copy-bounds.h" "$hardware_root/hwcomposer/luma-rgb-copy-bounds.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-task-lifetime.h" "$hardware_root/hwcomposer/luma-task-lifetime.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-window-callbacks.h" "$hardware_root/hwcomposer/luma-window-callbacks.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-task-resize.h" "$hardware_root/hwcomposer/luma-task-resize.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/Figtree-OFL.txt" "$hardware_root/hwcomposer/fonts/OFL.txt"
  for header in luma-window-frame.h luma-frame-tokens.h luma-frame-glyphs.h; do
    cmp "$repo_root/src/luma-platform/compat/$header" "$hardware_root/hwcomposer/$header"
  done
  commit_patch "$hardware_root" 'Prairie native Android window chrome'
  changed=1
fi
if [ "$(patch_sha "$lineage_task_lifetime_patch")" != "${LINEAGE_TASK_LIFETIME_PATCH_SHA:-}" ]; then
  git -C "$lineage_root" reset --hard -q "$lineage_official_head"
  git -C "$lineage_root" clean -fdq
  git -C "$lineage_root" apply "$lineage_task_lifetime_patch"
  commit_patch "$lineage_root" 'Luma authoritative Android task removal'
  changed=1
fi
if [ "$(patch_sha "$framework_patch")" != "${FRAMEWORK_PATCH_SHA:-}" ]; then
  git -C "$framework_root" reset --hard -q "$FRAMEWORK_OFFICIAL_HEAD"
  git -C "$framework_root" clean -fdq
  git -C "$framework_root" apply "$framework_patch"
  commit_patch "$framework_root" 'Prairie host-owned Android window chrome'
  changed=1
fi
if [ "$(patch_sha "$device_patch")" != "${DEVICE_PATCH_SHA:-}" ]; then
  git -C "$device_root" reset --hard -q "$DEVICE_OFFICIAL_HEAD"
  git -C "$device_root" clean -fdq
  git -C "$device_root" apply "$device_patch"
  commit_patch "$device_root" 'Prairie Android product window policy'
  changed=1
fi

cmp "$repo_root/patches/android-hardware-waydroid/native/luma-task-resize.h" "$hardware_root/hwcomposer/luma-task-resize.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/Figtree-OFL.txt" "$hardware_root/hwcomposer/fonts/OFL.txt"
for header in luma-window-frame.h luma-frame-tokens.h luma-frame-glyphs.h; do
  cmp "$repo_root/src/luma-platform/compat/$header" "$hardware_root/hwcomposer/$header"
done
write_state
if [ "$changed" -eq 1 ]; then
  printf 'Prairie Waydroid incremental patches refreshed\n'
else
  printf 'Prairie Waydroid incremental source already current\n'
fi
