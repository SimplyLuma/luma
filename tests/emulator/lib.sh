#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Shared helpers for the Luma Emulator test suite.

set -uo pipefail

: "${LUMA_EMULATOR_BIN:?LUMA_EMULATOR_BIN must point at the built luma-emulator}"

passes=0
failures=0
skips=0
failed_names=()

pass() { passes=$((passes + 1)); printf '  ok    %s\n' "$1"; }
skip() { skips=$((skips + 1)); printf '  skip  %s (%s)\n' "$1" "$2"; }
fail() {
  failures=$((failures + 1))
  failed_names+=("$1")
  printf '  FAIL  %s\n' "$1"
  [ $# -gt 1 ] && printf '        %s\n' "$2"
  return 0
}

check() {
  local name=$1 expected=$2
  shift 2
  local out status
  out=$("$@" 2>&1)
  status=$?
  if [ "$status" -eq "$expected" ]; then
    pass "$name"
  else
    fail "$name" "expected exit $expected, got $status: $(printf '%s' "$out" | head -3 | tr '\n' ' ')"
  fi
}

check_contains() {
  local name=$1 needle=$2
  shift 2
  local out
  out=$("$@" 2>&1)
  if printf '%s' "$out" | grep -Fq "$needle"; then
    pass "$name"
  else
    fail "$name" "output did not contain '$needle': $(printf '%s' "$out" | head -3 | tr '\n' ' ')"
  fi
}

check_json() {
  local name=$1 expression=$2
  shift 2
  local out
  out=$("$@" 2>&1)
  if printf '%s' "$out" | python3 -c "
import json,sys
try:
    document = json.load(sys.stdin)
except Exception as error:
    print('not JSON: %s' % error); raise SystemExit(1)
raise SystemExit(0 if ($expression) else 2)
" >/dev/null 2>&1; then
    pass "$name"
  else
    fail "$name" "JSON check '$expression' failed on: $(printf '%s' "$out" | head -5 | tr '\n' ' ')"
  fi
}

summary() {
  printf '\n%s: %d passed, %d failed, %d skipped\n' "$1" "$passes" "$failures" "$skips"
  if [ "$failures" -gt 0 ]; then
    printf 'failed: %s\n' "${failed_names[*]}"
    return 1
  fi
  return 0
}

guest_is_ready() {
  [ "$("$LUMA_EMULATOR_BIN" health --json 2>/dev/null |
    python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("health",""))
except Exception: print("")' 2>/dev/null)" = ready ]
}
