#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-haptics.env"

helper=$repo_root/scripts/mobile/test-fp6-haptics.py
pycache=$(mktemp -d)
trap 'rm -rf "$pycache"' EXIT
PYTHONPYCACHEPREFIX=$pycache python3 -m py_compile "$helper"

[ "$FP6_HAPTIC_CONTROLLER" = Awinic-AW86938 ]
[ "$FP6_HAPTIC_INPUT_NAME" = aw86927-haptics ]
[ "$FP6_HAPTIC_I2C_ADDRESS" = 0x5a ]
[ "$FP6_HAPTIC_DRIVER_SHA256" = f1176c6165b2850bc1aad28cd96d4d953f683ef1dd1e38fe61d002daa7e95e92 ]
[ "$FP6_HAPTIC_STOCK_FIRMWARE_REQUIRED" = false ]
[ "$FP6_HAPTIC_STOCK_FIRMWARE_REDISTRIBUTION_ACCEPTED" = false ]
[ "$FP6_HAPTIC_BOUNDED_TEST_READY" = true ]
[ "$FP6_HAPTIC_STANDARD_FF_TRANSPORT_ACCEPTED" = true ]
[ "$FP6_HAPTIC_BOUNDED_PULSE_EXECUTED" = true ]
[ "$FP6_HAPTIC_PHYSICAL_ACCEPTED" = true ]
[ "$FP6_HAPTIC_SUSPEND_ACCEPTED" = false ]
[ "$FP6_HAPTIC_THERMAL_ACCEPTED" = false ]

grep -Fq 'EXPECTED_MODEL = "The Fairphone (Gen. 6)"' "$helper"
grep -Fq 'EXPECTED_INPUT = "aw86927-haptics"' "$helper"
grep -Fq 'MAX_MAGNITUDE = 24576' "$helper"
grep -Fq 'MAX_DURATION_MS = 150' "$helper"
grep -Fq 'requires --acknowledge-physical-actuation' "$helper"
grep -Fq 'write_event(fd, effect_id, 0)' "$helper"
grep -Fq 'fcntl.ioctl(fd, EVIOCRMFF, effect_id)' "$helper"
grep -Fq 'PERSISTENT_MUTATIONS=none' "$helper"
if grep -Eq '(subprocess|os\.system|fastboot|reboot|systemctl|modprobe|insmod)' "$helper"; then
  printf 'FAIL: bounded haptic helper gained out-of-scope system control\n' >&2
  exit 1
fi

printf 'Mobile FP6 bounded haptics source gate: PASS\n'
