#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build the shared, architecture-independent Android integration RPM on the
# native Fedora 44 AArch64 composition host. This creates an offline bundle;
# it never contacts or changes a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

for tool in cpio desktop-file-validate mktemp python3 rpm rpm2cpio rpmbuild rpmspec sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || die "missing native build tool: $tool"
done

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || \
  die 'build the FP6 Android runtime bundle on native Linux/aarch64'
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}" = fedora ] && [ "${VERSION_ID:-}" = 44 ] || \
  die 'build the FP6 Android runtime bundle on Fedora 44'

output_dir=${1:-$repo_root/build/mobile/fp6-physical/luma-apps-aarch64/android-runtime}
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"
install -d -m 0755 "$(dirname -- "$output_dir")"
work_dir=$(mktemp -d "${output_dir}.work.XXXXXX")
rpmbuild_dir="$work_dir/rpmbuild"
source_dir="$work_dir/luma-android-runtime"
install -d -m 0755 "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} \
  "$source_dir/config/profiles" "$source_dir/config/hardware"

cp -R "$repo_root/src/luma-android/luma_android" "$source_dir/"
cp -R "$repo_root/src/luma-android/tests" "$source_dir/"
cp -R "$repo_root/src/luma-android/bin" "$repo_root/src/luma-android/data" \
  "$source_dir/"
cp "$repo_root/config/android/luma-android.conf" "$source_dir/config/"
cp "$repo_root/config/android/fp6-software-weston.ini" "$source_dir/config/"
cp "$repo_root/config/android/profiles/"*.conf "$source_dir/config/profiles/"
cp "$repo_root/config/android/hardware/"*.sha256 "$source_dir/config/hardware/"
tar -C "$work_dir" -czf "$rpmbuild_dir/SOURCES/luma-android-runtime.tar.gz" \
  luma-android-runtime
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
install -m 0644 "$repo_root/scripts/packages/counted-unittest.py" \
  "$rpmbuild_dir/SOURCES/counted-unittest.py"
install -m 0644 "$repo_root/packaging/rpm/luma-android-runtime.spec" \
  "$rpmbuild_dir/SPECS/"

rpmbuild -ba --define "_topdir $rpmbuild_dir" \
  "$rpmbuild_dir/SPECS/luma-android-runtime.spec"

runtime_nevra=$(rpmspec -q --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' \
  "$rpmbuild_dir/SPECS/luma-android-runtime.spec")
[[ "$runtime_nevra" != *$'\n'* && "$runtime_nevra" == luma-android-runtime-*.noarch ]] || \
  die 'runtime recipe did not declare exactly one architecture-independent RPM'
rpm_name="$runtime_nevra.rpm"
rpm_path="$rpmbuild_dir/RPMS/noarch/$rpm_name"
[ -f "$rpm_path" ] || die "missing expected runtime package: $rpm_name"
rpm -Kv "$rpm_path" >/dev/null || die 'runtime RPM validation failed'

verify_dir=$(mktemp -d "$work_dir/verify.XXXXXX")
(
  cd "$verify_dir"
  rpm2cpio "$rpm_path" | cpio -idm --quiet
  python3 -m py_compile usr/lib/python3*/site-packages/luma_android/*.py
  test -x usr/lib/systemd/system-generators/luma-android-policy-generator
  grep -Fqx 'KEEP_WARM=false' usr/share/luma/android/profiles/handheld.conf
  grep -Fqx 'CPU_QUOTA_PERCENT=300' usr/share/luma/android/profiles/handheld.conf
  grep -Fq 'synchronize_launchers' usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq 'COLD_LAUNCH_REASSERT_DELAY_SECONDS' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq 'sys.boot_completed' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq '"container", "unfreeze"' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq 'gpu-fault-latched' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq 'permissions' usr/lib/python3*/site-packages/luma_android/cli.py
  grep -Fq '_report_launch_failure' \
    usr/lib/python3*/site-packages/luma_android/cli.py
  grep -Fq 'FP6_CANVAS_VALUE = "558x1088-sdl-2x-v1"' \
    usr/libexec/luma-waydroid-fp6-graphics-compat
  grep -Fq '"persist.waydroid.no_background_subsurface": "true"' \
    usr/libexec/luma-waydroid-fp6-graphics-compat
  test -x usr/libexec/luma-waydroid-fp6-gpu-watchdog
  ! grep -Fq 'CAP_SYS_PTRACE' \
    usr/lib/systemd/system/luma-waydroid-fp6-gpu-watchdog.service
  grep -Fq 'fp6_software_service_admitted' \
    usr/libexec/luma-waydroid-fp6-gpu-watchdog
  grep -Fqx 'SuccessExitStatus=71' \
    usr/lib/systemd/system/luma-waydroid-fp6-gpu-watchdog.service
  grep -Fq 'journalctl' \
    usr/libexec/luma-waydroid-fp6-gpu-watchdog
  grep -Fq 'FP6_SOFTWARE_HOST_PIXELS = "1116x2176"' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq '"+multitouch"' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq '"+workarea"' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq '"-decorations"' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq '"/clipboard:direction-to:off,files-to:off"' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq 'viewer_environment.pop("SDL_TOUCH_MOUSE_EVENTS", None)' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq 'viewer_environment.pop("SDL_MOUSE_TOUCH_EVENTS", None)' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq 'exit_code = viewer.poll()' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fqx 'RuntimeDirectoryPreserve=yes' \
    usr/lib/systemd/user/luma-android.service
  grep -Fq 'fp6_software_service_admitted(session_user=session_user)' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq 'FP6_SOFTWARE_LAB_MARKER = Path(' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq 'metadata.st_uid != 0' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq 'metadata.st_mode & 0o022' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq '/smart-sizing:' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  grep -Fq '"--no-resizeable"' \
    usr/libexec/luma-waydroid-fp6-software-compositor
  ! grep -Fq '"+dynamic-resolution"' \
    usr/lib/python3*/site-packages/luma_android/engine.py
  test -x usr/libexec/luma-waydroid-fp6-software-compositor
  test -f usr/lib/systemd/user/luma-android-fp6-software-compositor.service
  grep -Fqx 'PrivateDevices=yes' \
    usr/lib/systemd/user/luma-android-fp6-software-compositor.service
  grep -Fqx 'IPAddressAllow=localhost' \
    usr/lib/systemd/user/luma-android-fp6-software-compositor.service
  grep -Fqx 'refresh-rate=30' usr/share/luma/android/fp6-software-weston.ini
  ! grep -Eq '^[[:space:]]*[0-9a-f]{64}([[:space:]]|$)' \
    usr/share/luma/android/hardware/fairphone-fp6-kernel-notes.sha256
  grep -Fq 'BindsTo=luma-waydroid-fp6-gpu-watchdog.service' \
    usr/lib/systemd/system-generators/luma-android-policy-generator
)

bundle_dir="$output_dir/bundle"
install -d -m 0755 "$bundle_dir"
install -m 0644 "$rpm_path" "$bundle_dir/$rpm_name"
rpm_sha256=$(sha256sum "$bundle_dir/$rpm_name" | awk '{print $1}')
cat >"$output_dir/manifest.env" <<EOF
LUMA_FP6_ANDROID_RUNTIME_BUILD_VERSION=1
FEDORA_RELEASE=44
ARCHITECTURE=noarch
TARGET_ARCHITECTURE=aarch64
RUNTIME_NEVRA=$runtime_nevra
RPM_FILENAME=$rpm_name
RPM_SHA256=$rpm_sha256
HANDHELD_KEEP_WARM=false
HANDHELD_CPU_QUOTA_PERCENT=300
PHONE_ACCESSED=false
PACKAGE_INSTALLED=false
EOF
chmod 0644 "$output_dir/manifest.env"

printf 'FP6 shared Android runtime bundle: %s\n' "$output_dir"
