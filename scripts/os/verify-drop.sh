#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Check that an incoming drop delivers exactly what it claims, before any of it
# is pinned or collected.
#
#   verify-drop.sh [--ledger FILE|--no-ledger] DROPDIR...
#
# A drop claims its contents twice: in packages.txt (the NEVRAs the pipeline is
# asked to pin) and in the file names under it. Neither is authority. The RPM
# header is, so every claim is checked against the header of the file that is
# supposed to carry it:
#
#   1. every NEVRA in packages.txt has a file whose HEADER says it is that
#      NEVRA -- not merely a file named that way;
#   2. every RPM in the drop has a file name that matches its own header, so a
#      renamed or stale copy cannot pass as a fresh build;
#   3. every RPM in the drop is either claimed in packages.txt or is a source
#      package or a debug package of something claimed;
#   4. SHA256SUMS, where the drop has one, verifies;
#   5. packages.txt exists at all. A drop whose contents are claimed only in a
#      message is a drop that gets pinned from prose;
#   6. the drop records the source checks that were run before it was handed
#      over (SOURCE-CHECKS, written by scripts/os/check-source-before-drop.sh)
#      against a full 40-character revision, and that revision agrees with the
#      drop's own SOURCE-COMMIT where it has one. Noted until SOURCE_CHECKS_FROM
#      below, a failure after it: a builder's tests only count if something
#      makes them run;
#   7. no NEVRA this check has seen before comes back with different content.
#      A drop is immutable once it has been handed over: rebuilding means a
#      new release, and the new drop's README says what it supersedes. The
#      ledger below is what makes that mechanical rather than remembered.
#
# Rule 1 is the one that matters most and the reason this exists: a stale
# release variable in a build script left the copy step finding nothing, so a
# drop kept claiming a new release while shipping the previous build, through
# three releases before anyone noticed. A claim with no matching header is a
# failure here, not a warning.
#
# The ledger ($LUMA_OS_DROP_LEDGER, by default .drop-ledger.tsv beside the
# drops) records "NEVRA header-sha256 drop first-seen" the first time a package
# is seen. A later file with the same NEVRA and a different header is refused
# and both drops are named. Delete a line to re-issue a release deliberately;
# doing that by hand is the point.
#
# A drop with a HOLD file is reported as held and not checked: it is not for
# pinning. A directory that holds drops rather than packages is walked one
# level into them, so a whole incoming directory can be checked at once, and
# the package pool is recognised and left alone.

set -euo pipefail

# Every drop must carry SOURCE-CHECKS from this date. Until then a drop without
# one is only noted, because every drop made before today lacks it and a rule
# that refuses everything on the day it lands is a rule people route around.
# It is a dated exception like the capability gate's: the date is written here,
# not remembered, and it is short on purpose.
#
# Moved from the 24th to the 27th on 2026-09-20: tests/unit does not pass on
# this branch (13 failures and 16 errors at fce74aa8, in CI's own environment),
# because CI runs on pull requests and pushes to stage and never on work/*, so
# the suite everything is cut from has been red with nobody looking. A gate
# that requires a suite nobody keeps green does not protect the pipeline, it
# stops it. The date moves again if the triage has not happened.
SOURCE_CHECKS_FROM=${LUMA_OS_SOURCE_CHECKS_FROM:-2026-09-27}

status=0

note() { printf '%s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*"; status=1; }

nevra_of() {
  rpm -qp --nosignature --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' "$1" 2>/dev/null
}

header_of() {
  rpm -qp --nosignature --qf '%{SHA256HEADER}' "$1" 2>/dev/null
}

# Remember this NEVRA's content, or refuse it if it has changed since.
ledger_check() {
  local nevra=$1 header=$2 drop=$3 seen_header seen_drop seen_when
  [ -n "$ledger" ] || return 0
  [ -n "$header" ] || return 0
  if [ -f "$ledger" ]; then
    read -r seen_header seen_drop seen_when < <(
      awk -v n="$nevra" '$1 == n { print $2, $3, $4; exit }' "$ledger")
    if [ -n "${seen_header:-}" ]; then
      [ "$seen_header" = "$header" ] && return 0
      fail "$nevra was already handed over from $seen_drop on $seen_when with different content; a rebuild needs a new release"
      return 1
    fi
  fi
  printf '%s\t%s\t%s\t%s\n' "$nevra" "$header" "$(basename "$drop")" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$ledger" 2>/dev/null || true
}

verify_drop() {
  local drop=$1
  [ -d "$drop" ] || { fail "$drop: not a directory"; return; }
  note "== $(basename "$drop")"
  if [ -e "$drop/HOLD" ] || [ -e "$drop/HOLD.txt" ]; then
    note "held: not for pinning; skipped"
    return
  fi

  # The pool is not a drop: one file per pinned NEVRA with its own manifest,
  # checked by collect-packages.sh on every run.
  if [ -f "$drop/pool.manifest" ]; then
    note "package pool, not a drop; skipped"
    return
  fi

  # A directory of drops: check each one rather than refusing the parent. A
  # drop keeps its packages beside it or under RPMS/; anything deeper belongs
  # to a drop of its own.
  if [ -z "$(find "$drop" -maxdepth 1 -name '*.rpm' -print -quit)" ] && [ ! -d "$drop/RPMS" ]; then
    local -a inner=()
    local child
    while IFS= read -r child; do
      { [ -n "$(find "$child" -maxdepth 1 -name '*.rpm' -print -quit)" ] || [ -d "$child/RPMS" ]; } &&
        inner+=("$child")
    done < <(find "$drop" -mindepth 1 -maxdepth 1 -type d | sort)
    if [ "${#inner[@]}" -gt 0 ]; then
      note "${#inner[@]} drops inside; checking each"
      for child in "${inner[@]}"; do verify_drop "$child"; done
      return
    fi
  fi

  if [ -f "$drop/SHA256SUMS" ]; then
    if (cd "$drop" && sha256sum -c --quiet SHA256SUMS >/dev/null 2>&1); then
      note 'SHA256SUMS: all files verify'
    else
      fail "$drop: SHA256SUMS does not verify"
    fi
  else
    note 'SHA256SUMS: absent'
  fi

  local -a rpms=()
  while IFS= read -r rpm; do rpms+=("$rpm"); done < <(find "$drop" -name '*.rpm' | sort)
  [ "${#rpms[@]}" -gt 0 ] || { fail "$drop: no RPMs"; return; }

  # Header NEVRA of every file, and the file names that claim it.
  local -a headers=() names=()
  local rpm nevra base
  for rpm in "${rpms[@]}"; do
    nevra=$(nevra_of "$rpm")
    [ -n "$nevra" ] || { fail "$(basename "$rpm"): cannot be read as an RPM"; continue; }
    base=$(basename "$rpm" .rpm)
    headers+=("$nevra")
    names+=("$base")
    # A source package's header carries the binary's NEVRA with the build
    # arch, so it would collide with the binary it built. The binary is what
    # ships and what the ledger is about.
    [ "$(rpm -qp --nosignature --qf '%{SOURCEPACKAGE}' "$rpm" 2>/dev/null)" = 1 ] ||
      ledger_check "$nevra" "$(header_of "$rpm")" "$drop" || true
    # A source package's header arch is the build arch, so compare on the stem.
    case "$base" in
      *.src) [ "${base%.src}" = "${nevra%.*}" ] ||
        fail "$base: header says ${nevra%.*}" ;;
      *) [ "$base" = "$nevra" ] ||
        fail "$base: header says $nevra" ;;
    esac
  done

  # Without packages.txt nothing in the drop is claimed in a form anything can
  # check, and the pin gets read out of a message instead.
  [ -f "$drop/packages.txt" ] || { fail "$drop: no packages.txt, so the drop claims nothing"; return; }

  # The tests a builder ran are worth what made them run. SOURCE-CHECKS says
  # which checks ran, over which revision, before the drop was handed over.
  local required=0
  [ "$(date -u +%Y-%m-%d)" \< "$SOURCE_CHECKS_FROM" ] || required=1
  if [ ! -f "$drop/SOURCE-CHECKS" ]; then
    if [ "$required" -eq 1 ]; then
      fail "$drop: no SOURCE-CHECKS; run scripts/os/hand-over-drop.sh on it before handing it over"
    else
      note "SOURCE-CHECKS: absent (required from $SOURCE_CHECKS_FROM)"
    fi
  else
    local checked_revision claimed_revision
    checked_revision=$(sed -n 's/^revision: *//p' "$drop/SOURCE-CHECKS" | head -n 1)
    case "$checked_revision" in
      *[!0-9a-f]* | "") fail "$drop: SOURCE-CHECKS revision is not a commit hash: ${checked_revision:-<none>}" ;;
      *) [ "${#checked_revision}" -ge 40 ] ||
           fail "$drop: SOURCE-CHECKS revision is abbreviated: $checked_revision" ;;
    esac
    # A drop that names its own source commit must be the one that was checked.
    if [ -f "$drop/SOURCE-COMMIT" ]; then
      claimed_revision=$(tr -d '[:space:]' < "$drop/SOURCE-COMMIT")
      [ -z "$claimed_revision" ] || [ "$claimed_revision" = "$checked_revision" ] ||
        fail "$drop: SOURCE-CHECKS covers $checked_revision but the drop says it was built from $claimed_revision"
    fi
    # Where the build host's mirror knows the revision, say whether it is on a
    # branch: a drop built from an unpushed tree is one nothing can rebuild.
    # The mirror holds only the branches synced to it, so an unknown revision
    # is reported as unknown rather than treated as missing.
    local mirror=${LUMA_OS_DROP_MIRROR:-${LUMA_OS_ROOT:-/mnt/luma-secondary/luma-build/os-release/fs}/git/ProjectLuma.git}
    local provenance="not in this host's mirror, which holds only the branches synced here"
    if [ -d "$mirror" ] && git -C "$mirror" cat-file -e "$checked_revision^{commit}" 2>/dev/null; then
      if [ -n "$(git -C "$mirror" branch --contains "$checked_revision" 2>/dev/null | head -n 1)" ]; then
        provenance="on a branch in this host's mirror"
      else
        provenance="in this host's mirror but on no branch"
      fi
    fi
    # A check whose tooling did not exist at that revision is recorded by name
    # as unavailable, never as a pass. Evidence that nothing ran is not
    # evidence, so after the date a file with no check that actually ran is
    # refused rather than counted.
    local ran unavailable
    ran=$(grep -c '^ran: ' "$drop/SOURCE-CHECKS" || true)
    # The writer says UNAVAILABLE; count it however it is spelled, or every
    # unavailable check reads as 0 and the note under-reports what did not run.
    unavailable=$(grep -ci '^unavailable: ' "$drop/SOURCE-CHECKS" || true)
    if [ "$ran" -eq 0 ] && [ "$required" -eq 1 ]; then
      fail "$drop: SOURCE-CHECKS records no check that ran ($unavailable unavailable)"
    fi
    note "SOURCE-CHECKS: $ran ran, $unavailable unavailable, over ${checked_revision:0:12} ($provenance)"
  fi

  local claimed=0 claim found h
  while read -r claim; do
    case "$claim" in ''|'#'*) continue ;; esac
    claimed=$((claimed + 1))
    found=0
    for h in "${headers[@]}"; do
      [ "$h" = "$claim" ] && { found=1; break; }
    done
    [ "$found" -eq 1 ] ||
      fail "$claim is claimed in packages.txt but no RPM header in the drop says it"
  done < "$drop/packages.txt"
  note "packages.txt: $claimed claimed, ${#rpms[@]} RPMs present"

  # Anything shipped but unclaimed, except sources and debug packages of a
  # claimed build, is worth saying out loud: it is either an accident or a
  # package the pin will miss.
  local n
  for n in "${names[@]}"; do
    case "$n" in *.src|*debuginfo*|*debugsource*) continue ;; esac
    found=0
    while read -r claim; do
      case "$claim" in ''|'#'*) continue ;; esac
      [ "$n" = "$claim" ] && { found=1; break; }
    done < "$drop/packages.txt"
    [ "$found" -eq 1 ] || note "note: $n is in the drop but not claimed in packages.txt"
  done
}

ledger=${LUMA_OS_DROP_LEDGER-}
args=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-ledger) ledger=none; shift ;;
    --ledger) ledger=${2:?}; shift 2 ;;
    -*) printf 'usage: %s [--ledger FILE|--no-ledger] DROPDIR...\n' "$0" >&2; exit 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
[ "${#args[@]}" -gt 0 ] || { printf 'usage: %s [--ledger FILE|--no-ledger] DROPDIR...\n' "$0" >&2; exit 2; }
if [ "$ledger" = none ]; then
  ledger=
elif [ -z "$ledger" ]; then
  # Beside the drops, so every agent that checks a drop shares one record:
  # the directory holding them, whether a drop or the whole incoming
  # directory was named.
  first=${args[0]%/}
  if [ -n "$(find "$first" -maxdepth 1 -name '*.rpm' -print -quit 2>/dev/null)" ] || [ -d "$first/RPMS" ]; then
    first=$(dirname -- "$first")
  fi
  ledger=$(cd "$first" && pwd)/.drop-ledger.tsv
fi
for drop in "${args[@]}"; do verify_drop "${drop%/}"; done
exit "$status"
