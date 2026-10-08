#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
runtime=$repo_root/build/mobile/fp6-physical/fp6-fingerprint-runtime-accepted-v1
backend=$repo_root/scripts/mobile/luma-fp6-fingerprint-backend
stop=$repo_root/scripts/mobile/luma-fp6-fingerprint-backend-stop
graphical_gate=$repo_root/scripts/mobile/luma-fp6-wait-graphical-session
agent=$repo_root/scripts/mobile/luma-fp6-fingerprint-lock-agent.sh
auth=$repo_root/scripts/mobile/run-fp6-stock-biometric-service-smoke-v1-remote.sh
restore=$repo_root/scripts/mobile/restore-fp6-stock-biometric-mounts-remote.sh
composer=$repo_root/scripts/mobile/prepare-fp6-p5-candidate.sh
units=$repo_root/config/mobile/fp6-physical/overlay/usr/lib/systemd/system
agent_dropin=$repo_root/config/mobile/fp6-physical/overlay/etc/systemd/system/luma-fp6-fingerprint-lock-agent.service.d/50-luma-graceful-stop.conf

(
  cd "$runtime"
  sha256sum -c manifest.sha256 >/dev/null
)
[ "$(sha256sum "$runtime/qcomtee-freezer-v144.ko" | cut -d' ' -f1)" = \
  2368279b3ebaeb5485d88676e95d88b23655f39eb548382d4076d030b45da591 ]
[ "$(sha256sum "$runtime/luma-qcomtee-listener-supplicant" | cut -d' ' -f1)" = \
  e871f3e7e13e5030945baed60038cb029badd6d69afb5bc6e342c5c343338389 ]
[ -x "$runtime/luma-qcomtee-listener-supplicant" ]
[ -x "$runtime/fp6-focal-driver-stage" ]
[ -x "$runtime/luma-fp6-wake-input" ]

bash -n "$backend" "$stop" "$graphical_gate" "$agent" "$auth" "$restore" "$composer"
grep -Fq 'wait_event_freezable(luma_listener_request_wait' \
  "$repo_root/src/fp6-fingerprint-qcomtee/qcomtee-listener-extension.inc"
grep -Fq 'sha256sum -c manifest.sha256' "$backend"
grep -Fq 'androidboot.slot_suffix=_b' "$backend"
grep -Fq 'expected_boot_sha=c6acad3a30d944d59648258eef383c33a3b51ac1148a98ef24256ee4dee61dd8' "$backend"
grep -Fq 'critical_fault' "$backend"
! grep -Eq 'kill[[:space:]]+-(KILL|9)' "$backend" "$stop" "$agent"
grep -Fq 'kill -HUP -- "-$child"' "$agent"
grep -Fq "trap 'exit 0' TERM" "$agent"
grep -Fq "trap 'exit 129' HUP" "$auth"

grep -Fq 'LUMA_FP6_RUNTIME_ROOT:-/usr/lib/luma/fp6-fingerprint' "$agent"
grep -Fq 'LUMA_STOCK_ADAPTER_DIR' "$agent"
grep -Fq 'LUMA_STOCK_ADAPTER_DIR:-/tmp/qcomtee-smoke-src/adapter' "$auth"
grep -Fq 'LUMA_STOCK_MANAGER_ACCESS_SHIM' "$auth"
grep -Fq 'state=waiting reason=lock_state_unavailable' "$agent"
grep -Fq 'if ! locked; then' "$agent"

grep -Fq 'SendSIGKILL=no' "$units/luma-fp6-fingerprint-backend.service"
grep -Fq 'Requires=luma-fp6-fingerprint-graphical-gate.service' \
  "$units/luma-fp6-fingerprint-backend.service"
grep -Fq 'ExecStart=/usr/libexec/luma-fp6-wait-graphical-session' \
  "$units/luma-fp6-fingerprint-graphical-gate.service"
grep -Fq 'pgrep -u 1000 -x phoc' "$graphical_gate"
grep -Fq 'SendSIGKILL=no' "$units/luma-fp6-fingerprint-lock-agent.service"
grep -Fq 'TimeoutStopFailureMode=terminate' \
  "$units/luma-fp6-fingerprint-lock-agent.service"
grep -Fq 'TimeoutStopFailureMode=terminate' "$agent_dropin"
grep -Fq 'luma-fp6-fingerprint-stock-runtime.service' \
  "$units/luma-fp6-fingerprint-lock-agent.service"
grep -Fq 'ExecStart=/usr/libexec/luma-fp6-fingerprint-restore-mounts' \
  "$units/luma-fp6-fingerprint-stock-runtime.service"
grep -Fq 'mount -o loop,ro,nosuid,nodev' "$restore"
grep -Fq 'ConditionKernelCommandLine=androidboot.slot_suffix=_b' \
  "$units/luma-fp6-fingerprint-backend.service"
[ ! -e "$units/multi-user.target.wants/luma-fp6-fingerprint-backend.service" ]
[ ! -e "$units/multi-user.target.wants/luma-fp6-fingerprint-lock-agent.service" ]

grep -Fq 'fingerprint-runtime-accepted-v1' "$composer"
grep -Fq 'sha256sum -c manifest.sha256' "$composer"
grep -Fq 'units are intentionally shipped' "$composer"

printf 'Mobile FP6 persistent fingerprint runtime gate: PASS\n'
