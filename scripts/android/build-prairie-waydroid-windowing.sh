#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck source=/dev/null
source "$repo_root/config/android/waydroid-source.env"

source_root=${LUMA_WAYDROID_SOURCE_ROOT:-$repo_root/build/android/lineage-20}
patch_file="$repo_root/patches/android-hardware-waydroid/0001-prairie-native-android-window-chrome.patch"
pointer_patch="$repo_root/patches/android-hardware-waydroid/0002-prairie-host-pointer-policy.patch"
frame_patch="$repo_root/patches/android-hardware-waydroid/0003-luma-appkit-content-island.patch"
treatment_patch="$repo_root/patches/android-hardware-waydroid/0004-luma-surface-treatment-frame.patch"
lumaui_frame_patch="$repo_root/patches/android-hardware-waydroid/0007-lumaui-current-inset-frame.patch"
window_behavior_patch="$repo_root/patches/android-hardware-waydroid/0008-luma-desktop-window-behavior-recovery.patch"
framework_patch="$repo_root/patches/android-frameworks-base/0001-prairie-host-owned-window-chrome.patch"
native_patch="$repo_root/patches/android-frameworks-native/0001-luma-task-layer-ownership.patch"
device_patch="$repo_root/patches/android-device-waydroid/0001-prairie-host-window-product-property.patch"
codec_patch="$repo_root/patches/android-device-waydroid/0002-luma-codec-services-match-the-image.patch"
input_method_patch="$repo_root/patches/android-device-waydroid/0003-luma-input-method.patch"
jelly_patch="$repo_root/patches/android-jelly/0001-luma-consume-handled-app-redirects.patch"
vendor_extra_patch="$repo_root/patches/android-vendor-extra/0001-luma-idempotent-pc-feature-mask.patch"
libhidl_patch="$repo_root/patches/android-system-libhidl/0001-luma-copy-service-manager-under-lock.patch"
hardware_root="$source_root/hardware/waydroid"
framework_root="$source_root/frameworks/base"
device_root="$source_root/device/waydroid/waydroid"
waydroid_patch_script="$source_root/vendor/extra/waydroid-patches/apply-patches.sh"
mesa_tool_root=${LUMA_ANDROID_MESA_TOOLS:-$source_root/prebuilts/mesa-tools}
expected_font_sha=26ad3db9b31ff7dde67a91ff515d022d2f495cd506590699cf264f0bfe6fb714
build_jobs=${LUMA_BUILD_JOBS:-}
soong_gc=${LUMA_ANDROID_SOONG_GOGC:-20}
soong_gc_patch="$repo_root/patches/android-soong/0001-pass-build-host-go-gc-budget.patch"
soong_cpu_patch="$repo_root/patches/android-soong/0002-pass-build-host-go-parallel-budget.patch"
blueprint_cpu_patch="$repo_root/patches/android-blueprint/0001-honor-host-go-concurrency.patch"
make_host_patch="$repo_root/patches/android-make/0001-luma-native-image-host-tools.patch"
property_admission_patch="$repo_root/patches/android-system-core/0001-luma-property-writer-admission.patch"
chrome_ownership_patch="$repo_root/patches/android-hardware-waydroid/0009-luma-frame-buffer-thread-ownership.patch"
task_lifetime_patch="$repo_root/patches/android-hardware-waydroid/0010-luma-authoritative-task-removal.patch"
initial_resize_patch="$repo_root/patches/android-hardware-waydroid/0011-luma-mandatory-initial-task-resize.patch"
rpc_threadpool_patch="$repo_root/patches/android-hardware-waydroid/0012-luma-shared-composer-rpc-threadpool.patch"
buffer_bounds_patch="$repo_root/patches/android-hardware-waydroid/0013-luma-software-buffer-allocation-bounds.patch"
configure_lock_patch="$repo_root/patches/android-hardware-waydroid/0014-luma-configure-invalidation-lock-order.patch"
native_clipboard_patch="$repo_root/patches/android-hardware-waydroid/0015-luma-native-clipboard-caller-admission.patch"
clipboard_transfer_patch="$repo_root/patches/android-hardware-waydroid/0016-luma-bounded-native-clipboard-transfer.patch"
task_admission_patch="$repo_root/patches/android-hardware-waydroid/0017-luma-native-task-caller-admission.patch"
task_channel_patch="$repo_root/patches/android-hardware-waydroid/0018-luma-bound-task-channel-io.patch"
app_settings_patch="$repo_root/patches/android-hardware-waydroid/0019-luma-native-app-settings-action.patch"
desktop_canvas_patch="$repo_root/patches/android-hardware-waydroid/0020-luma-logical-desktop-canvas.patch"
handheld_fill_patch="$repo_root/patches/android-hardware-waydroid/0021-luma-handheld-task-window-fill.patch"
text_input_patch="$repo_root/patches/android-hardware-waydroid/0022-luma-host-text-input.patch"
video_layer_patch="$repo_root/patches/android-hardware-waydroid/0023-luma-video-layer-conversion.patch"
lineage_task_lifetime_patch="$repo_root/patches/android-lineage-sdk/0001-luma-authoritative-task-removal.patch"
lineage_notification_patch="$repo_root/patches/android-lineage-sdk/0002-luma-notification-lifecycle.patch"
lineage_admission_patch="$repo_root/patches/android-lineage-sdk/0003-luma-platform-caller-admission.patch"
lineage_clipboard_patch="$repo_root/patches/android-lineage-sdk/0004-luma-native-clipboard-reconnect.patch"
lineage_reply_patch="$repo_root/patches/android-lineage-sdk/0005-luma-native-notification-replies.patch"
lineage_icon_patch="$repo_root/patches/android-lineage-sdk/0006-luma-unmasked-adaptive-icons.patch"
target_arch=${LUMA_WAYDROID_TARGET_ARCH:-x86_64}
case "$target_arch" in
  x86_64) target_product=waydroid_x86_64 ;;
  aarch64) target_product=waydroid_arm64_only ;;
  *) printf 'error: LUMA_WAYDROID_TARGET_ARCH must be x86_64 or aarch64\n' >&2; exit 1 ;;
esac
incremental=${LUMA_WAYDROID_INCREMENTAL:-0}
prepare_script="$repo_root/scripts/android/prepare-prairie-waydroid-tree.sh"

case "$incremental" in
  0|1) ;;
  *)
    printf 'error: LUMA_WAYDROID_INCREMENTAL must be 0 or 1\n' >&2
    exit 1
    ;;
esac

if [[ -n $build_jobs && ! $build_jobs =~ ^[1-9][0-9]*$ ]]; then
  printf 'error: LUMA_BUILD_JOBS must be a positive integer\n' >&2
  exit 1
fi
[[ "$soong_gc" =~ ^[1-9][0-9]*$ ]] || {
  printf 'error: LUMA_ANDROID_SOONG_GOGC must be a positive percentage\n' >&2
  exit 1
}
export GOGC="$soong_gc"
# This entry point produces Android runtime images/components, not host SDKs.
export LUMA_ANDROID_IMAGE_HOST_ONLY=1
build_job_args=()
if [[ -n $build_jobs ]]; then
  build_job_args+=("-j$build_jobs")
fi
if [[ -n $build_jobs && -z ${GOMAXPROCS:-} ]]; then
  export GOMAXPROCS="$build_jobs"
fi
if [[ -n ${GOMAXPROCS:-} && ! $GOMAXPROCS =~ ^[1-9][0-9]*$ ]]; then
  printf 'error: GOMAXPROCS must be a positive integer when supplied\n' >&2
  exit 1
fi

python3 "$repo_root/scripts/android/verify-mesa-host.py" "$source_root"
command -v glslangValidator >/dev/null || {
  printf 'error: glslangValidator is required to build Waydroid Mesa shaders\n' >&2
  exit 1
}
python3 -c 'import mako, yaml, pycparser' >/dev/null 2>&1 || {
  printf 'error: Python Mako, PyYAML and pycparser are required by the Waydroid Mesa generator\n' >&2
  exit 1
}
test -x "$mesa_tool_root/mesa_clc" || {
  printf 'error: pinned Waydroid mesa_clc wrapper is missing\n' >&2
  exit 1
}
# Mesa runs its host compilers only from the identical source version.
grep -Fqx "MESA_VERSION=$(cat "$source_root/external/mesa/VERSION")" "$mesa_tool_root/BUILD-INFO" 2>/dev/null || {
  printf 'error: Mesa host tools do not match external/mesa; build them with scripts/android/build-mesa-host-tools.sh and set LUMA_ANDROID_MESA_TOOLS\n' >&2
  exit 1
}

test -d "$source_root/.repo" || {
  printf 'error: synchronize the pinned Android source first\n' >&2
  exit 1
}
test -x "$waydroid_patch_script" || {
  printf 'error: official Waydroid patch driver is missing\n' >&2
  exit 1
}

cleanup() {
  cd "$source_root"
  repo forall -c 'git reset --hard -q "$REPO_LREV"; git clean -fdq'
}

if [ "$incremental" = 1 ]; then
  "$prepare_script"
else
  "$prepare_script" --reset
  test "$(git -C "$hardware_root" rev-parse HEAD)" = \
    "$LUMA_WAYDROID_HARDWARE_REVISION"
  test "$(git -C "$framework_root" rev-parse HEAD)" = \
    "$LUMA_ANDROID_FRAMEWORKS_BASE_REVISION"
  test "$(git -C "$device_root" rev-parse HEAD)" = \
    "$LUMA_WAYDROID_DEVICE_REVISION"
  dirty_projects=$(cd "$source_root" && repo forall -c \
    'test -z "$(git status --porcelain)" || printf "%s\n" "$REPO_PATH"')
  test -z "$dirty_projects" || {
    printf 'error: Android source tree has uncommitted changes:\n%s\n' \
      "$dirty_projects" >&2
    exit 1
  }
  trap cleanup EXIT
fi

# Waydroid's vendor tree ships required Android integration patches outside
# the synced projects. Apply that complete, version-matched series before the
# Prairie delta. Among other fundamentals, it defines the container's `host`
# identity used by init.waydroid.rc. Building without this layer can produce
# individually linkable components but cannot produce a valid product image.
if [ "$incremental" = 0 ]; then
  (
    cd "$source_root"
    GIT_COMMITTER_NAME='Project Luma Build' \
      GIT_COMMITTER_EMAIL='build@projectluma.invalid' \
      TERM=${TERM:-xterm} \
      bash "$waydroid_patch_script"
  )
fi

# Waydroid's official build-tools patch exposes its version-matched Mesa host
# compilers through this directory. LineageOS 20 does not add that directory to
# the shell PATH inherited by external/mesa/android/mesa3d_cross.mk, so make the
# upstream-pinned wrappers explicit rather than substituting a host binary.
export PATH="$mesa_tool_root:$source_root/prebuilts/build-tools/path/linux-x86:$PATH"
command -v mesa_clc >/dev/null || {
  printf 'error: official Waydroid mesa_clc PATH patch was not applied\n' >&2
  exit 1
}

waydroid_core_subjects=$(git -C "$source_root/system/core" log \
  --format=%s --max-count=80)
grep -Fqx 'init: Define "host" user' <<<"$waydroid_core_subjects" || {
    printf 'error: official Waydroid host identity patch was not applied\n' >&2
    exit 1
  }

if [ "$incremental" = 0 ]; then
  git -C "$hardware_root" apply --binary "$patch_file"
  git -C "$hardware_root" apply "$pointer_patch"
  git -C "$hardware_root" apply "$frame_patch"
  git -C "$hardware_root" apply "$treatment_patch"
  git -C "$hardware_root" apply "$lumaui_frame_patch"
  git -C "$hardware_root" apply "$window_behavior_patch"
  git -C "$hardware_root" apply "$chrome_ownership_patch"
  git -C "$hardware_root" apply "$task_lifetime_patch"
  git -C "$hardware_root" apply "$initial_resize_patch"
  git -C "$hardware_root" apply "$rpc_threadpool_patch"
  git -C "$hardware_root" apply "$buffer_bounds_patch"
  git -C "$hardware_root" apply "$configure_lock_patch"
  git -C "$hardware_root" apply "$native_clipboard_patch"
  git -C "$hardware_root" apply "$clipboard_transfer_patch"
  git -C "$hardware_root" apply "$task_admission_patch"
  git -C "$hardware_root" apply "$task_channel_patch"
  git -C "$hardware_root" apply "$app_settings_patch"
  git -C "$hardware_root" apply "$desktop_canvas_patch"
  git -C "$hardware_root" apply "$handheld_fill_patch"
  git -C "$hardware_root" apply --binary "$text_input_patch"
  git -C "$hardware_root" apply "$video_layer_patch"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-clipboard-transfer.h" "$hardware_root/hwcomposer/luma-clipboard-transfer.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-rgb-copy-bounds.h" "$hardware_root/hwcomposer/luma-rgb-copy-bounds.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-task-lifetime.h" "$hardware_root/hwcomposer/luma-task-lifetime.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-window-callbacks.h" "$hardware_root/hwcomposer/luma-window-callbacks.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/luma-task-resize.h" "$hardware_root/hwcomposer/luma-task-resize.h"
  cmp "$repo_root/patches/android-hardware-waydroid/native/Figtree-OFL.txt" "$hardware_root/hwcomposer/fonts/OFL.txt"
  for header in luma-window-frame.h luma-frame-tokens.h luma-frame-glyphs.h; do
    cmp "$repo_root/src/luma-platform/compat/$header" "$hardware_root/hwcomposer/$header"
  done
  git -C "$framework_root" apply "$framework_patch"
  git -C "$source_root/lineage-sdk" apply "$lineage_task_lifetime_patch"
  git -C "$source_root/lineage-sdk" apply "$lineage_notification_patch"
  git -C "$source_root/lineage-sdk" apply "$lineage_admission_patch"
  git -C "$source_root/lineage-sdk" apply "$lineage_clipboard_patch"
  git -C "$source_root/lineage-sdk" apply "$lineage_reply_patch"
  git -C "$source_root/lineage-sdk" apply "$lineage_icon_patch"
  cmp "$repo_root/patches/android-lineage-sdk/java/HostCallerPolicy.java" \
    "$source_root/lineage-sdk/lineage/lib/main/java/org/lineageos/platform/internal/HostCallerPolicy.java"
  cmp "$repo_root/patches/android-lineage-sdk/java/NotificationReplyDispatcher.java" \
    "$source_root/lineage-sdk/lineage/lib/main/java/org/lineageos/platform/internal/NotificationReplyDispatcher.java"
  # Keep clean builds aligned with the prepared incremental tree. Without
  # inherited task ownership, child video surfaces can disappear when more
  # than one Android application is open.
  git -C "$source_root/frameworks/native" apply "$native_patch"
  git -C "$device_root" apply "$device_patch"
  git -C "$device_root" apply "$codec_patch"
  git -C "$device_root" apply "$input_method_patch"
  git -C "$source_root/packages/apps/Jelly" apply "$jelly_patch"
  git -C "$source_root/vendor/extra" apply "$vendor_extra_patch"
  git -C "$source_root/system/libhidl" apply "$libhidl_patch"
  python3 "$repo_root/scripts/android/integrate-software-gralloc.py" "$source_root"
  git -C "$source_root/external/mesa" apply "$repo_root/patches/android-mesa/0001-bound-nested-build-jobs.patch"
  git -C "$source_root/external/mesa" apply "$repo_root/patches/android-mesa/0002-waydroid-android-formats.patch"
  git -C "$source_root/external/mesa" apply "$repo_root/patches/android-mesa/0003-gbm-serialize-shared-context-maps.patch"
  git -C "$source_root/external/minigbm" apply "$repo_root/patches/android-minigbm/0001-gbm-mesa-describe-yv12-planes.patch"
  git -C "$source_root/build/soong" apply "$soong_gc_patch"
  git -C "$source_root/build/soong" apply "$soong_cpu_patch"
  git -C "$source_root/build/blueprint" apply "$blueprint_cpu_patch"
  git -C "$source_root/build/make" apply "$make_host_patch"
  git -C "$source_root/system/core" apply "$property_admission_patch"
fi
printf '%s  %s\n' "$expected_font_sha" \
  "$hardware_root/hwcomposer/fonts/Figtree-VF.ttf" | sha256sum --check --status

if [ "$target_arch" = aarch64 ]; then
  grep -Fq 'int gralloc_lock_ycbcr(' "$source_root/hardware/libhardware/modules/gralloc/mapper.cpp" || {
    printf 'error: ARM image source lacks the admitted software-YUV allocator; reconcile the pinned allocator before building a replacement image\n' >&2
    exit 1
  }
fi
cd "$source_root"
# shellcheck source=/dev/null
set +u
source build/envsetup.sh
lunch "lineage_${target_product}-userdebug"
if [ "${LUMA_BUILD_WAYDROID_IMAGES:-0}" = 1 ]; then
  m "${build_job_args[@]}" systemimage vendorimage
else
  m "${build_job_args[@]}" \
    hwcomposer.waydroid vendor.waydroid.task@1.0-service prairie-figtree-font prairie-figtree-license \
    framework-minus-apex SystemUI
fi

output="$repo_root/build/android/prairie-windowing/$target_arch"
product_root="out/target/product/$target_product"
install -d -m 0755 \
  "$output/system/bin/hw" \
  "$output/system/etc/init" \
  "$output/system/framework" \
  "$output/system_ext/priv-app/SystemUI" \
  "$output/vendor/etc/fonts" \
  "$output/vendor/etc/licenses/prairie-figtree" \
  "$output/vendor/lib64/hw"
install -m 0755 \
  "$product_root/system/bin/hw/vendor.waydroid.task@1.0-service" \
  "$output/system/bin/hw/"
install -m 0644 \
  "$product_root/system/etc/init/vendor.waydroid.task@1.0-service.rc" \
  "$output/system/etc/init/"
install -m 0644 \
  "$product_root/system/framework/framework.jar" \
  "$output/system/framework/"
install -m 0644 \
  "$product_root/system/system_ext/priv-app/SystemUI/SystemUI.apk" \
  "$output/system_ext/priv-app/SystemUI/"
install -m 0644 \
  "$product_root/vendor/etc/fonts/Figtree-VF.ttf" \
  "$output/vendor/etc/fonts/"
install -m 0644 \
  "$product_root/vendor/etc/licenses/prairie-figtree/OFL.txt" \
  "$output/vendor/etc/licenses/prairie-figtree/"
install -m 0644 \
  "$product_root/vendor/lib64/hw/hwcomposer.waydroid.so" \
  "$output/vendor/lib64/hw/"
# shellcheck source=write-checksums.sh
source "$repo_root/scripts/android/write-checksums.sh"
if [ "${LUMA_BUILD_WAYDROID_IMAGES:-0}" = 1 ]; then
  install -d -m 0755 "$output/images"
  install -m 0644 "$product_root/system.img" "$output/images/system.img"
  install -m 0644 "$product_root/vendor.img" "$output/images/vendor.img"
  luma_android_write_checksums "$output/images" system.img vendor.img
fi
luma_android_write_checksums "$output"
printf 'Prairie Waydroid windowing components: %s\n' "$output"
