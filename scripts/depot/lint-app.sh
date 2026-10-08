#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Run flatpak-builder-lint (Flathub's linter, from org.flatpak.Builder) on an
# app's manifest, build directory and exported repository.
#
#   lint-app.sh APP_ID MANIFEST BUILD_DIR REPO OUT_DIR
#
# Luma is not Flathub: a few checks encode Flathub policy rather than
# correctness (its own runtimes list, screenshots mirrored into Flathub's
# media store). packaging/flatpak/lint-exceptions.json names each exception
# with its reason; every other error fails the build.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"

app_id=$1 manifest=$2 build_dir=$3 repo=$4 out=$5
exceptions="$depot_repo_root/packaging/flatpak/lint-exceptions.json"
scratch="$DEPOT_ROOT/work/lint/$app_id"
depot_in_tools install -d "$scratch"

depot_in_tools bash -s -- "$app_id" "$manifest" "$build_dir" "$repo" "$out" "$exceptions" "$scratch" <<'SH'
set -uo pipefail
app_id=$1 manifest=$2 build_dir=$3 repo=$4 out=$5 exceptions=$6 scratch=$7
# Builder initializes its own working state. Keep that state outside the
# separately sealed producer inputs, without hiding new source members.
cd "$scratch"
lint() {
  flatpak run --user --command=flatpak-builder-lint \
    --filesystem="$(dirname "$manifest")" --filesystem="$build_dir" \
    --filesystem="$repo" --filesystem="$(dirname "$exceptions")" --filesystem="$scratch" \
    org.flatpak.Builder --exceptions --user-exceptions "$exceptions" --appid "$app_id" "$@"
}
status=0
lint manifest "$manifest" >"$out/lint-manifest.json" 2>&1 || status=1
lint builddir "$build_dir" >"$out/lint-builddir.json" 2>&1 || status=1
lint repo "$repo" >"$out/lint-repo.json" 2>&1 || status=1
for f in "$out"/lint-*.json; do printf '%s: ' "$(basename "$f")"; tr -d '\n' <"$f" | cut -c1-600; echo; done
exit $status
SH
