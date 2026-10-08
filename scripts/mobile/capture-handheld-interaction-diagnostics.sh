#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

usage() {
  printf 'usage: %s OUTPUT_DIRECTORY [SECONDS]\n' "$0" >&2
  exit 2
}

[ "$#" -ge 1 ] && [ "$#" -le 2 ] || usage
output_dir=$1
duration=${2:-30}

case "$duration" in
  ''|*[!0-9]*) usage ;;
esac
[ "$duration" -ge 5 ] && [ "$duration" -le 300 ] || {
  printf 'error: duration must be between 5 and 300 seconds\n' >&2
  exit 2
}

for tool in awk busctl date free gsettings jq journalctl mkdir pgrep ps rpm sleep systemctl uptime; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required diagnostic tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

mkdir -p "$output_dir"
chmod 0700 "$output_dir"
started=$(date --iso-8601=seconds)

# This capture is deliberately content-free. Never record key codes, touch
# coordinates, application text, screenshots, clipboard data, or accessibility
# object contents. System-level scheduling and journal timing is sufficient to
# distinguish Shell, compositor, GPU, kernel, and memory-pressure stalls.
{
  printf 'started=%s\n' "$started"
  printf 'duration_seconds=%s\n' "$duration"
  printf 'shell='; rpm -q gnome-shell
  printf 'mutter='; rpm -q mutter
  printf 'screen_keyboard='; \
    gsettings get org.gnome.desktop.a11y.applications screen-keyboard-enabled
  printf 'display_scale='; \
    display_state=$(busctl --user call org.gnome.Mutter.DisplayConfig \
      /org/gnome/Mutter/DisplayConfig org.gnome.Mutter.DisplayConfig \
      GetCurrentState 2>/dev/null || true)
  grep -oE -m1 '[0-9]+\.[0-9]+ 0 true' <<<"$display_state" || true
  uptime
  free -h
} >"$output_dir/baseline.txt"

samples=$((duration * 5))
ps -eo pid,ppid,stat,ni,pcpu,pmem,maj_flt,min_flt,comm,args \
  --sort=-pcpu >"$output_dir/processes-before.txt"

i=0
while [ "$i" -lt "$samples" ]; do
  {
    printf '%s ' "$(date +%s.%N)"
    awk '{printf "load=%s,%s,%s runnable=%s ", $1, $2, $3, $4}' /proc/loadavg
    awk '/MemAvailable:|SwapFree:|Dirty:|Writeback:/ {printf "%s=%s%s ", $1, $2, $3}' /proc/meminfo
    for name in gnome-shell systemd-journald; do
      pid=$(pgrep -xo "$name" 2>/dev/null || true)
      if [ -n "$pid" ] && [ -r "/proc/$pid/stat" ]; then
        awk -v name="$name" '{printf "%s_cpu=%s,%s %s_faults=%s,%s ", name, $14, $15, name, $10, $12}' \
          "/proc/$pid/stat"
      fi
    done
    for pressure_file in /proc/pressure/cpu /proc/pressure/io /proc/pressure/memory; do
      if [ -r "$pressure_file" ]; then
        pressure_name=${pressure_file##*/}
        awk -v name="$pressure_name" \
          '{split($2, avg, "="); printf "%s_%s_avg10=%s ", name, $1, avg[2]}' \
          "$pressure_file"
      fi
    done
    printf '\n'
  } >>"$output_dir/samples.txt"
  sleep 0.2
  i=$((i + 1))
done

ps -eo pid,ppid,stat,ni,pcpu,pmem,maj_flt,min_flt,comm,args \
  --sort=-pcpu >"$output_dir/processes-after.txt"
systemctl --failed --no-legend >"$output_dir/system-failures.txt"
systemctl --user --failed --no-legend >"$output_dir/user-failures.txt"
# The diagnostic greetd lane runs Shell directly, so unit filters alone miss
# its process journal. Match only infrastructure process names and redact the
# message payload down to severity/category markers; never persist app text.
# Shell is a direct greetd child on the physical diagnostic lane, so its
# records are in the system journal rather than a systemd --user unit journal.
# The luma account has journal read access on the candidate.
journalctl --since "$started" --no-pager -o json \
  | jq -r 'select(._COMM == "gnome-shell" or .SYSLOG_IDENTIFIER == "gnome-shell") |
      [."__REALTIME_TIMESTAMP", (.PRIORITY // ""),
       ((.MESSAGE // "") |
        if test("GPU|DRM|IOMMU|hang|stall|timeout|error|warning"; "i")
        then (capture("(?<category>GPU|DRM|IOMMU|hang|stall|timeout|error|warning)"; "i").category)
        else "event" end)] | @tsv' \
  >"$output_dir/shell-journal-markers.txt"
journalctl -k --since "$started" --no-pager \
  >"$output_dir/kernel-journal.txt"

printf 'Handheld interaction diagnostics captured without input content: %s\n' \
  "$output_dir"
