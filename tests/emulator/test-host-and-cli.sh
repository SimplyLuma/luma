#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Host detection, the command surface, JSON output, and exit codes.
# Nothing here needs a running guest, so it is safe on any Apple Silicon Mac.

set -uo pipefail
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$here/lib.sh"

printf '== host and command surface\n'

# An isolated state directory so a developer's real machines, images and keys
# are never read or written by the test suite.
export LUMA_EMULATOR_STATE=$(mktemp -d /tmp/luma-emulator-test.XXXXXX)
trap 'rm -rf -- "$LUMA_EMULATOR_STATE"' EXIT

check "version exits zero" 0 "$LUMA_EMULATOR_BIN" version
check_json "version reports the backend and guest architecture" \
  "document['backend']=='apple-virtualization-framework' and document['guestArchitecture']=='aarch64'" \
  "$LUMA_EMULATOR_BIN" version --json

check "doctor exits zero on a supported host" 0 "$LUMA_EMULATOR_BIN" doctor
check_json "doctor detects Apple Silicon" \
  "document['architecture'].startswith('arm64') and document['supported'] is True" \
  "$LUMA_EMULATOR_BIN" doctor --json
check_json "doctor reports the macOS version it observed" \
  "len(document['macOSVersion'].split('.'))>=2" \
  "$LUMA_EMULATOR_BIN" doctor --json
check_json "doctor names every required capability check" \
  "{'host-architecture','macos-version','virtualization-framework'} <= {c['id'] for c in document['checks']}" \
  "$LUMA_EMULATOR_BIN" doctor --json
check_json "doctor explains every check rather than just passing it" \
  "all(c['detail'] for c in document['checks'])" \
  "$LUMA_EMULATOR_BIN" doctor --json

check "an unknown command is a usage error, not a crash" 2 \
  "$LUMA_EMULATOR_BIN" definitely-not-a-command
check "a command needing the runtime reports 'not running'" 3 \
  "$LUMA_EMULATOR_BIN" status
check "health reports not-running rather than guessing" 3 \
  "$LUMA_EMULATOR_BIN" health

check_json "errors are machine-readable in JSON mode" \
  "document['ok'] is False and 'code' in document['error']" \
  "$LUMA_EMULATOR_BIN" status --json

printf '== JSON mode carries no terminal formatting\n'
out=$("$LUMA_EMULATOR_BIN" doctor --json 2>&1)
if printf '%s' "$out" | grep -q $'\033'; then
  fail "no ANSI escapes in JSON output"
else
  pass "no ANSI escapes in JSON output"
fi

printf '== project connection refuses a tree that is not Project Luma\n'
notluma=$(mktemp -d /tmp/not-luma.XXXXXX)
check "connecting a non-Luma directory is refused" 2 \
  "$LUMA_EMULATOR_BIN" project connect "$notluma"
check "connecting a missing directory is refused" 2 \
  "$LUMA_EMULATOR_BIN" project connect "$notluma/nope"
rm -rf -- "$notluma"

repo=$(CDPATH= cd -- "$here/../.." && pwd)
check "connecting the real checkout succeeds" 0 \
  "$LUMA_EMULATOR_BIN" project connect "$repo"
check_json "the connected checkout is recorded read-only" \
  "document['access'].startswith('read-only')" \
  "$LUMA_EMULATOR_BIN" project connect "$repo" --json

printf '== destructive paths are resolved before they are used\n'
check "reset without --yes refuses to act" 2 \
  "$LUMA_EMULATOR_BIN" reset --machine desktop
check "reset of an unknown machine is refused" 2 \
  "$LUMA_EMULATOR_BIN" reset --machine no-such-machine --yes

# A machine name that tries to escape the state directory must be refused by the
# ownership check rather than removing something outside it.
mkdir -p "$LUMA_EMULATOR_STATE/machines"
canary=$(mktemp -d /tmp/luma-canary.XXXXXX)
touch "$canary/precious"
check "reset refuses a traversing machine name" 2 \
  "$LUMA_EMULATOR_BIN" reset --machine "../../../../$(basename "$canary")" --yes
if [ -f "$canary/precious" ]; then
  pass "a traversing reset left files outside the state tree untouched"
else
  fail "a traversing reset left files outside the state tree untouched" "the canary was deleted"
fi
rm -rf -- "$canary"

printf '== Luma package bundle admission\n'
bundle_dir="$HOME/Library/Application Support/Luma Emulator/bundles"
bundle=$(ls -1 "$bundle_dir"/*.tar.gz 2>/dev/null | head -1)

check "inspecting a file that does not exist is a usage error" 2 \
  "$LUMA_EMULATOR_BIN" bundle inspect /nonexistent/bundle.tar.gz

broken="$(mktemp /tmp/luma-broken-bundle.XXXXXX).tar.gz"
printf 'this is not a tar archive at all' > "$broken"
check "a bundle that is not an archive is refused" 1 \
  "$LUMA_EMULATOR_BIN" bundle inspect "$broken"
rm -f "$broken"

if [ -n "$bundle" ]; then
  check "a real bundle is admitted" 0 "$LUMA_EMULATOR_BIN" bundle inspect "$bundle"
  check_json "the bundle reports its architecture and Fedora release" \
    "document['architecture']=='aarch64' and document['fedoraRelease']=='44'" \
    "$LUMA_EMULATOR_BIN" bundle inspect "$bundle" --json
  check_json "the bundle records the source revision it came from" \
    "len(document['sourceCommit'])>=12" \
    "$LUMA_EMULATOR_BIN" bundle inspect "$bundle" --json
  check_json "the bundle states its completeness honestly" \
    "document['completeness'] in ('complete','complete-with-substitutions','incomplete')" \
    "$LUMA_EMULATOR_BIN" bundle inspect "$bundle" --json
  check_json "every omission carries a reason" \
    "all(o['reason'] for o in document['omitted'])" \
    "$LUMA_EMULATOR_BIN" bundle inspect "$bundle" --json
  check_json "an incomplete bundle is never described as complete" \
    "(not document['omitted']) or document['completeness']=='incomplete'" \
    "$LUMA_EMULATOR_BIN" bundle inspect "$bundle" --json
  check_json "the bundle carries the Luma toolkit and applications" \
    "any(p.startswith('gtk4-') for p in document['packages']) and any(p.startswith('prairie-core-apps-') for p in document['packages'])" \
    "$LUMA_EMULATOR_BIN" bundle inspect "$bundle" --json
  check_json "the bundle carries Figtree" \
    "any(p.startswith('google-figtree-fonts-') for p in document['packages'])" \
    "$LUMA_EMULATOR_BIN" bundle inspect "$bundle" --json

  if env -i PATH=/usr/bin:/bin /usr/bin/tar -tf "$bundle" >/dev/null 2>&1; then
    pass "the bundle is readable on a Mac with no Homebrew on PATH"
  else
    fail "the bundle is readable on a Mac with no Homebrew on PATH" "system tar could not read it"
  fi

  check "a bundle whose pinned digest does not match is refused" 1 \
    "$LUMA_EMULATOR_BIN" bundle inspect "$bundle" \
    --luma-bundle-sha256 0000000000000000000000000000000000000000000000000000000000000000
else
  skip "every bundle admission test" "no bundle in $bundle_dir"
fi

printf '== disk reporting is honest about sparse files\n'
check_json "doctor reports free space and what the emulator occupies" \
  "any(c['id']=='host-disk' and 'GiB' in c['detail'] for c in document['checks'])" \
  "$LUMA_EMULATOR_BIN" doctor --json

printf '== scenario catalogue is honest\n'
catalogue="$here/../../config/emulator/scenarios.json"
check_json "every unsupported scenario states its gap and carries no script" \
  "all((s['supported'] and s['script']) or ((not s['supported']) and s['gap'] and not s['script']) for s in document['scenarios'])" \
  cat "$catalogue"
check_json "telephony and power scenarios are not claimed as supported" \
  "all(not s['supported'] for s in document['scenarios'] if s['id'] in {'incoming-call','missed-call','active-call','low-battery','charging'})" \
  cat "$catalogue"

summary "host and command surface"
