#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
# shellcheck disable=SC1091
. "$repo_root/config/android/images-aarch64.env"
# shellcheck disable=SC1091
. "$repo_root/config/android/weston-fp6-source.env"

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$LUMA_ANDROID_RUNTIME_NEVRA" = \
  luma-android-runtime-0.1.0-1.luma.60.fc44.noarch ] || \
  fail 'unexpected shared Android runtime pin'
[ "$LUMA_ANDROID_RUNTIME_RELEASE" = 1.luma.60 ] || \
  fail 'unexpected shared Android runtime release'
grep -Fq 'BuildRequires:  systemd-rpm-macros' \
  "$repo_root/packaging/rpm/luma-android-runtime.spec"
[ "$NAUTILUS_AARCH64_NEVRA" = \
  nautilus-50.2.2-1.luma.44.preview20260905.fc44.aarch64 ] || \
  fail 'unexpected shared AArch64 Filer pin'
[ "$NAUTILUS_EXTENSIONS_AARCH64_NEVRA" = \
  nautilus-extensions-50.2.2-1.luma.44.preview20260905.fc44.aarch64 ] || \
  fail 'unexpected shared AArch64 Filer extensions pin'
case "$LUMA_ANDROID_SYSTEM_FILENAME:$LUMA_ANDROID_VENDOR_FILENAME" in
  *waydroid_arm64_only-system.zip:*waydroid_arm64_only-vendor.zip) ;;
  *) fail 'mobile image pair is not ARM64-only' ;;
esac
for digest in "$LUMA_ANDROID_SYSTEM_SHA256" "$LUMA_ANDROID_VENDOR_SHA256"; do
  case "$digest" in
    *[!0-9a-f]*) fail 'Android image digest is not lowercase hexadecimal' ;;
  esac
  [ "${#digest}" -eq 64 ] || fail 'Android image digest is not SHA-256 length'
done
[ "$LUMA_ANDROID_SYSTEM_SIZE" -eq 728517282 ] || fail 'unexpected system image size'
[ "$LUMA_ANDROID_VENDOR_SIZE" -eq 77349347 ] || fail 'unexpected vendor image size'

grep -Fxq luma-android-runtime "$repo_root/config/shared/application-packages.txt"
grep -Fxq nautilus "$repo_root/config/shared/application-packages.txt"
grep -Fxq KEEP_WARM=false "$repo_root/config/android/profiles/handheld.conf"
grep -Fxq CPU_QUOTA_PERCENT=300 "$repo_root/config/android/profiles/handheld.conf"
grep -Fq fairphone,fp6 \
  "$repo_root/src/luma-android/bin/luma-waydroid-fp6-graphics-compat"
grep -Fq 'FP6_CANVAS_VALUE = "558x1088-sdl-2x-v1"' \
  "$repo_root/src/luma-android/bin/luma-waydroid-fp6-graphics-compat"
grep -Fq '"persist.waydroid.use_subsurface": "false"' \
  "$repo_root/src/luma-android/bin/luma-waydroid-fp6-graphics-compat"
grep -Fq 'gpu-fault-latched' "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq 'GPU watchdog active while Android runs' \
  "$repo_root/src/luma-android/luma_android/settings.py"
! grep -Eq '^[[:space:]]*[0-9a-f]{64}([[:space:]]|$)' \
  "$repo_root/config/android/hardware/fairphone-fp6-kernel-notes.sha256"
grep -Fq 'hangcheck detected gpu lockup' \
  "$repo_root/src/luma-android/bin/luma-waydroid-fp6-gpu-watchdog"
grep -Fq 'PrivateDevices=yes' \
  "$repo_root/src/luma-android/data/luma-android-fp6-software-compositor.service"
grep -Fq 'IPAddressAllow=localhost' \
  "$repo_root/src/luma-android/data/luma-android-fp6-software-compositor.service"
grep -Fq 'fp6_software_presentation_active' \
  "$repo_root/src/luma-android/luma_android/kernel.py"
! grep -Fq 'CAP_SYS_PTRACE' \
  "$repo_root/src/luma-android/data/luma-waydroid-fp6-gpu-watchdog.service"
grep -Fxq 'SuccessExitStatus=71' \
  "$repo_root/src/luma-android/data/luma-waydroid-fp6-gpu-watchdog.service"
grep -Fq 'fp6_software_service_admitted' \
  "$repo_root/src/luma-android/bin/luma-waydroid-fp6-gpu-watchdog"
grep -Fq 'journalctl' \
  "$repo_root/src/luma-android/bin/luma-waydroid-fp6-gpu-watchdog"
grep -Fq 'FP6_SOFTWARE_HOST_PIXELS = "1116x2176"' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq 'patched Weston RDP' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq '"+multitouch"' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq '"+workarea"' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq '"-decorations"' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq '"/clipboard:direction-to:off,files-to:off"' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq 'viewer_environment.pop("SDL_TOUCH_MOUSE_EVENTS", None)' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq 'viewer_environment.pop("SDL_MOUSE_TOUCH_EVENTS", None)' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq 'viewer_environment["SDL_RENDER_DRIVER"] = "software"' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq '_monitor_fp6_software_viewer' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq '"/usr/bin/waydroid", "session", "stop"' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq '"/gdi:sw"' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq '"-gfx"' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq 'exit_code = viewer.poll()' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fxq 'RuntimeDirectoryPreserve=yes' \
  "$repo_root/src/luma-android/data/luma-android.service"
grep -Fxq 'refresh-rate=30' \
  "$repo_root/config/android/fp6-software-weston.ini"
grep -Fq 'fp6_software_service_admitted(session_user=session_user)' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq 'FP6_SOFTWARE_LAB_MARKER = Path(' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq 'metadata.st_uid != 0' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq 'metadata.st_mode & 0o022' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq '/smart-sizing:' \
  "$repo_root/src/luma-android/luma_android/engine.py"
! grep -Fq '"/f"' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fq '"--no-resizeable"' \
  "$repo_root/src/luma-android/bin/luma-waydroid-fp6-software-compositor"
! grep -Fq '"+dynamic-resolution"' \
  "$repo_root/src/luma-android/luma_android/engine.py"
grep -Fxq 'd %h/.local/share/waydroid 0700 - - -' \
  "$repo_root/src/luma-android/data/luma-android-user.conf"

composer="$repo_root/scripts/mobile/compose-fp6-rootfs.sh"
runtime_builder="$repo_root/scripts/mobile/build-luma-android-runtime-fp6.sh"
filer_builder="$repo_root/scripts/mobile/build-luma-filer-fp6.sh"
image_fetcher="$repo_root/scripts/android/fetch-pinned-images.sh"
image_preparer="$repo_root/scripts/android/prepare-pinned-images.sh"
accepted_runtime="$repo_root/build/mobile/fp6-physical/luma-apps-aarch64/android-runtime"
accepted_weston="$repo_root/build/mobile/fp6-physical/luma-apps-aarch64/weston-rdp-native-touch"
test -x "$composer"
test -x "$runtime_builder"
test -x "$filer_builder"
test -x "$image_fetcher"
test -x "$image_preparer"
test -x "$repo_root/scripts/mobile/build-luma-weston-rdp-fp6.sh"
grep -Fq 'android-runtime/bundle' "$composer"
grep -Fq 'weston-rdp-native-touch/bundle' "$composer"
grep -Fq 'filer/bundle' "$composer"
grep -Fq 'printf '\''handheld\n'\'' >"$rootfs/etc/luma-device-class"' "$composer"
grep -Fq 'HANDHELD_KEEP_WARM=false' "$runtime_builder"
grep -Fq 'CPU_QUOTA_PERCENT=300' "$runtime_builder"
grep -Fq 'synchronize_launchers' "$runtime_builder"
grep -Fq 'org.projectluma.ApplicationInstaller1' "$filer_builder"
grep -Fq 'RequestInstall' "$filer_builder"
grep -Fq "tr -d '[:space:]'" "$image_fetcher"
grep -Fq 'sha256sum --check --strict SHA256SUMS' "$image_preparer"

[ "$LUMA_WESTON_NEVRA" = weston-15.0.1-2.luma.4.fc44.aarch64 ]
[ "$LUMA_WESTON_LIBS_NEVRA" = weston-libs-15.0.1-2.luma.4.fc44.aarch64 ]
[ "$LUMA_WESTON_RDP_NEVRA" = \
  weston-libs-backend-rdp-15.0.1-2.luma.4.fc44.aarch64 ]
[ "$LUMA_WESTON_VNC_NEVRA" = \
  weston-libs-backend-vnc-15.0.1-2.luma.4.fc44.aarch64 ]
printf '%s  %s\n' "$LUMA_WESTON_TOUCH_PATCH_SHA256" \
  "$repo_root/patches/weston/0001-luma-rdp-native-touch-host-keys.patch" | \
  sha256sum -c - >/dev/null
printf '%s  %s\n' "$LUMA_WESTON_HOST_KEYS_PATCH_SHA256" \
  "$repo_root/patches/weston/0002-luma-rdp-keep-hardware-keys-host-owned.patch" | \
  sha256sum -c - >/dev/null
printf '%s  %s\n' "$LUMA_WESTON_DVC_READY_PATCH_SHA256" \
  "$repo_root/patches/weston/0003-luma-rdp-defer-rdpei-until-dvc-ready.patch" | \
  sha256sum -c - >/dev/null
for nevra in "$LUMA_WESTON_NEVRA" "$LUMA_WESTON_LIBS_NEVRA" \
  "$LUMA_WESTON_RDP_NEVRA" "$LUMA_WESTON_VNC_NEVRA"; do
  test -f "$accepted_weston/bundle/$nevra.rpm"
  rpm -Kv "$accepted_weston/bundle/$nevra.rpm" >/dev/null
done
grep -Fxq NATIVE_RDPEI_TOUCH=true "$accepted_weston/manifest.env"
grep -Fxq HOST_HARDWARE_KEYS=true "$accepted_weston/manifest.env"
grep -Fxq PHYSICAL_TOUCH_ACCEPTANCE=pending "$accepted_weston/manifest.env"

grep -Fxq "RUNTIME_NEVRA=$LUMA_ANDROID_RUNTIME_NEVRA" \
  "$accepted_runtime/manifest.env"
grep -Fxq \
  'RPM_SHA256=100dc6e2ae05fb49bb178d5492109a590db1e24171930a5e833401636ca438f2' \
  "$accepted_runtime/manifest.env"
runtime_rpm="$accepted_runtime/bundle/$LUMA_ANDROID_RUNTIME_NEVRA.rpm"
test -f "$runtime_rpm"
if command -v sha256sum >/dev/null 2>&1; then
  runtime_sha256=$(sha256sum "$runtime_rpm" | awk '{print $1}')
else
  runtime_sha256=$(shasum -a 256 "$runtime_rpm" | awk '{print $1}')
fi
[ "$runtime_sha256" = \
  100dc6e2ae05fb49bb178d5492109a590db1e24171930a5e833401636ca438f2 ] || \
  fail 'accepted FP6 Android runtime hash differs'

filer_patch="$repo_root/patches/nautilus/0015-luma-application-installer-drop.patch"
for extension in apk apks xapk apkm; do
  grep -Fqi ".$extension" "$filer_patch" || fail "Filer lacks .$extension handoff"
done

printf 'Mobile shared Android source contract: PASS\n'
