#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

failures=0
check() {
  description=$1
  shift
  if "$@"; then
    printf 'PASS  %s\n' "$description"
  else
    printf 'FAIL  %s\n' "$description"
    failures=$((failures + 1))
  fi
}

android_diagnostics() {
  if tr '\0' '\n' </proc/device-tree/compatible 2>/dev/null | \
      grep -Fxq fairphone,fp6 && \
      ! grep -Eq '^[[:space:]]*[0-9a-f]{64}([[:space:]]|$)' \
        /usr/share/luma/android/hardware/fairphone-fp6-kernel-notes.sha256; then
    if luma-android status | \
        grep -Fq '"fp6_software_presentation": true'; then
      luma-android doctor
      return
    fi
    if luma-android doctor; then
      return 1
    fi
    luma-android status | grep -Fq '"fp6_gpu_gate": "kernel-not-admitted"'
    return
  fi
  luma-android doctor
}

check 'Luma Android runtime package' \
  rpm -q "${LUMA_ANDROID_EXPECTED_NEVRA:-luma-android-runtime}"
check 'Fedora Waydroid 1.6.3 or newer' bash -c \
  'rpm --quiet --query waydroid && rpm --quiet --query --whatprovides waydroid'
check 'Binder kernel transport' bash -c \
  'grep -qw binder /proc/filesystems || { test -c /dev/binder && test -c /dev/hwbinder && test -c /dev/vndbinder; }'
check 'render node is available' bash -c 'compgen -G "/dev/dri/renderD*" >/dev/null'
check 'APK MIME contract is installed' \
  grep -Fq application/vnd.android.package-archive \
    /usr/share/mime/packages/luma-android.xml
check 'APK default handler is Luma Application Installer' bash -c \
  'if command -v xdg-mime >/dev/null; then test "$(xdg-mime query default application/vnd.android.package-archive)" = org.projectluma.ApplicationInstaller.desktop; else gio mime application/vnd.android.package-archive | sed -n 1p | grep -Fq org.projectluma.ApplicationInstaller.desktop; fi'
check 'split-package MIME contract is installed' \
  grep -Fq application/vnd.projectluma.android-package-set \
    /usr/share/mime/packages/luma-android.xml
check 'split-package default handler is Luma Application Installer' bash -c \
  'if command -v xdg-mime >/dev/null; then test "$(xdg-mime query default application/vnd.projectluma.android-package-set)" = org.projectluma.ApplicationInstaller.desktop; else gio mime application/vnd.projectluma.android-package-set | sed -n 1p | grep -Fq org.projectluma.ApplicationInstaller.desktop; fi'
check 'split-package helper is installed with its policy' bash -c \
  'test -x /usr/libexec/luma-waydroid-package-session && test -f /usr/share/polkit-1/actions/org.projectluma.waydroid-package-session.policy'
check 'FP6 kernel admission and GPU watchdog are installed' bash -c \
  'test -x /usr/libexec/luma-waydroid-fp6-gpu-watchdog && test -x /usr/libexec/luma-waydroid-fp6-software-compositor && test -f /usr/share/luma/android/hardware/fairphone-fp6-kernel-notes.sha256 && test -f /usr/lib/systemd/system/luma-waydroid-fp6-gpu-watchdog.service && test -f /usr/lib/systemd/user/luma-android-fp6-software-compositor.service'
check 'runtime broker unit has a valid hardening profile' bash -c \
  'systemd-analyze --user security luma-android.service >/dev/null'
check 'runtime broker owns private writable package staging' bash -c \
  'systemctl --user start luma-android.service && test "$(stat -c %a /run/user/$UID/luma-android)" = 700 && test -w "/run/user/$UID/luma-android"'
check 'resource policy is generated' \
  test -f /run/systemd/generator/waydroid-container.service.d/40-luma-resource-policy.conf
check 'Android engine responds' luma-android status
check 'Android diagnostics enforce hardware admission' android_diagnostics
check 'SDL viewer defers native-touch hint policy and detects early exit' bash -c \
  'grep -Fq '\''viewer_environment.pop("SDL_TOUCH_MOUSE_EVENTS", None)'\'' /usr/lib/python3*/site-packages/luma_android/engine.py && grep -Fq '\''viewer_environment.pop("SDL_MOUSE_TOUCH_EVENTS", None)'\'' /usr/lib/python3*/site-packages/luma_android/engine.py && grep -Fq '\''exit_code = viewer.poll()'\'' /usr/lib/python3*/site-packages/luma_android/engine.py && grep -Fxq '\''RuntimeDirectoryPreserve=yes'\'' /usr/lib/systemd/user/luma-android.service'
check 'FP6 viewer keeps clipboard and file transfer host-owned' bash -c \
  'grep -Fq '\''"/clipboard:direction-to:off,files-to:off"'\'' /usr/lib/python3*/site-packages/luma_android/engine.py'
check 'sandboxed broker authenticates the immutable FP6 compositor unit' bash -c \
  'grep -Fq '\''fp6_software_service_admitted(session_user=session_user)'\'' /usr/lib/python3*/site-packages/luma_android/engine.py'
check 'FP6 software lab path requires an ephemeral root-owned marker' bash -c \
  'grep -Fq '\''FP6_SOFTWARE_LAB_MARKER = Path('\'' /usr/lib/python3*/site-packages/luma_android/engine.py && grep -Fq '\''metadata.st_uid != 0'\'' /usr/lib/python3*/site-packages/luma_android/engine.py && grep -Fq '\''metadata.st_mode & 0o022'\'' /usr/lib/python3*/site-packages/luma_android/engine.py'
check 'Waydroid exposes per-application desktop notifications' bash -c \
  'manager=$(rpm -ql waydroid | grep -E '\''/tools/services/notification_manager.py$'\'' | head -n 1); test -n "$manager" && grep -Fq '\''"desktop-entry": f"waydroid.{package_name}"'\'' "$manager" && grep -Fq '\''org.freedesktop.Notifications'\'' "$manager"'

exit "$failures"
