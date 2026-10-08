#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

timeout_seconds=${1:-60}
case "$timeout_seconds" in
  ''|*[!0-9]*) printf 'error: timeout must be an integer\n' >&2; exit 2 ;;
esac
[ "$timeout_seconds" -ge 5 ] && [ "$timeout_seconds" -le 120 ] || {
  printf 'error: timeout must be between 5 and 120 seconds\n' >&2
  exit 2
}

expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2
expected_i2c_hash=df905d9ad4240b708a11c029af98344fa7d7ea23284ef0eb21f1655c6e67746a
module=/lib/modules/7.1.2/updates/luma-fp6-nfc/modules/s3fwrn5_i2c.ko

[ "$(id -u)" -eq 0 ] || {
  printf 'error: run as root on the FP6\n' >&2
  exit 1
}
[ "$(tr -d '\0' </proc/device-tree/model)" = "$expected_model" ] || {
  printf 'error: device identity differs\n' >&2
  exit 1
}
[ "$(uname -r)" = "$expected_kernel" ] || {
  printf 'error: kernel ABI differs\n' >&2
  exit 1
}
[ -e /sys/class/nfc/nfc0 ] || {
  printf 'error: nfc0 is absent\n' >&2
  exit 1
}
[ "$(sha256sum "$module" | cut -d ' ' -f 1)" = "$expected_i2c_hash" ] || {
  printf 'error: NFC transport module hash differs\n' >&2
  exit 1
}
for loaded in nfc nci s3fwrn5 s3fwrn5_i2c; do
  grep -q "^${loaded} " /proc/modules || {
    printf 'error: required module is not loaded: %s\n' "$loaded" >&2
    exit 1
  }
done
command -v nfctool >/dev/null || {
  printf 'error: nfctool is absent\n' >&2
  exit 1
}
if rfkill list nfc 2>/dev/null | grep -Eq 'Soft blocked: yes|Hard blocked: yes'; then
  printf 'error: NFC is blocked\n' >&2
  exit 1
fi

cleanup() {
  nfctool -d nfc0 -0 >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

printf 'LUMA_FP6_NFC_READER_ACCEPTANCE_VERSION=1\n'
printf 'BOOT_ID=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
printf 'POLL_MODE=initiator-read-only\n'
printf 'IDENTIFIERS_EXPOSED=false\n'
printf 'PAYLOADS_READ=false\n'
printf 'WRITES_ATTEMPTED=false\n'
printf 'POLL_WINDOW_SECONDS=%s\n' "$timeout_seconds"

nfctool -d nfc0 -1 >/dev/null
set +e
timeout --signal=INT --kill-after=2 "$timeout_seconds" \
  stdbuf -oL -eL nfctool -d nfc0 -p Initiator 2>&1 |
  awk '
    BEGIN { found = 0 }
    /^Start polling/ { print; next }
    /^Targets found for nfc[0-9]+/ { print; found = 1; next }
    /^Targets found:/ { print; if ($0 ~ /[1-9][0-9]*/) found = 1; next }
    /^Tags found:/ { print; if ($NF + 0 > 0) found = 1; next }
    /^Devices found:/ { print; if ($NF + 0 > 0) found = 1; next }
    /NFC Type|Protocol/ { print; next }
    /^Error|^error/ { print; next }
    END {
      printf "TARGET_DETECTED=%s\n", found ? "true" : "false"
      exit found ? 0 : 3
    }
  '
poll_status=${PIPESTATUS[1]}
set -e

cleanup
trap - EXIT INT TERM
printf 'FINAL_POWER_STATE=%s\n' "$(nfctool -l | awk '/Powered:/ { print $2; exit }')"
printf 'FAILED_UNITS=%s\n' "$(systemctl --failed --no-legend --plain | wc -l | tr -d ' ')"

if [ "$poll_status" -ne 0 ]; then
  printf 'error: no passive NFC target was detected in the bounded window\n' >&2
  exit 3
fi
printf 'PHYSICAL_READER_ACCEPTED=true\n'
