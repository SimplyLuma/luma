#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# End-to-end checks against a real guest.
#
# These are skipped, not failed, when no factory image has been provisioned, so
# the suite stays useful on a machine that has not bootstrapped yet. Nothing
# here asserts anything about physical hardware.

set -uo pipefail
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../.." && pwd)
. "$here/lib.sh"

state="${LUMA_EMULATOR_STATE:-$HOME/Library/Application Support/Luma Emulator}"
image="$state/images/fedora44-aarch64/disk.raw"
machine=${LUMA_EMULATOR_TEST_MACHINE:-test-desktop}

printf '== guest lifecycle\n'

if [ ! -f "$image" ]; then
  skip "every guest test" "no factory image at $image; run bootstrap and provision first"
  summary "guest"
  exit 0
fi

cleanup() {
  "$LUMA_EMULATOR_BIN" stop --force >/dev/null 2>&1 || true
  "$LUMA_EMULATOR_BIN" reset --machine "$machine" --yes >/dev/null 2>&1 || true
}
trap cleanup EXIT

"$LUMA_EMULATOR_BIN" stop --force >/dev/null 2>&1 || true
"$LUMA_EMULATOR_BIN" reset --machine "$machine" --yes >/dev/null 2>&1 || true

printf -- '-- first boot\n'
start=$(date +%s)
if "$LUMA_EMULATOR_BIN" start desktop --machine "$machine" --headless --wait 420 >/dev/null 2>&1; then
  pass "first boot reaches ready"
  printf '        first boot to ready: %ds\n' "$(($(date +%s) - start))"
else
  fail "first boot reaches ready" "start did not reach ready"
  summary "guest"
  exit 1
fi

check_json "status reports a ready guest with an address" \
  "document['health'] in ('ready','previewing') and document['guestAddress']" \
  "$LUMA_EMULATOR_BIN" status --json
check_json "status reports the backend and guest architecture" \
  "document['backend']=='apple-virtualization-framework' and document['guestArchitecture']=='aarch64'" \
  "$LUMA_EMULATOR_BIN" status --json
check_json "status reports the image identity it booted" \
  "document.get('imageID')=='fedora44-aarch64'" \
  "$LUMA_EMULATOR_BIN" status --json

check_contains "the guest really is AArch64" "aarch64" \
  "$LUMA_EMULATOR_BIN" exec -- uname -m
check_contains "the guest really is Fedora 44" "Fedora" \
  "$LUMA_EMULATOR_BIN" exec -- cat /etc/fedora-release

printf -- '-- idempotence and conflicts\n'
check "starting an already-running guest is idempotent" 0 \
  "$LUMA_EMULATOR_BIN" start desktop --machine "$machine" --headless
check "starting a second, different guest is refused" 6 \
  "$LUMA_EMULATOR_BIN" start mobile --machine "other-$machine" --headless

printf -- '-- the guest cannot reach the network beyond its own NAT\n'
check_contains "no Android or Waydroid component is present" "absent" \
  "$LUMA_EMULATOR_BIN" exec -- 'command -v waydroid >/dev/null 2>&1 && echo present || echo absent'

printf -- '-- source synchronization is one-way\n'
"$LUMA_EMULATOR_BIN" project connect "$repo" >/dev/null 2>&1
before=$(git -C "$repo" status --porcelain | wc -l | tr -d ' ')
check "sync succeeds" 0 "$LUMA_EMULATOR_BIN" sync
after=$(git -C "$repo" status --porcelain | wc -l | tr -d ' ')
if [ "$before" = "$after" ]; then
  pass "synchronizing did not modify the host checkout"
else
  fail "synchronizing did not modify the host checkout" "$before -> $after changed paths"
fi
check_contains "the real application source landed in the guest" "prairie-notes" \
  "$LUMA_EMULATOR_BIN" exec -- 'ls ~/.local/share/luma-emulator/source/prairie-core/bin'
# The shared AppKit reaches a preview one of two ways: from the connected
# checkout when that branch carries src/luma-platform/appkit, or from the
# installed Luma Developer Platform package. Either satisfies the contract;
# neither being present does not.
if [ -d "$repo/src/luma-platform/appkit/luma_appkit" ]; then
  check_contains "the shared AppKit landed in the guest from source" "widgets.py" \
    "$LUMA_EMULATOR_BIN" exec -- 'ls ~/.local/share/luma-emulator/source/luma-platform/appkit/luma_appkit'
else
  check_contains "the shared AppKit is available from the installed platform package" "luma_appkit" \
    "$LUMA_EMULATOR_BIN" exec -- 'python3 -c "import luma_appkit, os; print(os.path.dirname(luma_appkit.__file__))"'
fi
check_contains "the AppKit that previews import is Luma's own package" "luma-developer-platform" \
  "$LUMA_EMULATOR_BIN" exec -- 'rpm -qf "$(python3 -c "import luma_appkit,os;print(os.path.dirname(luma_appkit.__file__))")" 2>/dev/null | head -1'

check_contains "no build output was written back into the sync tree" "clean" \
  "$LUMA_EMULATOR_BIN" exec -- 'test -z "$(find ~/.local/share/luma-emulator/source -name "*.pyc" -print -quit)" && echo clean || echo dirty'

printf -- '-- previews never replace the packaged system\n'
system_before=$("$LUMA_EMULATOR_BIN" exec -- 'ls -la /usr/bin/prairie-notes 2>/dev/null || echo none')
if "$LUMA_EMULATOR_BIN" preview app notes >/dev/null 2>&1; then
  pass "the notes preview launches from the connected checkout"
else
  skip "the notes preview launches from the connected checkout" "no graphical session yet"
fi
system_after=$("$LUMA_EMULATOR_BIN" exec -- 'ls -la /usr/bin/prairie-notes 2>/dev/null || echo none')
if [ "$system_before" = "$system_after" ]; then
  pass "the packaged system application was not touched by the preview"
else
  fail "the packaged system application was not touched by the preview" "$system_before -> $system_after"
fi
check "stopping previews touches only emulator-created units" 0 \
  "$LUMA_EMULATOR_BIN" preview stop

printf -- '-- scenarios\n'
check_json "the scenario catalogue is reachable from the guest control plane" \
  "document['catalogueCount']>=20 and document['supportedCount']>=10" \
  "$LUMA_EMULATOR_BIN" scenario list --json
check "an unknown scenario is a usage error" 2 "$LUMA_EMULATOR_BIN" scenario not-a-scenario
check "an unsupported scenario refuses rather than faking" 1 \
  "$LUMA_EMULATOR_BIN" scenario low-battery
check "the offline scenario applies through NetworkManager" 0 \
  "$LUMA_EMULATOR_BIN" scenario offline
check "the normal scenario restores the guest" 0 "$LUMA_EMULATOR_BIN" scenario normal

printf -- '-- logs\n'
check_contains "the boot log is retrievable without the interface" "Fedora" \
  "$LUMA_EMULATOR_BIN" logs --component boot --lines 400
check "the guest journal is retrievable" 0 \
  "$LUMA_EMULATOR_BIN" logs --component system --lines 20

printf -- '-- clean shutdown, snapshot and restore\n'
check "the guest shuts down cleanly through ACPI" 0 "$LUMA_EMULATOR_BIN" stop
check "snapshotting a stopped machine succeeds" 0 \
  "$LUMA_EMULATOR_BIN" snapshot create baseline --machine "$machine"
check_json "the snapshot is listed" \
  "'baseline' in document['snapshots']" \
  "$LUMA_EMULATOR_BIN" snapshot list --machine "$machine" --json
check "restoring the snapshot succeeds" 0 \
  "$LUMA_EMULATOR_BIN" snapshot restore baseline --machine "$machine"

printf -- '-- warm boot\n'
start=$(date +%s)
if "$LUMA_EMULATOR_BIN" start desktop --machine "$machine" --headless --wait 420 >/dev/null 2>&1; then
  pass "warm boot reaches ready"
  printf '        warm boot to ready: %ds\n' "$(($(date +%s) - start))"
else
  fail "warm boot reaches ready" "the guest did not come back"
fi

printf -- '-- forced recovery after an unclean stop\n'
check "force stop succeeds" 0 "$LUMA_EMULATOR_BIN" stop --force
check "the guest starts again after an unclean stop" 0 \
  "$LUMA_EMULATOR_BIN" start desktop --machine "$machine" --headless --wait 420
check "stopping twice is idempotent" 0 "$LUMA_EMULATOR_BIN" stop --force
check "stopping an already stopped guest reports not-running" 3 "$LUMA_EMULATOR_BIN" stop

printf -- '-- safe reset\n'
check "resetting this machine succeeds" 0 \
  "$LUMA_EMULATOR_BIN" reset --machine "$machine" --yes
if [ -f "$image" ]; then
  pass "reset left the factory image in place"
else
  fail "reset left the factory image in place" "the factory image was removed"
fi
if [ -d "$repo/src/prairie-core" ]; then
  pass "reset left the connected checkout in place"
else
  fail "reset left the connected checkout in place" "the checkout was damaged"
fi

summary "guest"
