#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
hardware_patch="$repo_root/patches/android-hardware-waydroid/0001-prairie-native-android-window-chrome.patch"
framework_patch="$repo_root/patches/android-frameworks-base/0001-prairie-host-owned-window-chrome.patch"
device_patch="$repo_root/patches/android-device-waydroid/0001-prairie-host-window-product-property.patch"
builder="$repo_root/scripts/android/build-prairie-waydroid-windowing.sh"
prepare="$repo_root/scripts/android/prepare-prairie-waydroid-tree.sh"
builder="$repo_root/scripts/android/build-prairie-waydroid-windowing.sh"

for source in "$hardware_patch" "$framework_patch" "$device_patch" "$builder"; do
  test -f "$source"
done

# Host chrome must own both the visible caption and Android's nested positioner.
grep -Fq 'HOST_OWNS_WINDOW_CHROME' "$framework_patch"
grep -Fq '!HOST_OWNS_WINDOW_CHROME' "$framework_patch"
grep -Fq 'persist.luma.host_window_chrome' "$framework_patch"
grep -Fq '|| SystemProperties.getBoolean("ro.luma.host_window_chrome"' \
  "$framework_patch"
if grep -Fq '&& !SystemProperties.getBoolean("ro.luma.host_window_chrome"' \
  "$framework_patch"; then
  printf 'error: host chrome must suppress, not redirect to, legacy DecorCaptionView\n' >&2
  exit 1
fi
grep -Fq 'ro.luma.host_window_chrome=true' "$device_patch"

# Prairie retains one real Back action and one outer task-resize bridge.
grep -Fq 'TitlebarTarget::Back' "$hardware_patch"
grep -Fq 'KEY_BACK' "$hardware_patch"
grep -Fq 'setFocusedTask' "$hardware_patch"
grep -Fq 'resizeTaskNative' "$hardware_patch"
grep -Fq 'SOCK_SEQPACKET' "$hardware_patch"
grep -Fq 'SO_PEERCRED' "$hardware_patch"

# Transparent rounded corners must be premultiplied for Wayland ARGB buffers.
grep -Fq 'left_red = ((left_pixel >> 16) & 0xff) * coverage / 255' \
  "$hardware_patch"
grep -Fq 'right_blue = (right_pixel & 0xff) * coverage / 255' \
  "$hardware_patch"

# Old numbered patches are historical inputs, not the effective UI. Verify
# current frame/scheduler production code below rather than old circle constants.
current_frame_patch="$repo_root/patches/android-hardware-waydroid/0007-lumaui-current-inset-frame.patch"
behavior_patch="$repo_root/patches/android-hardware-waydroid/0008-luma-desktop-window-behavior-recovery.patch"
test -s "$current_frame_patch"
test -s "$behavior_patch"
grep -Fq 'property_get("persist.luma.device_class"' "$hardware_patch"
test -x "$builder"
test -x "$prepare"
grep -Fq 'LUMA_WAYDROID_INCREMENTAL' "$builder"
grep -Fq '"$prepare_script" --reset' "$builder"
grep -Fq 'TREE_SNAPSHOT=' "$prepare"
grep -Fq 'prepared Android tree drifted; refusing automatic reset' "$prepare"
grep -Fq 'std::strcmp(value, "handheld") != 0' "$hardware_patch"
grep -Fq 'state_changed || width_changed || height_changed' "$hardware_patch"
grep -Fq 'prairie::redraw_shadow(*window)' "$hardware_patch"

# The release lane can emit a coherent image pair; individual framework and
# SystemUI files are retained only for inspection and never treated as an image.
grep -Fq 'LUMA_BUILD_WAYDROID_IMAGES' "$builder"
grep -Fq 'LUMA_BUILD_JOBS' "$builder"
grep -Fq 'build_job_args+=("-j$build_jobs")' "$builder"
grep -Fq 'systemimage vendorimage' "$builder"
grep -Fq 'command -v meson' "$builder"
grep -Fq 'command -v glslangValidator' "$builder"
grep -Fq "python3 -c 'import mako, yaml, pycparser'" "$builder"
grep -Fq 'prebuilts/mesa-tools' "$builder"
grep -Fq 'prebuilts/build-tools/path/linux-x86:$PATH' "$builder"
grep -Fq 'command -v mesa_clc' "$builder"
grep -Fq 'install -m 0644 "$product_root/system.img"' "$builder"
grep -Fq 'install -m 0644 "$product_root/vendor.img"' "$builder"
grep -Fq 'sha256sum system.img vendor.img >SHA256SUMS' "$builder"

deployer="$repo_root/scripts/android/deploy-prairie-waydroid-images.sh"
test -x "$deployer"
grep -Fq 'sha256sum --check --strict SHA256SUMS' "$deployer"
grep -Fq 'prairie-windowing/x86_64/images' "$deployer"
grep -Fq 'systemctl is-active --quiet waydroid-container.service' "$deployer"
grep -Fq 'LUMA_WAYDROID_IMAGE_DIR:-/etc/waydroid-extra/images' "$deployer"
grep -Fq 'backup=$(mktemp -d "$image_target/.prairie-previous.XXXXXX")' "$deployer"
grep -Fq 'restorecon -RF "$image_target"' "$deployer"
grep -Fq 'waydroid init -f' "$deployer"

frame_patch="$repo_root/patches/android-hardware-waydroid/0003-luma-appkit-content-island.patch"
test -s "$frame_patch"
treatment_patch="$repo_root/patches/android-hardware-waydroid/0004-luma-surface-treatment-frame.patch"
test -s "$treatment_patch"
grep -Fq 'persist.luma.surface_treatment' "$treatment_patch"
grep -Fq 'SurfaceTreatment::Glass' "$treatment_patch"
grep -Fq 'scale_premultiplied' "$treatment_patch"
grep -Fq 'HARDWARE_FRAME_PATCH_SHA' "$prepare"
grep -Fq 'HARDWARE_TREATMENT_PATCH_SHA' "$prepare"
grep -Fq 'apply "$treatment_patch"' "$builder"
grep -Fq 'apply "$hardware_treatment_patch"' "$prepare"
grep -Fq 'lineage_${target_product}-userdebug' "$builder"
grep -Fq 'waydroid_arm64_only' "$builder"
# Compile/run actual shared geometry and pixels, not a simulated app window.
frame_test=$(mktemp)
trap 'rm -f "$frame_test"' EXIT
"${CC:-cc}" -std=c17 -O2 -Wall -Wextra -Werror \
  "$repo_root/src/luma-platform/tests/test-window-frame.c" -lm -o "$frame_test"
"$frame_test"
queue_test=$(mktemp)
trap 'rm -f "$frame_test" "$queue_test"' EXIT
"${CXX:-c++}" -std=c++17 -O2 -Wall -Wextra -Werror -pthread \
  -I"$repo_root/patches/android-hardware-waydroid/native" \
  "$repo_root/tests/android/task-resize-queue.cpp" -o "$queue_test"
"$queue_test"
callback_test=$(mktemp)
trap 'rm -f "$frame_test" "$queue_test" "$callback_test"' EXIT
"${CXX:-c++}" -std=c++17 -O2 -Wall -Wextra -Werror -pthread \
  -I"$repo_root/patches/android-hardware-waydroid/native" \
  "$repo_root/tests/android/window-callback-lifetime.cpp" -o "$callback_test"
"$callback_test"
task_lifetime_test=$(mktemp)
trap 'rm -f "$frame_test" "$queue_test" "$callback_test" "$task_lifetime_test"' EXIT
"${CXX:-c++}" -std=c++17 -O2 -Wall -Wextra -Werror -pthread \
  -I"$repo_root/patches/android-hardware-waydroid/native" \
  "$repo_root/tests/android/task-removal-lifetime.cpp" -o "$task_lifetime_test"
"$task_lifetime_test"
printf 'Prairie Android windowing source contract: PASS (image/runtime qualification separate)\n'
