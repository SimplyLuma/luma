#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# A native-CPU rendering/adapter probe, not signed OS or Flatpak qualification.
set -euo pipefail
trap 'printf "Runtime probe failed at line %s: %s\n" "$LINENO" "$BASH_COMMAND" >&2' ERR
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = aarch64 ] && [ -f /.dockerenv ]
inputs="$root/build/runtime-inputs"
out="$root/build/browser-runtime-evidence"
mkdir -p "$out"
python3 - "$inputs" "$out" <<'PY'
import hashlib,json,sys
from pathlib import Path
inputs,out=map(Path,sys.argv[1:])
expected='bdaf62fd08989b790decfac941a6947f8f72273a'
revisions=list((inputs/'browser').rglob('SOURCE-REVISION'))
assert len(revisions)==1 and revisions[0].read_text().strip()==expected
receipts=list(inputs.rglob('SHA256SUMS'));assert len(receipts)>=2
verified=[]
for receipt in receipts:
    for row in receipt.read_text().splitlines():
        digest,name=row.split(None,1);path=receipt.parent/name
        assert not path.is_symlink() and path.resolve().is_relative_to(receipt.parent.resolve())
        actual=hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
        assert actual==digest,(str(path),actual,digest)
        verified.append(dict(path=str(path.relative_to(inputs)),sha256=actual))
(out/'inputs.json').write_text(json.dumps(dict(scope='NATIVE_CPU_PACKAGE_PROBE_NOT_RELEASE_QUALIFICATION',source=expected,verified=verified),indent=2)+'\n')
PY
. "$root/config/desktop/inputs.env"
rpms=()
for identity in "${GTK4_NEVRA%.*}.aarch64" "${LIBADWAITA_NEVRA%.*}.aarch64" \
  "${LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA%.*}.aarch64" \
  'luma-apk-metadata-0.1.0-1.luma.1~preview.20260910.1.fc44.aarch64' \
  'luma-appimage-compat-0.1.0-1.luma.1.fc44.aarch64' \
  'viola-browser-stable-151.0.7922.72-15.viola8210.native38.creator20261025.1.fc44.aarch64'; do
  mapfile -t found < <(find "$inputs" -type f -name "$identity.rpm")
  printf 'Selecting %s: %s retained copies\n' "$identity" "${#found[@]}"
  [ "${#found[@]}" -ge 1 ]
  expected_sha=
  for file in "${found[@]}"; do
    observed=$(rpm -qp --nosignature --qf '%{NEVRA}' "$file")
    printf 'Observed RPM identity: %s (%s)\n' "$observed" "$file"
    [ "$observed" = "$identity" ]
    actual_sha=$(sha256sum "$file" | cut -d ' ' -f1)
    [ -z "$expected_sha" ] || [ "$actual_sha" = "$expected_sha" ]
    expected_sha=$actual_sha
  done
  printf '%s  %s\n' "$expected_sha" "${found[0]}" >> "$out/selected-rpms.sha256"
  rpms+=("${found[0]}")
done
installer=luma-application-installer-0.1.0-1.luma.71.creator20261021.1.fc44.noarch.rpm
installer_sha=c26a7e6b68859f9624abf2d73cc62ba1845bff25447e14eafd376c8db2543ad4
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
  "https://media.simplyluma.com/inputs/rpm/$installer_sha/$installer" --output "$inputs/$installer"
printf '%s  %s\n' "$installer_sha" "$inputs/$installer" | sha256sum -c -
dnf5 -y install "${rpms[@]}" "$inputs/$installer" \
  pipewire wireplumber gstreamer1-plugins-base gstreamer1-plugins-good \
  pipewire-gstreamer mesa-dri-drivers mesa-libEGL python3-pillow dbus-daemon
host=/usr/share/viola-browser/luma-host
sha256sum /opt/viola/browser/chrome > "$out/installed-engine.sha256"
rpm -V viola-browser-stable > "$out/rpm-verify-before.txt"
cp /usr/share/viola-browser/provenance.json "$out/installed-provenance.json"
cmp "$root/src/external/viola/chromium-linux/luma-package/desktop-arm-preview-frame-profile.json" \
  /usr/share/viola-browser/native-engine-profile.json
useradd --create-home --uid 1100 viola-runtime
install -d -o viola-runtime -g viola-runtime -m 0700 /run/user/1100
chown -R viola-runtime:viola-runtime "$out"
runuser -u viola-runtime -- env HOME=/home/viola-runtime XDG_RUNTIME_DIR=/run/user/1100 \
  OUT="$out" HOST="$host" PYTHONDONTWRITEBYTECODE=1 LIBGL_ALWAYS_SOFTWARE=1 \
  timeout --kill-after=15 240 dbus-run-session -- bash -euo pipefail -c '
    pipewire >"$OUT/pipewire.log" 2>&1 & audio=$!
    wireplumber >"$OUT/wireplumber.log" 2>&1 & policy=$!
    trap '\''kill "$policy" "$audio" 2>/dev/null || true; wait "$policy" "$audio" 2>/dev/null || true'\'' EXIT
    python3 "$HOST/headless_qa.py" --engine /opt/viola/browser/chrome --work-root "$OUT/visual" >"$OUT/visual.log" 2>&1
    python3 "$HOST/headless_qa.py" --engine /opt/viola/browser/chrome --work-root "$OUT/adapter" --qualify-input >"$OUT/adapter.log" 2>&1
  '
rpm -V viola-browser-stable > "$out/rpm-verify-after.txt"
python3 - "$out" <<'PY'
import json,sys
from pathlib import Path
from PIL import Image
out=Path(sys.argv[1]);reports=[]
for case in ('visual','adapter'):
    paths=list((out/case).rglob('integration-report.json'));assert len(paths)==1
    report=json.loads(paths[0].read_text())
    assert not report['errors'] and report['engine_exit_code']==0
    assert report['imported_frames']>0 and report['pending_leases']==0
    reports.append(dict(case=case,path=str(paths[0].relative_to(out)),report=report))
images=list((out/'visual').rglob('native-monitor.png'));assert len(images)==1
image=Image.open(images[0]);image.load();assert image.width>=800 and image.height>=600
assert len(image.convert('RGB').getcolors(image.width*image.height) or [])>100
(out/'RESULT.json').write_text(json.dumps(dict(result='NATIVE_FRAMES_AND_ADAPTER_PROBE_PASSED_VISUAL_REVIEW_REQUIRED',scope='NOT_PAGE_PIXEL_COMPOSITOR_INPUT_FLATPAK_OR_OS_RELEASE_ACCEPTANCE',reports=reports,screenshot=str(images[0].relative_to(out))),indent=2)+'\n')
PY
