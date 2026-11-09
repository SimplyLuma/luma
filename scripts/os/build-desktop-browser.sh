#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the shared host around its admitted engine using checked toolkit inputs.
set -euo pipefail
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
[ "${LUMA_RPM_BUILDER_DIRECT:-0}" = 1 ] && [ "$(id -u)" = 0 ] &&
  { [ -f /run/.containerenv ] || [ -f /.dockerenv ]; } || {
  printf 'error: use an isolated native Fedora browser builder\n' >&2; exit 1;
}
architecture=${LUMA_TARGET_ARCHITECTURE:?select the target CPU}
case "$architecture" in
  x86_64) engine_release=35; engine_sha=ab4b8994ff4634780102671070a6d3908161438c3c0baafb1db9c6f74a3e5b37 ;;
  aarch64) engine_release=36; engine_sha=072d4bb42a64cde7efee51c653e112adbe15a24d078a098c0d501520984bb3b0 ;;
  *) exit 2 ;;
esac
[ "$(rpm --eval '%{_arch}')" = "$architecture" ]
. "$root/config/desktop/inputs.env"
# Verify whole retained build manifests before selecting any dependency RPM.
python3 - "$root/build/browser-inputs" <<'PY'
from pathlib import Path
import hashlib,sys
root=Path(sys.argv[1]).resolve()
for receipt in root.rglob('SHA256SUMS'):
    for line in receipt.read_text().splitlines():
        expected,name=line.split(None,1)
        path=receipt.parent/name
        if path.is_symlink() or not path.resolve().is_relative_to(receipt.parent.resolve()):
            raise SystemExit('Unsafe checked artifact member')
        if hashlib.file_digest(path.open('rb'),'sha256').hexdigest()!=expected:
            raise SystemExit('Checked artifact body differs: '+name)
print('Retained dependency package manifests verified')
PY
inputdir="$root/build/browser-inputs"
gtk=${GTK4_NEVRA%.*}.$architecture
adwaita=${LIBADWAITA_NEVRA%.*}.$architecture
platform=${LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA%.*}.$architecture
apk_metadata=${LUMA_APK_METADATA_X86_64_NEVRA%.*}.$architecture
rpms=()
for identity in "$gtk" "$adwaita" "$platform" "$apk_metadata" \
  "luma-appimage-compat-0.1.0-1.luma.1.fc44.$architecture"; do
  mapfile -t matches < <(find "$inputdir" -type f -name "$identity.rpm")
  [ "${#matches[@]}" -ge 1 ] || { printf 'Missing checked browser provider: %s\n' "$identity" >&2; exit 1; }
  expected_sha=
  for file in "${matches[@]}"; do
    [ "$(rpm -qp --nosignature --qf '%{NEVRA}' "$file")" = "$identity" ]
    actual_sha=$(sha256sum "$file" | cut -d ' ' -f1)
    [ -z "$expected_sha" ] || [ "$actual_sha" = "$expected_sha" ] || {
      printf 'Conflicting checked browser provider bodies: %s\n' "$identity" >&2; exit 1;
    }
    expected_sha=$actual_sha
  done
  printf 'Checked browser provider: %s (%s identical retained copies)\n' "$identity" "${#matches[@]}"
  rpms+=("${matches[0]}")
done
installer=luma-application-installer-0.1.0-1.luma.86.fc44.noarch.rpm
installer_sha=ef2b347d41b654f7f6788ab254be031a995662402b0000cd2166c86aa939e3bc
mkdir -p "$inputdir/mirrored"
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
  "https://media.simplyluma.com/inputs/rpm/$installer_sha/$installer" --output "$inputdir/mirrored/$installer"
printf '%s  %s\n' "$installer_sha" "$inputdir/mirrored/$installer" | sha256sum -c -
[ "$(rpm -qp --nosignature --qf '%{NEVRA}' "$inputdir/mirrored/$installer")" = "${installer%.rpm}" ]
rpms+=("$inputdir/mirrored/$installer")
dnf5 -y install "${rpms[@]}"
engine="$inputdir/mirrored/viola-native-engine-0.2.10-native$engine_release.tar.zst"
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
  "https://media.simplyluma.com/inputs/viola-engine/$engine_sha/$(basename "$engine")" --output "$engine"
printf '%s  %s\n' "$engine_sha" "$engine" | sha256sum -c -
export LUMA_VIOLA_ENGINE_ARCHIVE=$engine
case "$architecture" in
  aarch64) export LUMA_VIOLA_DESKTOP_PROFILE=arm-preview ;;
  x86_64) export LUMA_VIOLA_DESKTOP_PROFILE= ;;
esac
bash "$root/scripts/packages/build-viola-browser.sh"
