#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Gate check for luma-vitals-memory-guard, run as root inside a gate VM:
#
#   gate-memory-guard.sh USER [DEADLINE_SECONDS]
#
# USER owns a running graphical session (the no-account stage's first-boot
# setup session). A synthetic runaway is started in that user's app.slice and
# allocates incompressible memory until something ends it. The check passes
# only when systemd-oomd's swap policy (ManagedOOMSwap=kill on app.slice) ends
# the runaway and GNOME Shell (same process), the display manager and every
# system service survive it. A kernel OOM kill does not count: it would mean
# the memory guard did not act first.
#
# Runs only in disposable gate VMs. Never run this on a real machine.

set -uo pipefail
user=${1:?usage: gate-memory-guard.sh USER [DEADLINE_SECONDS]}
deadline_s=${2:-600}
unit=luma-gate-runaway

id -u "$user" >/dev/null 2>&1 || { echo "FAIL no such user: $user"; exit 1; }
ucmd() { systemctl --user -M "$user@" "$@"; }

shell_before=$(pgrep -u "$user" -x gnome-shell | head -n 1)
[ -n "$shell_before" ] || { echo "FAIL no gnome-shell running for $user"; exit 1; }
echo "gnome-shell pid before: $shell_before"

[ "$(systemctl is-active systemd-oomd)" = active ] || { echo "FAIL systemd-oomd is not active"; exit 1; }
policy=$(ucmd show -p ManagedOOMSwap --value app.slice)
echo "app.slice ManagedOOMSwap: $policy"
[ "$policy" = kill ] || { echo "FAIL app.slice has no ManagedOOMSwap=kill"; exit 1; }
echo "== memory and swap before"
free -m
swapon --show

# A leak that compresses the way application heaps do (a quarter random
# pages, the rest empty), at a rate kswapd and zram keep up with, so swap fills
# the way a real leak fills it. Wholly incompressible data made the check a
# race: zram then holds every swapped page at full size, RAM ran out while
# swap was still far below oomd's limit, and in 20260916.8 the kernel's OOM
# killer ended the runaway first (in .5 and .7 oomd did, by a second or two).
cat >/var/tmp/luma-gate-runaway.py <<'PY'
import os, time
hog = []
while True:
    chunk = bytearray(32 << 20)
    chunk[: 8 << 20] = os.urandom(8 << 20)
    hog.append(chunk)
    time.sleep(0.25)
PY
chmod 0644 /var/tmp/luma-gate-runaway.py

since=$(date '+%Y-%m-%d %H:%M:%S')
ucmd reset-failed "$unit" >/dev/null 2>&1 || true
systemd-run --user -M "$user@" --unit="$unit" --slice=app.slice \
  /usr/bin/python3 /var/tmp/luma-gate-runaway.py ||
  { echo "FAIL could not start the runaway"; exit 1; }

end=$((SECONDS + deadline_s))
state=active
while [ "$SECONDS" -lt "$end" ]; do
  state=$(ucmd is-active "$unit" 2>/dev/null)
  case "$state" in active|activating) sleep 3 ;; *) break ;; esac
done
if [ "$state" = active ] || [ "$state" = activating ]; then
  echo "FAIL nothing ended the runaway within ${deadline_s}s"
  ucmd stop "$unit" >/dev/null 2>&1 || true
  exit 1
fi
elapsed=$((deadline_s - (end - SECONDS)))
result=$(ucmd show -p Result --value "$unit" 2>/dev/null)
oomd=$(journalctl --since "$since" -q --no-pager -u systemd-oomd | grep -F "$unit" | tail -n 1)
kernel=$(journalctl --since "$since" -q -k --no-pager | grep -E 'Out of memory|oom-kill' | tail -n 1)
echo "runaway ended after ${elapsed}s, result: ${result:-unknown}"
echo "systemd-oomd: ${oomd:-none}"
echo "kernel OOM killer: ${kernel:-none}"
ucmd reset-failed "$unit" >/dev/null 2>&1 || true
rm -f /var/tmp/luma-gate-runaway.py
sleep 5

ok=1
[ -n "$oomd" ] || { echo "FAIL systemd-oomd did not end the runaway"; ok=0; }
shell_after=$(pgrep -u "$user" -x gnome-shell | head -n 1)
echo "gnome-shell pid after: ${shell_after:-none}"
[ "$shell_after" = "$shell_before" ] || { echo "FAIL GNOME Shell did not survive (before $shell_before, after ${shell_after:-none})"; ok=0; }
LUMA_GREENBOOT_FORCE_TRIAL=1 /usr/lib/greenboot/check/required.d/20-luma-shell-responsive.sh ||
  { echo "FAIL GNOME Shell stopped answering"; ok=0; }
LUMA_GREENBOOT_FORCE_TRIAL=1 /usr/lib/greenboot/check/required.d/10-luma-display-manager.sh ||
  { echo "FAIL the display manager session did not survive"; ok=0; }
failed=$(systemctl list-units --state=failed --no-legend --plain)
[ -z "$failed" ] || { echo "FAIL failed system units: $(tr '\n' ' ' <<<"$failed")"; ok=0; }
echo "== memory and swap after"
free -m

[ "$ok" -eq 1 ] || exit 1
echo "PASS systemd-oomd ended the runaway after ${elapsed}s; GNOME Shell (pid $shell_before), the display manager and all system services survived"
