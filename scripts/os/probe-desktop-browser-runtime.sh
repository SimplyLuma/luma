#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# A native-CPU mapped-window probe, not signed OS or Flatpak qualification.
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
  gdb pipewire-gstreamer mesa-dri-drivers mesa-libEGL python3-pillow dbus-daemon gnome-shell gjs
host=/usr/share/viola-browser/luma-host
sha256sum /opt/viola/browser/chrome > "$out/installed-engine.sha256"
rpm -V viola-browser-stable > "$out/rpm-verify-before.txt"
cp /usr/share/viola-browser/provenance.json "$out/installed-provenance.json"
cmp "$root/src/external/viola/chromium-linux/luma-package/desktop-arm-preview-frame-profile.json" \
  /usr/share/viola-browser/native-engine-profile.json
# The Fedora package uses the real journal socket for dbus-broker-launch.
# A minimal CI container has no service manager, so supply its ordinary journal
# daemon locally rather than changing the browser or suppressing broker errors.
/usr/bin/systemd-machine-id-setup
/usr/lib/systemd/systemd-journald > "$out/journald.log" 2>&1 &
journal=$!
trap 'kill "$journal" 2>/dev/null || true; wait "$journal" 2>/dev/null || true' EXIT
for _ in $(seq 50); do
  [ -S /run/systemd/journal/socket ] && break
  kill -0 "$journal"
  sleep .1
done
[ -S /run/systemd/journal/socket ]
# GNOME's login/time-limit clients require an actual system bus even in a
# headless container. Keep this daemon local to the disposable CI container.
install -d -m 0755 /run/dbus
dbus-daemon --system --nofork --nopidfile > "$out/system-bus.log" 2>&1 &
system_bus=$!
trap 'kill "$system_bus" "$journal" 2>/dev/null || true; wait "$system_bus" "$journal" 2>/dev/null || true' EXIT
for _ in $(seq 50); do
  [ -S /run/dbus/system_bus_socket ] && break
  kill -0 "$system_bus"
  sleep .1
done
[ -S /run/dbus/system_bus_socket ]
# Fedora's login1 activation delegates to a service manager, which is absent
# here. Start the ordinary daemon directly and require its real bus owner.
/usr/lib/systemd/systemd-logind > "$out/logind.log" 2>&1 &
logind=$!
trap 'kill "$logind" "$system_bus" "$journal" 2>/dev/null || true; wait "$logind" "$system_bus" "$journal" 2>/dev/null || true' EXIT
for _ in $(seq 50); do
  busctl --system --no-pager status org.freedesktop.login1 > "$out/logind-bus.txt" 2>&1 && break
  kill -0 "$logind"
  sleep .1
done
busctl --system --no-pager status org.freedesktop.login1 > "$out/logind-bus.txt"
useradd --create-home --uid 1100 luma-gate-browser-opens
install -d -o luma-gate-browser-opens -g luma-gate-browser-opens -m 0700 /run/user/1100
qa=$(mktemp -d /tmp/luma-gate-browser-opens.XXXXXX)
cp "$root/scripts/os/qa/viola-native/"* "$qa/"
mkdir "$qa/out"
chown -R luma-gate-browser-opens:luma-gate-browser-opens "$out" "$qa"
status=0
# Diagnostic attaches only to this fresh fixture's exact host and compositor.
python3 "$qa/gdb-owned-host.py" "$qa/out" "$out" & debugger=$!
runuser -u luma-gate-browser-opens -- env HOME=/home/luma-gate-browser-opens XDG_RUNTIME_DIR=/run/user/1100 \
  OUT="$out" QA="$qa" PYTHONDONTWRITEBYTECODE=1 LIBGL_ALWAYS_SOFTWARE=1 GSK_RENDERER=gl \
  XDG_CURRENT_DESKTOP=GNOME XDG_SESSION_DESKTOP=gnome XDG_SESSION_TYPE=wayland \
  timeout --kill-after=15 420 bash -euo pipefail -c '
    trap '\''jobs -pr | xargs -r kill; wait || true'\'' EXIT
    export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1100/bus
    dbus-daemon --session --address="$DBUS_SESSION_BUS_ADDRESS" --nofork --nopidfile >"$OUT/session-bus.log" 2>&1 &
    for _ in $(seq 50); do [ -S /run/user/1100/bus ] && break; sleep .1; done
    [ -S /run/user/1100/bus ]
    pipewire >"$OUT/pipewire.log" 2>&1 & audio=$!
    wireplumber >"$OUT/wireplumber.log" 2>&1 & policy=$!
    python3 "$QA/page-server.py" "$QA/out" --html-sha256 cceef935f0735daaa75004d613262781db0883b33db51e7c11a223e64c168504 >"$OUT/page-server.log" 2>&1 &
    for _ in $(seq 50); do [ -s "$QA/out/page-server.json" ] && break; sleep .1; done
    export BROWSER_GATE_URL=$(python3 -c '\''import json,sys;print(json.load(open(sys.argv[1]))["url"])'\'' "$QA/out/page-server.json")
    export BROWSER_GATE_OUT="$QA/out" BROWSER_GATE_NATIVE_OBSERVER="$QA/read-native-state.py"
    export BROWSER_GATE_PIXEL_VALIDATOR="$QA/verify-pixels.py" BROWSER_GATE_INPUT_HELPER="$QA/input-owned-session.py"
    export WAYLAND_DISPLAY=viola-qa-shm-r1
    (
      sleep 35
      ps -u "$(id -u)" -o pid,ppid,stat,wchan:30,comm,args >"$OUT/startup-processes.txt"
      busctl --user --no-pager list >"$OUT/startup-bus.txt" 2>&1 || true
    ) &
    GSETTINGS_BACKEND=memory gnome-shell --headless --no-x11 --virtual-monitor=1600x1000 \
      --wayland-display=viola-qa-shm-r1 --automation-script="$QA/browser-opens.js" >"$OUT/shell.log" 2>&1
    python3 -c '\''import json,sys;d=json.load(open(sys.argv[1]));assert d["ok"] is True,d'\'' "$QA/out/RESULT.json"
  ' || status=$?
wait "$debugger" || true
mkdir "$out/mapped"
cp -a "$qa/out/." "$out/mapped/"
# Retain the fresh fixture profile log and exact diagnostics, never a user profile.
home=/home/luma-gate-browser-opens
if [ -f "$home/.local/share/viola-luma/profile.log" ]; then
  cp "$home/.local/share/viola-luma/profile.log" "$out/engine.log"
fi
if [ -d "$home/.local/state/viola-luma/sessions" ]; then
  # Avoid carrying a .local ancestor: the artifact uploader excludes hidden
  # paths by default. Copy only the three fresh fixture diagnostic families.
  python3 - "$home/.local/state/viola-luma/sessions" "$out/runtime-reports" <<'PY'
import shutil,sys
from pathlib import Path
source,destination=map(Path,sys.argv[1:])
for name in ('presentation-live.json','integration-report.json','runtime-provenance.json'):
    for path in source.rglob(name):
        assert path.is_file() and not path.is_symlink()
        target=destination/path.relative_to(source)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
PY
fi
rpm -V viola-browser-stable > "$out/rpm-verify-after.txt"
exit "$status"
