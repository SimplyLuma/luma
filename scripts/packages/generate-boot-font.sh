#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output=${1:-$repo_root/build/generated/prairie.pf2}
font=${2:-/usr/share/fonts/google-figtree-fonts/Figtree[wght].ttf}

command -v grub2-mkfont >/dev/null 2>&1 || {
  printf 'error: grub2-mkfont is required\n' >&2
  exit 1
}
test -f "$font" || {
  printf 'error: Figtree source font not found: %s\n' "$font" >&2
  exit 1
}

install -d -m 0755 "${output%/*}"
grub2-mkfont --output="$output" --name='Prairie Regular' --size=16 "$font"
test -s "$output"
