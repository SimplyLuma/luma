#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
#
# Host side of the AirPlay end-to-end run: two rootful podman containers on the
# default bridge network, one under test (run-headless.sh airplay) and one with
# the receivers (airplay-receivers.sh). This script relays scenario steps that
# change the receiver side.
#
#   run-airplay-e2e.sh <container-under-test> <receiver-container>
#
# Both containers must already have their packages and this directory at
# /src/tests/luma-audio-devices/integration.
set -u
it=$1
rx=$2
here=/src/tests/luma-audio-devices/integration
podman exec "$rx" bash "$here/airplay-receivers.sh" start
log=$(mktemp)
podman exec "$it" bash -c "rm -rf /tmp/airplay-steps; $here/run-headless.sh airplay" >"$log" 2>&1 &
runner=$!
handled=""
while kill -0 "$runner" 2>/dev/null; do
  for step in $(grep -o '^STEP [a-z-]*' "$log" | cut -d' ' -f2); do
    count=$(grep -c "^STEP $step\$" "$log")
    marker="$step:$count"
    case " $handled " in *" $marker "*) continue ;; esac
    handled="$handled $marker"
    case "$step" in
      audio-bytes) result=$(podman exec "$rx" bash "$here/airplay-receivers.sh" audio-bytes) ;;
      stop-open-receiver) podman exec "$rx" bash "$here/airplay-receivers.sh" stop-open; result=ok ;;
      start-open-receiver) podman exec "$rx" bash "$here/airplay-receivers.sh" start-open; result=ok ;;
      *) result=unknown ;;
    esac
    podman exec "$it" bash -c "mkdir -p /tmp/airplay-steps && echo '$result' > /tmp/airplay-steps/$step.done"
  done
  sleep 0.5
done
wait "$runner"
status=$?
grep -v '^STEP ' "$log"
rm -f "$log"
exit $status
