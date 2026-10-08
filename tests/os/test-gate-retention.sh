#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Dry-run harness for scripts/os/prune-gate-vms.sh: fake gate VM sets, a stub
# virsh and a real flock, no libvirt and no real disks.
#
#   tests/os/test-gate-retention.sh [RETENTION-SCRIPT]
#
# Each case names what must be removed and what must survive, and fails on
# either. It was watched going red against the rule it replaces (sets kept
# until they were over two days old) before the new script existed.

set -euo pipefail

here=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
script=${1:-$here/scripts/os/prune-gate-vms.sh}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failures=0 cases=0
fail() { printf 'FAIL %s: %s\n' "$tc" "$*"; failures=$((failures + 1)); }

# The stub reads "domain state" lines from $work/domains.
mkdir -p "$work/bin"
cat >"$work/bin/virsh" <<'STUB'
#!/usr/bin/env bash
db=${STUB_DOMAINS:?}
case "$1" in
  list) cut -d' ' -f1 "$db" ;;
  domstate) sed -n "s/^$2 //p" "$db" ;;
  *) echo "stub virsh: unexpected $*" >&2; exit 1 ;;
esac
STUB
chmod +x "$work/bin/virsh"

setup() {
  tc=$1
  cases=$((cases + 1))
  root="$work/$tc/fs"
  keys="$work/$tc/ssh"
  mkdir -p "$root/vm/media" "$root/locks" "$keys"
  : >"$root/locks/gate.lock"
  : >"$work/$tc/domains"
}
gate_set() { # gate_set BUILD RUN
  mkdir -p "$root/vm/gate-$1-$2"
  head -c 4096 /dev/zero >"$root/vm/gate-$1-$2/luma-os-gate-fresh-$2.qcow2"
  mkdir -p "$keys/luma-os-gate-$2"
}
domain() { printf '%s %s\n' "$1" "$2" >>"$work/$tc/domains"; }
run() {
  LUMA_OS_ROOT=$root LUMA_OS_GATE_KEY_ROOT=$keys LUMA_OS_VIRSH="$work/bin/virsh" \
    STUB_DOMAINS="$work/$tc/domains" bash "$script" "$@" >"$work/$tc/out" 2>&1 ||
    fail "exit $? $(tail -n 3 "$work/$tc/out")"
}
gone() { [ ! -e "$root/vm/$1" ] || fail "$1 was kept"; }
kept() { [ -d "$root/vm/$1" ] || fail "$1 was removed"; }
logged() { grep -Fq "$1" "$work/$tc/out" || fail "log does not say: $1"; }

# 1. Three failed nightlies, all under a day old: only the newest stays, its
#    key stays, the others' keys go, and each removal is logged. The build ids
#    sort wrongly as text (.10 < .9), so the run id must decide.
setup three-failures
gate_set 20260921.9 20260921T050000Z
gate_set 20260921.10 20260921T180000Z
gate_set 20260922.1 20260922T050000Z
run --apply
kept gate-20260922.1-20260922T050000Z
gone gate-20260921.10-20260921T180000Z
gone gate-20260921.9-20260921T050000Z
[ -d "$keys/luma-os-gate-20260922T050000Z" ] || fail 'kept set lost its key'
[ ! -e "$keys/luma-os-gate-20260921T050000Z" ] || fail 'removed set kept its key'
logged "removed"
logged "gate-20260921.9-20260921T050000Z"

# 2. A gate is running (its domains are up): it is never touched and does not
#    count as the kept set, so the newest finished failure stays too.
setup running-domain
gate_set 20260920.1 20260920T050000Z
gate_set 20260921.1 20260921T050000Z
gate_set 20260922.1 20260922T050000Z
domain luma-os-gate-fresh-20260922T050000Z running
domain luma-os-gate-update-20260922T050000Z paused
domain luma-os-gate-fresh-20260921T050000Z 'shut off'
run --apply
kept gate-20260922.1-20260922T050000Z
kept gate-20260921.1-20260921T050000Z
gone gate-20260920.1-20260920T050000Z
logged "kept (running: "

# 3. A gate is setting up: lock held, no domain yet. The newest set is its.
setup lock-held
gate_set 20260921.1 20260921T050000Z
gate_set 20260922.1 20260922T050000Z
exec 9>"$root/locks/gate.lock"
flock -n 9
run --apply
exec 9>&-
kept gate-20260922.1-20260922T050000Z
kept gate-20260921.1-20260921T050000Z
logged "gate lock held"

# 4. The dry run (the default) removes nothing and says what it would.
setup dry-run
gate_set 20260921.1 20260921T050000Z
gate_set 20260922.1 20260922T050000Z
run
kept gate-20260921.1-20260921T050000Z
logged "would remove"

# 5. Anything not shaped like a gate set is left alone, including a symlink to
#    one and the installer media.
setup not-a-set
gate_set 20260922.1 20260922T050000Z
mkdir -p "$root/vm/gate-notes" "$work/$tc/elsewhere"
ln -s "$work/$tc/elsewhere" "$root/vm/gate-20260101.1-20260101T000000Z"
run --apply
kept gate-notes
kept media
[ -L "$root/vm/gate-20260101.1-20260101T000000Z" ] || fail 'symlinked set was removed'
[ -d "$work/$tc/elsewhere" ] || fail 'symlink target was removed'

# 6. Nothing to do is not an error.
setup empty
run --apply
logged "0 set(s)"

[ "$cases" -eq 6 ] || fail "ran $cases cases, expected 6"
if [ "$failures" -ne 0 ]; then
  printf 'gate retention: %d failure(s) in %d cases\n' "$failures" "$cases"
  exit 1
fi
printf 'gate retention: %d cases passed\n' "$cases"
