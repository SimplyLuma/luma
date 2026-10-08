#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Check that Luma's shipped default for Settings › Displays › Night Light's
# Color Temperature sits at the slider's far-left, least-warm end.
#
# The slider is inverted (panels/display/cc-night-light-page.blp: adjustment
# lower=1700 upper=4700, `inverted: true`) and its trough gradient runs pale
# (4700K) at the left to deep orange (1700K) at the right
# (panels/display/night-light.css). So the far-left value is the adjustment's
# own maximum, 4700.
#
# This default lives in the image's desktop-defaults dconf database --
# config/desktop/dconf/db/luma.d/00-luma-desktop, installed to
# /etc/dconf/db/luma.d/00-luma-desktop by image/luma-desktop/scripts/
# configure-system.sh and compiled with `dconf update` -- the same place
# every other Luma fresh-profile GNOME default (icon theme, wallpaper, clock
# format, Shelf...) lives, so it can't be silently dropped by an unrelated
# package change. It deliberately does NOT live in a package's own
# gschema.override.
#
# Usage:
#   check-shipped-override.sh                              # check the repo source file
#   check-shipped-override.sh path/to/00-luma-desktop       # check an installed copy
#   check-shipped-override.sh path/to/etc-dconf-db-luma.d   # check an installed /etc/dconf/db/luma.d dir
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
target=${1:-"$repo_root/config/desktop/dconf/db/luma.d/00-luma-desktop"}
expect_key="night-light-temperature=uint32 4700"
status=0

if [ -d "$target" ]; then
  target="$target/00-luma-desktop"
fi
test -f "$target" || { printf 'FAIL %s not found\n' "$target" >&2; exit 1; }

if grep -Fxq "$expect_key" "$target"; then
  printf 'PASS %s ships %s\n' "$target" "$expect_key"
else
  printf 'FAIL %s does not set %s\n' "$target" "$expect_key" >&2
  status=1
fi
if ! grep -Fq '[org/gnome/settings-daemon/plugins/color]' "$target"; then
  printf 'FAIL %s is missing the [org/gnome/settings-daemon/plugins/color] group\n' "$target" >&2
  status=1
fi

# dconf itself is the authority on whether this resolves to 4700 for a fresh
# profile with no user value -- not just that our text matches by eye.
if command -v dconf >/dev/null 2>&1; then
  db_root=$(mktemp -d)
  trap 'rm -rf "$db_root"' EXIT
  mkdir -p "$db_root/db/luma.d"
  cp "$target" "$db_root/db/luma.d/00-luma-desktop"
  printf 'user-db:user\nsystem-db:luma\n' >"$db_root/profile"
  DCONF_PROFILE="$db_root/profile" dconf update "$db_root/db" 2>&1 || {
    printf 'FAIL dconf update failed to compile %s\n' "$target" >&2
    status=1
  }
  empty_home=$(mktemp -d)
  resolved=$(DCONF_PROFILE="$db_root/profile" XDG_CONFIG_HOME="$empty_home/.config" \
    dconf read /org/gnome/settings-daemon/plugins/color/night-light-temperature 2>/dev/null || echo "")
  rm -rf "$empty_home"
  if [ "$resolved" = "uint32 4700" ]; then
    printf 'PASS compiled dconf db resolves night-light-temperature to %s for a fresh profile\n' "$resolved"
  else
    printf 'FAIL compiled dconf db resolved night-light-temperature to "%s", expected uint32 4700\n' "$resolved" >&2
    status=1
  fi
else
  printf 'SKIP dconf CLI not available (Linux-only); text-only check above still applies\n'
fi

exit "$status"
