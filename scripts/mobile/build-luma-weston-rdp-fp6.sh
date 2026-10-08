#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Rebuild Fedora 44's Weston package with Luma's narrow FP6 RDP presentation
# patches. The input is a previously downloaded, Fedora-signed SRPM; this
# script does not contact or modify a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/android/weston-fp6-source.env"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

for tool in cpio mktemp python3 readelf rpm rpm2cpio rpmbuild sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || die "missing native build tool: $tool"
done
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || \
  die 'build the FP6 Weston bundle on native Linux/aarch64'
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}" = fedora ] && [ "${VERSION_ID:-}" = 44 ] || \
  die 'build the FP6 Weston bundle on Fedora 44'

srpm=${1:-$repo_root/build/sources/$LUMA_WESTON_FEDORA_SRPM}
output_dir=${2:-$repo_root/build/mobile/fp6-physical/luma-apps-aarch64/weston-rdp-native-touch}
[ -f "$srpm" ] || die "missing signed Fedora source package: $srpm"
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"
printf '%s  %s\n' "$LUMA_WESTON_FEDORA_SRPM_SHA256" "$srpm" | sha256sum -c -
rpm -Kv "$srpm" | grep -Eiq 'signature.*OK' || \
  die 'Fedora source package signature validation failed'

install -d -m 0755 "$(dirname -- "$output_dir")"
work_dir=$(mktemp -d "${output_dir}.work.XXXXXX")
rpmbuild_dir="$work_dir/rpmbuild"
extracted="$work_dir/extracted"
install -d -m 0755 "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} \
  "$extracted"
(
  cd "$extracted"
  rpm2cpio "$srpm" | cpio -idm --quiet
)
source_tar="$extracted/$LUMA_WESTON_SOURCE_TAR"
[ -f "$source_tar" ] || die "source RPM lacks $LUMA_WESTON_SOURCE_TAR"
printf '%s  %s\n' "$LUMA_WESTON_SOURCE_TAR_SHA256" "$source_tar" | sha256sum -c -

touch_patch="$repo_root/patches/weston/0001-luma-rdp-native-touch-host-keys.patch"
host_keys_patch="$repo_root/patches/weston/0002-luma-rdp-keep-hardware-keys-host-owned.patch"
dvc_ready_patch="$repo_root/patches/weston/0003-luma-rdp-defer-rdpei-until-dvc-ready.patch"
printf '%s  %s\n' "$LUMA_WESTON_TOUCH_PATCH_SHA256" "$touch_patch" | sha256sum -c -
printf '%s  %s\n' "$LUMA_WESTON_HOST_KEYS_PATCH_SHA256" "$host_keys_patch" | sha256sum -c -
printf '%s  %s\n' "$LUMA_WESTON_DVC_READY_PATCH_SHA256" "$dvc_ready_patch" | sha256sum -c -

install -m 0644 "$source_tar" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$touch_patch" "$host_keys_patch" "$dvc_ready_patch" \
  "$rpmbuild_dir/SOURCES/"
python3 - "$extracted/weston.spec" "$rpmbuild_dir/SPECS/weston.spec" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
text = source.read_text(encoding="utf-8")

def replace_once(old: str, new: str) -> None:
    global text
    if text.count(old) != 1:
        raise SystemExit(f"unexpected Fedora spec structure: {old!r}")
    text = text.replace(old, new, 1)

replace_once("Release:        %autorelease", "Release:        2.luma.4%{?dist}")
source_line = (
    "Source0:        https://gitlab.freedesktop.org/wayland/%{name}/-/releases/"
    "%{version}/downloads/%{name}-%{version}.tar.xz\n"
)
replace_once(
    source_line,
    source_line
    + "Patch0:         0001-luma-rdp-native-touch-host-keys.patch\n"
    + "Patch1:         0002-luma-rdp-keep-hardware-keys-host-owned.patch\n"
    + "Patch2:         0003-luma-rdp-defer-rdpei-until-dvc-ready.patch\n",
)
replace_once(
    "%changelog\n",
    "%changelog\n"
    "* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 15.0.1-2.luma.4\n"
    "- Defer drdynvc auto-open until the RDP peer is activated\n"
    "\n"
    "* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 15.0.1-2.luma.3\n"
    "- Open RDPEI when drdynvc reaches READY without a redundant name lookup\n"
    "\n"
    "* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 15.0.1-2.luma.2\n"
    "- Start RDPEI only after FreeRDP's dynamic-channel manager is ready\n"
    "- Terminate FreeRDP RDPEI input as native multi-contact Weston touch\n"
    "- Keep volume, mute, brightness, power, and camera hardware keys host-owned\n\n",
)
destination.write_text(text, encoding="utf-8")
PY

rpmbuild -ba --define "_topdir $rpmbuild_dir" "$rpmbuild_dir/SPECS/weston.spec"

bundle="$work_dir/bundle"
install -d -m 0755 "$bundle"
packages=(
  "$LUMA_WESTON_NEVRA"
  "$LUMA_WESTON_LIBS_NEVRA"
  "$LUMA_WESTON_RDP_NEVRA"
  "$LUMA_WESTON_VNC_NEVRA"
)
for nevra in "${packages[@]}"; do
  rpm_path="$rpmbuild_dir/RPMS/aarch64/$nevra.rpm"
  [ -f "$rpm_path" ] || die "missing expected Weston package: $nevra"
  rpm -Kv "$rpm_path" >/dev/null || die "invalid built package: $nevra"
  install -m 0644 "$rpm_path" "$bundle/"
done

verify_dir=$(mktemp -d "$work_dir/verify.XXXXXX")
(
  cd "$verify_dir"
  rpm2cpio "$bundle/$LUMA_WESTON_RDP_NEVRA.rpm" | cpio -idm --quiet
  backend=usr/lib64/libweston-15/rdp-backend.so
  [ -f "$backend" ]
  symbols=rdp-backend.symbols
  readelf -Ws "$backend" >"$symbols"
  grep -Fq rdpei_server_context_new "$symbols"
  grep -Fq rdpei_server_handle_messages "$symbols"
  grep -Fq rdpei_server_send_sc_ready "$symbols"
  grep -Fq WTSVirtualChannelManagerCheckFileDescriptorEx "$symbols"
  grep -Fq WTSVirtualChannelManagerGetDrdynvcState "$symbols"
  grep -Fq notify_touch_frame "$symbols"
)

mv "$work_dir" "$output_dir"
printf 'FP6 native-touch Weston bundle: %s\n' "$output_dir"
