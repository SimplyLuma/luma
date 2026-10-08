#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# scripts/os/lib/system_apps_current.py: an older system-app pin fails, the
# newest pin passes, HOLD drops are ignored, and a changed pin needs an
# "Updated <App> to X.Y" improvement fragment. Needs python3-rpm and git.
#
#   tests/smoke/system-apps-current.sh [REPO]
set -euo pipefail
repo=$(realpath "${1:-$(dirname -- "$0")/../..}")
tool="$repo/scripts/os/lib/system_apps_current.py"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail() { echo "FAIL $*" >&2; exit 1; }

src="$work/src"
mkdir -p "$src/config/desktop" "$src/changes" "$work/pool" "$work/incoming/held" "$work/incoming/empty"
printf 'viola-browser-stable  Viola\n' >"$src/config/desktop/system-apps.txt"
printf 'viola-browser-stable-151.0.7922.72-2.viola8210.native21.x86_64\n' >"$src/config/desktop/packages.txt"
touch "$work/incoming/held/HOLD"

# 1. Pool has only older or equal builds: pass.
printf '%s x y\n' viola-browser-stable-151.0.7922.72-1.viola8210.x86_64 \
  viola-browser-stable-151.0.7922.72-2.viola8210.native21.x86_64 >"$work/pool/pool.manifest"
python3 "$tool" pins --source "$src" --pool "$work/pool" --incoming "$work/incoming" >"$work/out" ||
  fail "a current pin failed: $(cat "$work/out")"
grep -q '^PASS viola-browser-stable' "$work/out" || fail 'no PASS line for a current pin'

# 2. A newer build in the pool: fail loudly and name it.
printf '%s x y\n' viola-browser-stable-151.0.7922.72-3.viola8210.native22.x86_64 >>"$work/pool/pool.manifest"
if python3 "$tool" pins --source "$src" --pool "$work/pool" --incoming "$work/incoming" >"$work/out"; then
  fail 'an older pin passed'
fi
grep -q '^FAIL viola-browser-stable: pinned 151.0.7922.72-2.viola8210.native21 is older than the released 151.0.7922.72-3.viola8210.native22' "$work/out" ||
  fail "unexpected message: $(cat "$work/out")"

# 3. Notes: a changed pin without a fragment fails; with one it passes.
git -C "$src" init -q
git -C "$src" -c user.name=t -c user.email=t@t add -A
git -C "$src" -c user.name=t -c user.email=t@t commit -qm base
base=$(git -C "$src" rev-parse HEAD)
printf 'viola-browser-stable-151.0.7922.72-1.viola8210.x86_64 a b\n' >"$work/previous.manifest"
printf 'viola-browser-stable-151.0.7922.72-2.viola8210.native21.x86_64 a b\n' >"$work/current.manifest"
if python3 "$tool" notes --repo "$src" --from "$base" --to HEAD --previous-manifest "$work/previous.manifest" \
     --manifest "$work/current.manifest" >"$work/out"; then
  fail 'a changed pin without a fragment passed'
fi
printf -- '---\ntype: fix\npackages: [viola-browser-stable]\n---\nSummary: Fixed Viola\nDetails: x.\n' >"$src/changes/wrong.md"
printf -- '---\ntype: improvement\npackages: [viola-browser-stable]\n---\nSummary: Updated Viola to 0.2.10-21\nDetails: x.\n' >"$src/changes/viola.md"
git -C "$src" -c user.name=t -c user.email=t@t add -A
git -C "$src" -c user.name=t -c user.email=t@t commit -qm notes
python3 "$tool" notes --repo "$src" --from "$base" --to HEAD --previous-manifest "$work/previous.manifest" \
  --manifest "$work/current.manifest" >"$work/out" || fail "a noted pin change failed: $(cat "$work/out")"
grep -q '^PASS viola-browser-stable: .* is noted in changes/viola.md' "$work/out" || fail "unexpected message: $(cat "$work/out")"

echo 'PASS system apps current: stale pins fail, noted pin changes pass'
