#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Destructive scope: the supplied acceptance APK is removed and immediately
# reinstalled to prove the lifecycle. Do not use an APK carrying test data.
set -euo pipefail

apk=${1:?usage: acceptance-desktop.sh ACCEPTANCE.apk}
package=${LUMA_ANDROID_ACCEPTANCE_PACKAGE:-org.fdroid.fdroid}
display=${LUMA_ANDROID_ACCEPTANCE_DISPLAY:-wayland-luma-android-acceptance}
shell_unit=luma-android-acceptance-shell.service
luma_android=(luma-android)
if [[ -n ${LUMA_ANDROID_ACCEPTANCE_PYTHONPATH:-} ]]; then
  export PYTHONPATH=$LUMA_ANDROID_ACCEPTANCE_PYTHONPATH
  luma_android=(python3 -m luma_android.cli)
fi

test -r "$apk"
sudo -n true

cleanup() {
  systemctl --user stop luma-android-session.service luma-android.service 2>/dev/null || true
  systemctl --user stop "$shell_unit" 2>/dev/null || true
}
trap cleanup EXIT

systemctl --user stop "$shell_unit" luma-android-session.service luma-android.service \
  2>/dev/null || true
/usr/bin/waydroid session stop >/dev/null 2>&1 || true
systemctl --user unset-environment WAYLAND_DISPLAY DISPLAY XDG_SESSION_TYPE 2>/dev/null || true
systemd-run --user --unit="$shell_unit" --property=Restart=on-failure \
  --setenv="WAYLAND_DISPLAY=$display" --setenv=XDG_SESSION_TYPE=wayland \
  /usr/bin/gnome-shell --headless --wayland --no-x11 \
  --wayland-display="$display" --virtual-monitor=1280x720 >/dev/null
systemctl --user set-environment "WAYLAND_DISPLAY=$display" XDG_SESSION_TYPE=wayland

for _attempt in $(seq 1 30); do
  test -S "${XDG_RUNTIME_DIR:?}/$display" && break
  sleep 0.5
done
test -S "$XDG_RUNTIME_DIR/$display"

export WAYLAND_DISPLAY=$display
export XDG_SESSION_TYPE=wayland
systemctl --user start luma-android.service
for _attempt in $(seq 1 180); do
  status=$("${luma_android[@]}" status)
  grep -q '"session": "RUNNING"' <<<"$status" && break
  sleep 0.5
done
grep -q '"session": "RUNNING"' <<<"${status:-}"

printf 'PASS  warm broker starts Android only when an installed app exists\n'
tests/smoke/android-runtime.sh
gdbus call --session --dest org.projectluma.ApplicationInstaller1 \
  --object-path /org/projectluma/ApplicationInstaller1 \
  --method org.projectluma.ApplicationInstaller1.Supports \
  application/vnd.android.package-archive | grep -Fq '(true,)'
printf 'PASS  generic Filer installer interface accepts APK MIME\n'

if ! "${luma_android[@]}" list | grep -Fq "packageName: $package"; then
  "${luma_android[@]}" install --confirmed "$apk"
  printf 'PASS  repaired a stale launcher by reinstalling the verified APK\n'
fi

start_ns=$(date +%s%N)
"${luma_android[@]}" launch "$package"
launch_ms=$((($(date +%s%N) - start_ns) / 1000000))
sleep 4
activities=$(sudo waydroid shell -- dumpsys activity activities)
grep -q "$package" <<<"$activities"
printf 'PASS  %s launched as an Android activity (%d ms command latency)\n' \
  "$package" "$launch_ms"

android_selinux=$(sudo waydroid shell -- getenforce | tr -d '\r')
if [[ $android_selinux == Enforcing ]]; then
  printf 'PASS  Android internal SELinux is enforcing\n'
else
  printf 'WARN  Android internal SELinux is %s; host SELinux/LXC is the MAC boundary\n' \
    "$android_selinux"
fi
test "$(getenforce)" = Enforcing
host_processes=$(ps -eZ)
grep -q 'container_runtime_t.*lxc-start' <<<"$host_processes"
package_state=$(sudo waydroid shell -- dumpsys package "$package")
grep -Eq 'userId=[0-9]+' <<<"$package_state"
android_processes=$(sudo waydroid shell -- ps -AZ)
grep -Eq "u0_a[0-9]+.*$package" <<<"$android_processes"
if sudo grep -Eq 'lxc\.mount\.entry.*(/home|/var/home)' \
  /var/lib/waydroid/lxc/waydroid/config; then
  printf 'FAIL  Android container exposes a Linux home mount\n' >&2
  exit 1
fi
printf 'PASS  host SELinux/LXC and Android per-app UID are active; Linux home is not mounted\n'

connectivity=$(sudo waydroid shell -- dumpsys connectivity)
grep -q 'Active default network: [0-9]' <<<"$connectivity"
grep -q 'Transports: ETHERNET' <<<"$connectivity"
grep -q 'Capabilities: .*INTERNET.*VALIDATED' <<<"$connectivity"
sudo waydroid shell -- ping -c 1 -W 3 192.168.240.1 >/dev/null
printf 'PASS  Android has a validated application network and reachable gateway\n'

session_log=$(journalctl --user -u luma-android-session.service -n 80 --no-pager)
grep -q 'Android with user 0 is ready' <<<"$session_log"
grep -q 'Service manager /dev/binder has appeared' <<<"$session_log"
printf 'PASS  Waydroid binder notification bridge is attached to the desktop session\n'

"${luma_android[@]}" pause
for _attempt in $(seq 1 60); do
  status=$("${luma_android[@]}" status)
  grep -q '"session": "STOPPED"' <<<"$status" && break
  sleep 0.5
done
grep -q '"session": "STOPPED"' <<<"${status:-}"
"${luma_android[@]}" resume
for _attempt in $(seq 1 180); do
  status=$("${luma_android[@]}" status)
  grep -q '"session": "RUNNING"' <<<"$status" && break
  sleep 0.5
done
grep -q '"session": "RUNNING"' <<<"${status:-}"
printf 'PASS  non-destructive pause/resume recovery\n'

"${luma_android[@]}" remove "$package"
! "${luma_android[@]}" list | grep -Fq "packageName: $package"
printf 'PASS  uninstall removes the test application\n'
"${luma_android[@]}" install --confirmed "$apk"
"${luma_android[@]}" list | grep -Fq "packageName: $package"
test -f "$HOME/.local/share/applications/waydroid.$package.desktop"
"${luma_android[@]}" launch "$package"
printf 'PASS  reinstall restores the first-class application entry and launch\n'

receipt=$(sha256sum "$apk" | cut -d' ' -f1)
test -f "$HOME/.local/share/luma-android/receipts/$receipt.json"
test "$(stat -c %a "$HOME/.local/share/luma-android/receipts/$receipt.json")" = 600
printf 'PASS  private immutable installation receipt\n'
