#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
# Copyright (C) 2026 Project Luma contributors
#
# Fetch Atlas's pinned, verified build inputs into .build-inputs/:
#   - Cockpit's pkg/lib at the commit anaconda-webui 68 pins
#   - Figtree at the Google Fonts commit config/desktop/inputs.env pins
# Nothing downloaded here is committed.

set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$here/../.." && pwd)
# shellcheck disable=SC1091
. "$here/upstream/inputs.env"
# shellcheck disable=SC1091
. "${ATLAS_DESKTOP_INPUTS:-$repo_root/config/desktop/inputs.env}"

inputs="$here/.build-inputs"
mkdir -p "$inputs/fonts"

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

fetch_verified() {
  destination=$1 url=$2 expected=$3
  if [ -f "$destination" ] && [ "$(sha256 "$destination")" = "$expected" ]; then
    return 0
  fi
  curl -L --fail --silent --show-error -o "$destination.partial" "$url"
  actual=$(sha256 "$destination.partial")
  if [ "$actual" != "$expected" ]; then
    rm -f "$destination.partial"
    printf 'error: %s has SHA-256 %s, expected %s\n' "$url" "$actual" "$expected" >&2
    exit 1
  fi
  mv "$destination.partial" "$destination"
}

font_base="https://raw.githubusercontent.com/google/fonts/$FIGTREE_GOOGLE_FONTS_COMMIT/ofl/figtree"
fetch_verified "$inputs/fonts/Figtree.ttf" "$font_base/Figtree%5Bwght%5D.ttf" "$FIGTREE_REGULAR_SHA256"
fetch_verified "$inputs/fonts/OFL.txt" "$font_base/OFL.txt" "$FIGTREE_OFL_SHA256"

stamp="$inputs/pkg/lib/.cockpit-commit"
if [ "$(cat "$stamp" 2>/dev/null || true)" != "$ATLAS_COCKPIT_LIB_COMMIT" ]; then
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  git -C "$work" init -q
  git -C "$work" fetch -q --no-tags --depth=1 https://github.com/cockpit-project/cockpit.git "$ATLAS_COCKPIT_LIB_COMMIT"
  tree=$(git -C "$work" rev-parse "FETCH_HEAD^{tree}")
  rm -rf "$inputs/pkg"
  git -C "$work" archive "$tree" -- pkg/lib | tar -x -C "$inputs"
  printf '%s\n' "$ATLAS_COCKPIT_LIB_COMMIT" > "$stamp"
fi

printf 'build inputs ready in %s\n' "$inputs"
