#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# The one command to run on a drop before handing it over.
#
#   scripts/os/hand-over-drop.sh [--revision REV] [--repo GIT_DIR]
#                                [--current-checkers] [--backfill] DROP_DIR
#
# It writes DROP_DIR/SOURCE-CHECKS and then runs verify-drop.sh, which is what
# collect-packages.sh will run. From 2026-09-27 collect-packages refuses a drop
# without SOURCE-CHECKS, and until today no drop cut from work/os-release-pipeline
# could have one: the writer was not on that branch, it needs a Git work tree
# at the built revision (a build tree rsynced to the build host has none), and
# the repository check it runs failed on that branch for an unrelated reason.
# This makes the work tree itself, so nobody has to.
#
#   REV      the full commit the RPMs were built from. Default: DROP_DIR/SOURCE-COMMIT.
#            Given and the drop has no SOURCE-COMMIT, it is written.
#   GIT_DIR  a repository that has REV. Default: the build host mirror if present,
#            else the repository this script is in. Only read, never written:
#            the checkout is a --shared clone in a temporary directory.
#   --current-checkers  run tools/check-repository.sh and the unreached-test
#            checker from THIS checkout over REV's tree, recorded as such in
#            SOURCE-CHECKS. For drops cut before the Calendar contract was
#            corrected (2026-09-22), whose own check-repository.sh fails on it.
#   --backfill  for a drop built before a check existed; see
#            check-source-before-drop.sh.
#
# The environment is CI's: python3, python3-gobject, python3-pyyaml, git,
# dbus-daemon (tests/unit, from 2026-10-04). Exits non-zero, and leaves no
# SOURCE-CHECKS behind, if any check fails.

set -euo pipefail

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
checkout=$(CDPATH='' cd -- "$here/../.." && pwd)
revision="" repo="" gate_args=()
while [ "$#" -gt 1 ]; do
  case "$1" in
    --revision) revision=${2:?}; shift 2 ;;
    --repo) repo=${2:?}; shift 2 ;;
    --current-checkers) gate_args+=(--tools-from "$checkout"); shift ;;
    --backfill) gate_args+=(--backfill); shift ;;
    *) break ;;
  esac
done
[ "$#" -eq 1 ] && [ -d "$1" ] || {
  printf 'usage: %s [--revision REV] [--repo GIT_DIR] [--current-checkers] [--backfill] DROP_DIR\n' "$0" >&2
  exit 2
}
drop=$(CDPATH='' cd -- "$1" && pwd)
die() { printf 'hand-over-drop: %s\n' "$*" >&2; exit 1; }

recorded=""
[ ! -f "$drop/SOURCE-COMMIT" ] || recorded=$(tr -d '[:space:]' <"$drop/SOURCE-COMMIT")
if [ -z "$revision" ]; then
  revision=$recorded
  [ -n "$revision" ] || die "$drop has no SOURCE-COMMIT; pass --revision <full commit the RPMs were built from>"
elif [ -n "$recorded" ] && [ "$recorded" != "$revision" ]; then
  die "$drop/SOURCE-COMMIT says $recorded, not $revision"
fi
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || die "revision must be a full 40-character commit hash: $revision"

if [ -z "$repo" ]; then
  mirror=${LUMA_OS_ROOT:-/mnt/luma-secondary/luma-build/os-release/fs}/git/ProjectLuma.git
  if [ -d "$mirror" ]; then repo=$mirror; else repo=$(git -C "$checkout" rev-parse --absolute-git-dir); fi
fi
git -C "$repo" cat-file -e "$revision^{commit}" 2>/dev/null ||
  die "$repo does not have $revision. Push it; on the build host the coordinator syncs the mirror (bundle, scp, fetch)."
if [ -z "$(git -C "$repo" branch --all --contains "$revision" 2>/dev/null | head -n 1)" ]; then
  printf 'hand-over-drop: warning: %s is on no branch in %s; nothing can rebuild it from there\n' "$revision" "$repo" >&2
fi

work=$(mktemp -d "${TMPDIR:-/var/tmp}/hand-over-drop.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM
git clone --quiet --shared --no-checkout "$repo" "$work/src"
git -C "$work/src" -c advice.detachedHead=false checkout --quiet --detach "$revision"
[ "$(git -C "$work/src" rev-parse HEAD)" = "$revision" ] || die "checkout is not at $revision"

"$here/check-source-before-drop.sh" "${gate_args[@]}" "$work/src" "$drop" "$revision" ||
  die "source checks failed at $revision; the drop is not fit to hand over"
[ -f "$drop/SOURCE-CHECKS" ] || die "check-source-before-drop.sh passed but wrote no SOURCE-CHECKS"
[ -n "$recorded" ] || printf '%s\n' "$revision" >"$drop/SOURCE-COMMIT"

# Checked the way collect-packages will check it, on the day it becomes
# mandatory, so a drop that passes here is not refused there.
LUMA_OS_SOURCE_CHECKS_FROM=0000-00-00 "$here/verify-drop.sh" "$drop" ||
  die "verify-drop.sh refused $drop"
printf 'hand-over-drop: %s is ready to hand over (SOURCE-CHECKS over %s)\n' "$drop" "$revision"
