#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -Eeuo pipefail
umask 077

stage=/tmp/luma-qcomtee-stage-rpmb-lazy-v1
candidate=/tmp/luma-qcomtee-listener-supplicant.trace-v2
old_sha=f09c6ebbb0cb1bcfab0be867ac71c9361dafedbfc864fe5a2dbcfda10ec80131
new_sha=166938d0520ebf968cdbddae4c74f65d359d3a290fc8e0be57ea78d7ec37d6cc

fail() {
  printf 'event=fp6_listener_trace_replace_failed reason=%s\n' "$1" >&2
  exit 1
}

[[ $(id -u) -eq 0 ]] || fail not_root
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(uname -r) == 7.1.2-luma-fp-cma1 ]] || fail kernel
[[ $(sha256sum "$candidate" | cut -d' ' -f1) == "$new_sha" ]] || fail candidate_hash
[[ $(sha256sum "$stage/luma-qcomtee-listener-supplicant" | cut -d' ' -f1) == "$old_sha" ]] || fail current_hash

pid=$(cat "$stage/listener.pid")
[[ $pid =~ ^[0-9]+$ && -d /proc/$pid ]] || fail current_pid
[[ $(sha256sum "/proc/$pid/exe" | cut -d' ' -f1) == "$old_sha" ]] || fail current_process_hash
[[ $(awk '/^Uid:/ {print $2}' "/proc/$pid/status") == 0 ]] || fail current_uid

kill -TERM "$pid"
for _ in $(seq 1 30); do
  [[ ! -d /proc/$pid ]] && break
  sleep 0.1
done
[[ ! -d /proc/$pid ]] || fail sigterm_timeout

install -m 0755 "$stage/luma-qcomtee-listener-supplicant" \
  "$stage/luma-qcomtee-listener-supplicant.previous"
install -m 0755 "$candidate" "$stage/luma-qcomtee-listener-supplicant"
: >>"$stage/listener.log"
nohup "$stage/luma-qcomtee-listener-supplicant" \
  --state-dir /var/lib/luma/fingerprint/qsee-state \
  >>"$stage/listener.log" 2>&1 </dev/null &
new_pid=$!
printf '%s\n' "$new_pid" >"$stage/listener.pid"

for _ in $(seq 1 50); do
  [[ -d /proc/$new_pid ]] || fail replacement_exited
  if tail -20 "$stage/listener.log" | \
    grep -Fq 'event=listener_registered transport=qseecomcompat id=28672 '; then
    break
  fi
  sleep 0.1
done
[[ -d /proc/$new_pid ]] || fail replacement_exited
[[ $(sha256sum "/proc/$new_pid/exe" | cut -d' ' -f1) == "$new_sha" ]] || fail replacement_process_hash
listener_epoch=$(awk '
  /event=transport_start transport=qseecomcompat/ { registrations = ""; next }
  /event=listener_registered transport=qseecomcompat/ {
    registrations = registrations $0 "\n"
  }
  END { printf "%s", registrations }
' "$stage/listener.log")
[[ $(grep -c 'event=listener_registered transport=qseecomcompat' <<<"$listener_epoch") -eq 3 ]] || fail listener_count
for listener_id in 10 8192 28672; do
  grep -Eq "event=listener_registered transport=qseecomcompat id=$listener_id([[:space:]]|$)" \
    <<<"$listener_epoch" || fail "listener_${listener_id}"
done

printf 'event=fp6_listener_trace_replace_complete old_pid=%s new_pid=%s listeners=10,8192,28672 paths_logged=0 payloads_logged=0\n' \
  "$pid" "$new_pid"
