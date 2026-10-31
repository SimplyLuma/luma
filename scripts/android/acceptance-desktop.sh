#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Disposable graphical test account only. This removes/reinstalls the source
# fixture's synthetic data. It never changes the user manager's display setup.
set -euo pipefail

apk=${1:?usage: acceptance-desktop.sh ACCEPTANCE.apk}
package=org.projectluma.androidtest
isolation_failed=0
luma_android=(luma-android)
if [[ -n ${LUMA_ANDROID_ACCEPTANCE_PYTHONPATH:-} ]]; then
  export PYTHONPATH=$LUMA_ANDROID_ACCEPTANCE_PYTHONPATH
  luma_android=(python3 -m luma_android.cli)
fi

test -r "$apk"
sudo -n true

test -n "${WAYLAND_DISPLAY:-}"
test -S "${XDG_RUNTIME_DIR:?}/$WAYLAND_DISPLAY"
test -n "${DBUS_SESSION_BUS_ADDRESS:-}"

if ! "${luma_android[@]}" list | grep -Fq "packageName: $package"; then
  "${luma_android[@]}" install --confirmed "$apk"
fi
systemctl --user start luma-android.service
for _attempt in $(seq 1 180); do
  status=$("${luma_android[@]}" status)
  grep -q '"session": "RUNNING"' <<<"$status" && break
  sleep 0.5
done
grep -q '"session": "RUNNING"' <<<"${status:-}"

printf 'PASS  broker and fixture runtime report running\n'
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
  printf 'UNQUALIFIED  Android internal SELinux is %s\n' "$android_selinux" >&2
  isolation_failed=1
fi
test "$(getenforce)" = Enforcing
init_pid=$(sudo lxc-info -P /var/lib/waydroid/lxc -n waydroid -pH)
[[ $init_pid =~ ^[1-9][0-9]*$ ]]
if [[ $(sudo readlink "/proc/$init_pid/ns/user") == $(readlink /proc/self/ns/user) ]]; then
  printf 'UNQUALIFIED  Android shares the host user namespace\n' >&2
  isolation_failed=1
else
  uid_map=$(sudo cat "/proc/$init_pid/uid_map")
  if python3 -c '
import os, sys
ranges = [tuple(map(int, line.split())) for line in sys.stdin if line.strip()]
def host(uid):
    return next((outside + uid - inside for inside, outside, size in ranges
                 if inside <= uid < inside + size), None)
values = [host(uid) for uid in (0, 1000, 10000, 10129, 99000)]
sys.exit(0 if all(value is not None and value not in (0, 1000, os.getuid())
                  for value in values) else 1)
' <<<"$uid_map"; then
    printf 'PASS  observed separate namespace and non-colliding sampled IDs; full mapping audit still required\n'
  else
    printf 'UNQUALIFIED  Android UID mapping is missing or collides with sampled host identities\n' >&2
    isolation_failed=1
  fi
fi
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
printf 'PASS  observed host labels and Android app UID; inspected base config has no home mount\n'

connectivity=$(sudo waydroid shell -- dumpsys connectivity)
grep -q 'Active default network: [0-9]' <<<"$connectivity"
grep -q 'Transports: ETHERNET' <<<"$connectivity"
grep -q 'Capabilities: .*INTERNET.*VALIDATED' <<<"$connectivity"
sudo waydroid shell -- ping -c 1 -W 3 192.168.240.1 >/dev/null
printf 'PASS  Android has a validated application network and reachable gateway\n'

printf 'PENDING  notification delivery/actions require fixture and native renderer receipts\n'

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
printf 'PASS  private installation receipt\n'

if (( isolation_failed )); then
  printf 'UNQUALIFIED  functional observations do not pass the runtime isolation gate\n' >&2
  exit 1
fi
printf 'FUNCTIONAL CHECKS COMPLETE  resize, notifications, hardware, ABI and power need separate receipts\n'
