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
  x86_64) engine_release=30; engine_sha=2760bcadbb3864ffb637888b06e9717642c1add31656c2b81c6d0fb7fd5b1028 ;;
  aarch64) engine_release=31; engine_sha=325f3ca8be682bbe6c267251a29315f01229a52971b4627c972dbe0a32552f30 ;;
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
rpms=()
for identity in "$gtk" "$adwaita" "$platform" \
  "luma-apk-metadata-0.1.0-1.luma.1~preview.20260910.1.fc44.$architecture" \
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
installer=luma-application-installer-0.1.0-1.luma.71.creator20261021.1.fc44.noarch.rpm
installer_sha=c26a7e6b68859f9624abf2d73cc62ba1845bff25447e14eafd376c8db2543ad4
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
