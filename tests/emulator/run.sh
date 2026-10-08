#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Run the whole Luma Emulator test suite.
#
#   tests/emulator/run.sh              everything that does not need a guest
#   tests/emulator/run.sh --with-guest also the end-to-end guest tests

set -uo pipefail
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../.." && pwd)

export LUMA_EMULATOR_BIN=${LUMA_EMULATOR_BIN:-$repo/build/emulator/luma-emulator}
if [ ! -x "$LUMA_EMULATOR_BIN" ]; then
  printf 'error: %s is not built. Run scripts/emulator/build.sh first.\n' "$LUMA_EMULATOR_BIN" >&2
  exit 1
fi
export LUMA_EMULATOR_SCENARIOS=${LUMA_EMULATOR_SCENARIOS:-$repo/config/emulator/scenarios.json}

status=0

printf '### Swift unit tests\n'
if swift test --package-path "$repo/tools/luma-emulator" 2>&1 | tail -20; then
  printf '  swift test passed\n'
else
  printf '  swift test FAILED\n'
  status=1
fi

for suite in test-host-and-cli.sh test-repository-hygiene.sh; do
  printf '\n### %s\n' "$suite"
  "$here/$suite" || status=1
done

if [ "${1:-}" = "--with-guest" ]; then
  printf '\n### test-guest.sh\n'
  "$here/test-guest.sh" || status=1
else
  printf '\n### test-guest.sh skipped (pass --with-guest to run it)\n'
fi

printf '\n=== suite exit status: %d\n' "$status"
exit "$status"
