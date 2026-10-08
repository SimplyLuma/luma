#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Measure the Luma Emulator on this Mac and print a table.
#
# Every number this prints is observed here, now. Nothing is estimated, and a
# step that does not run prints "not measured" rather than a plausible figure.

set -uo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
emulator=${LUMA_EMULATOR_BIN:-$repo_root/build/emulator/luma-emulator}
machine=${LUMA_EMULATOR_MEASURE_MACHINE:-measure}
app=${LUMA_EMULATOR_MEASURE_APP:-notes}
state="${LUMA_EMULATOR_STATE:-$HOME/Library/Application Support/Luma Emulator}"

test -x "$emulator" || { printf 'error: build the emulator first\n' >&2; exit 1; }

results=()
record() { results+=("$1|$2"); printf '  %-44s %s\n' "$1" "$2"; }

seconds_since() {
  python3 -c "import sys;print('%.2fs' % (float(sys.argv[2])-float(sys.argv[1])))" "$1" "$2"
}
now() { python3 -c 'import time;print(time.time())'; }

printf '== host\n'
chip=$(sysctl -n machdep.cpu.brand_string)
memory=$(python3 -c "print(int($(sysctl -n hw.memsize)/1073741824))")
record "Mac" "$chip, ${memory} GiB, macOS $(sw_vers -productVersion)"

printf '\n== storage\n'
if [ -f "$state/cache/fedora44-base.raw" ]; then
  record "verified base image (apparent)" "$(ls -lh "$state/cache/fedora44-base.raw" | awk '{print $5}')"
  record "verified base image (on disk)" "$(du -h "$state/cache/fedora44-base.raw" | awk '{print $1}')"
fi
if [ -f "$state/images/fedora44-aarch64/disk.raw" ]; then
  record "factory image (on disk)" "$(du -h "$state/images/fedora44-aarch64/disk.raw" | awk '{print $1}')"
fi
record "total emulator state" "$(du -sh "$state" 2>/dev/null | awk '{print $1}')"

printf '\n== overlay creation and cold start\n'
"$emulator" stop --force >/dev/null 2>&1
"$emulator" reset --machine "$machine" --yes >/dev/null 2>&1

start=$(now)
"$emulator" start desktop --machine "$machine" --headless --wait 420 >/dev/null 2>&1
status=$?
finish=$(now)
if [ "$status" -eq 0 ]; then
  record "cold start (new overlay) to ready" "$(seconds_since "$start" "$finish")"
else
  record "cold start (new overlay) to ready" "FAILED"
  printf 'the guest did not start; remaining measurements are skipped\n' >&2
  exit 1
fi

printf '\n== control plane\n'
start=$(now); "$emulator" status --json >/dev/null 2>&1; finish=$(now)
record "status --json round trip" "$(seconds_since "$start" "$finish")"
start=$(now); "$emulator" exec -- true >/dev/null 2>&1; finish=$(now)
record "exec round trip (multiplexed ssh)" "$(seconds_since "$start" "$finish")"

printf '\n== source synchronization\n'
"$emulator" project connect "$repo_root" >/dev/null 2>&1
start=$(now); "$emulator" sync >/dev/null 2>&1; finish=$(now)
record "first sync of the connected checkout" "$(seconds_since "$start" "$finish")"
start=$(now); "$emulator" sync >/dev/null 2>&1; finish=$(now)
record "no-change sync" "$(seconds_since "$start" "$finish")"

# A single-character CSS change is the ordinary case the fast lane exists for.
# Whichever stylesheet this branch actually carries.
css=""
for candidate in \
  "$repo_root/src/luma-platform/appkit/luma-appkit.css" \
  "$repo_root/src/prairie-core/style/prairie.css"
do
  [ -w "$candidate" ] && { css=$candidate; break; }
done
if [ -n "$css" ]; then
  cp "$css" "$css.luma-measure-backup"
  printf '\n/* luma-emulator measurement */\n' >> "$css"
  start=$(now); "$emulator" sync >/dev/null 2>&1; finish=$(now)
  record "sync after one CSS edit" "$(seconds_since "$start" "$finish")"
  mv "$css.luma-measure-backup" "$css"
  "$emulator" sync >/dev/null 2>&1
else
  record "sync after one CSS edit" "not measured (no writable stylesheet in this checkout)"
fi

printf '\n== warm restart\n'
"$emulator" stop >/dev/null 2>&1
start=$(now); "$emulator" start desktop --machine "$machine" --headless --wait 420 >/dev/null 2>&1; finish=$(now)
record "warm start to ready" "$(seconds_since "$start" "$finish")"

printf '\n== idle cost\n'
pid=$(pgrep -f "LumaEmulatorHost --machine $machine" | head -1)
if [ -n "$pid" ]; then
  sleep 10
  read -r cpu rss <<<"$(ps -o %cpu=,rss= -p "$pid" | tr -s ' ')"
  record "runtime idle CPU" "${cpu}%"
  record "runtime resident memory" "$(python3 -c "print('%.0f MiB' % (${rss}/1024))")"
else
  record "runtime idle CPU" "not measured"
fi

printf '\n== preview lane\n'
start=$(now); "$emulator" preview app "$app" >/dev/null 2>&1; status=$?; finish=$(now)
if [ "$status" -eq 0 ]; then
  record "first $app preview launch" "$(seconds_since "$start" "$finish")"
  start=$(now); "$emulator" preview app "$app" >/dev/null 2>&1; finish=$(now)
  record "$app preview restart (warm)" "$(seconds_since "$start" "$finish")"
else
  record "first $app preview launch" "not measured (no graphical session)"
fi

printf '\n== screenshot\n'
shot=$(mktemp /tmp/luma-measure.XXXXXX.png)
start=$(now); "$emulator" screenshot --output "$shot" >/dev/null 2>&1; status=$?; finish=$(now)
if [ "$status" -eq 0 ]; then
  record "framebuffer screenshot" "$(seconds_since "$start" "$finish") ($(du -h "$shot" | awk '{print $1}'))"
else
  record "framebuffer screenshot" "not measured"
fi
rm -f "$shot"

printf '\n== snapshot\n'
"$emulator" stop --force >/dev/null 2>&1
start=$(now); "$emulator" snapshot create measure --machine "$machine" >/dev/null 2>&1; finish=$(now)
record "snapshot create (APFS clone)" "$(seconds_since "$start" "$finish")"
start=$(now); "$emulator" snapshot restore measure --machine "$machine" >/dev/null 2>&1; finish=$(now)
record "snapshot restore" "$(seconds_since "$start" "$finish")"

"$emulator" reset --machine "$machine" --yes >/dev/null 2>&1

printf '\n== summary table\n\n'
printf '| Measurement | Result |\n|---|---|\n'
for row in "${results[@]}"; do
  printf '| %s | %s |\n' "${row%%|*}" "${row#*|}"
done
