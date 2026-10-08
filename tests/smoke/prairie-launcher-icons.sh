#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Every visible Luma launcher must have artwork an installed image can resolve.
#
# This exists because the failure it catches is completely silent. A desktop
# file naming `Icon=org.projectluma.Clock` installs perfectly, rpm verifies
# clean, and the application launches — and the user sees a question mark,
# because no package ever shipped the artwork. Ten of eleven core applications
# were in that state while every other check passed.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$repo_root"

theme_apps=assets/icon-theme/Prairie/scalable/apps
spec=packaging/rpm/prairie-icon-theme.spec
builder=scripts/packages/build-prairie-icon-theme.sh

test -d "$theme_apps" || {
  printf 'error: the Prairie theme has no application artwork directory: %s\n' \
    "$theme_apps" >&2
  exit 1
}

# 1. The packaging must ship the whole artwork directory. Naming each icon as
#    its own Source is what allowed twelve existing SVGs to go unpackaged.
grep -Fq 'app-icons.tar' "$spec" || {
  printf 'error: %s does not ship the application artwork directory.\n' "$spec" >&2
  printf '       Artwork added to %s would never reach an installed image.\n' \
    "$theme_apps" >&2
  exit 1
}
grep -Fq 'app-icons.tar' "$builder" || {
  printf 'error: %s does not assemble app-icons.tar\n' "$builder" >&2
  exit 1
}

# 2. Every visible launcher carrying a Luma-branded icon name must have
#    artwork. A generic freedesktop name such as `edit-find-symbolic` is
#    legitimately inherited from Adwaita and is not Luma's to ship; an
#    `org.projectluma.*` name is an identity Luma owns and must provide.
missing=0
checked=0
warned=0
while IFS= read -r desktop; do
  grep -Fqx 'Type=Application' "$desktop" || continue
  grep -Fqx 'NoDisplay=true' "$desktop" && continue
  icon=$(sed -n 's/^Icon=//p' "$desktop" | head -1)
  [ -n "$icon" ] || {
    printf 'error: %s declares no Icon=\n' "$desktop" >&2
    missing=$((missing + 1))
    continue
  }
  case "$icon" in
    org.projectluma.*) ;;
    *)
      # Inherited from adwaita-icon-theme or hicolor, which the Prairie theme
      # declares as its fallbacks.
      continue
      ;;
  esac
  checked=$((checked + 1))

  if [ -f "$theme_apps/$icon.svg" ]; then
    continue
  fi
  # An application package may ship its own hicolor artwork instead.
  if find src -path "*/data/$icon.svg" -print -quit 2>/dev/null | grep -q .; then
    continue
  fi

  # Core applications are a release contract; anything else is reported so the
  # gap is visible without blocking an unrelated change.
  case "$desktop" in
    src/prairie-core/*)
      printf 'error: %s declares Icon=%s but no artwork exists\n' \
        "$(basename "$desktop")" "$icon" >&2
      printf '       looked in %s/%s.svg and src/*/data/%s.svg\n' \
        "$theme_apps" "$icon" "$icon" >&2
      missing=$((missing + 1))
      ;;
    *)
      printf 'warning: %s declares Icon=%s with no artwork (not a core launcher)\n' \
        "$(basename "$desktop")" "$icon" >&2
      warned=$((warned + 1))
      ;;
  esac
done < <(find src -name 'org.projectluma.*.desktop' | sort)

if [ "$missing" -gt 0 ]; then
  printf '\n%d visible launcher(s) would render as a question mark.\n' "$missing" >&2
  exit 1
fi

printf 'prairie launcher icons: %d Luma-branded launchers checked, every core icon has artwork' \
  "$checked"
if [ "$warned" -gt 0 ]; then
  printf ' (%d non-core launcher(s) still missing artwork)' "$warned"
fi
printf '\n' 
