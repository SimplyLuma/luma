#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Compose the Fairphone 6 boot DTB: the kernel-built milos-fairphone-fp6.dtb
# with the overlays of the kernel tag's luma/fp6/alpha1/dtb applied by that
# directory's compose-dtb.sh (devices/fairphone/fp6/device.toml [kernel]
# dtb_overlays). compose-dtb.sh pins the Alpha 1 base DTB; this runs it
# unchanged except for that pin, so a kernel with DTS patches gets the same
# composition. On the Alpha 1 base it reproduces the Alpha 1 DTB.
#
# usage: compose-fp6-dtb.sh SOURCE_DIR BASE_DTB OUTPUT
#   SOURCE_DIR  the kernel tree build-fp6.sh built (it holds dtb_overlays)
#   BASE_DTB    that build's milos-fairphone-fp6.dtb
#
# Needs cpp, dtc, fdtoverlay, fdtput, fdtget (dtc 1.7.2) and python3.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
fail() { printf 'compose-fp6-dtb: %s\n' "$*" >&2; exit 1; }
[ "$#" -eq 3 ] || { sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2; }
source_dir=$(realpath -e "$1")
base=$(realpath -e "$2")
output=$3

overlays=$(python3 - "$repo_root/devices/fairphone/fp6/device.toml" <<'PY'
import sys, tomllib
print(tomllib.load(open(sys.argv[1], "rb"))["kernel"]["dtb_overlays"])
PY
)
[ -f "$source_dir/$overlays/compose-dtb.sh" ] || fail "$source_dir has no $overlays/compose-dtb.sh"

# The FT9362 overlay addresses the pin controller by its phandle in the
# Alpha 1 base (0x35), since that DTB has no __symbols__.
[ "$(fdtget -t x "$base" /soc@0/pinctrl@f100000 phandle)" = 35 ] ||
  fail "$base: /soc@0/pinctrl@f100000 is not phandle 0x35, which the fingerprint overlay names"

work=$(mktemp -d "${TMPDIR:-/var/tmp}/fp6-dtb.XXXXXX")
trap 'rm -rf "$work"' EXIT
install -d "$work/inputs/include/dt-bindings/interrupt-controller"
install -m 0644 "$base" "$work/inputs/base.dtb"
install -m 0644 "$source_dir/include/dt-bindings/interrupt-controller/irq.h" \
  "$work/inputs/include/dt-bindings/interrupt-controller/irq.h"
install -m 0644 "$source_dir/$overlays"/*.dtso "$work/inputs/"
pin='^\[\[ \$\(sha256sum "\$base_dtb" \| cut -d'"' '"' -f1\) == [0-9a-f]{64} \]\]$'
[ "$(grep -cE "$pin" "$source_dir/$overlays/compose-dtb.sh")" = 1 ] ||
  fail "$overlays/compose-dtb.sh does not pin its base DTB the way this expects"
grep -vE "$pin" "$source_dir/$overlays/compose-dtb.sh" >"$work/compose-dtb.sh"
(cd "$work" && bash ./compose-dtb.sh >/dev/null 2>"$work/compose.log") ||
  { cat "$work/compose.log" >&2; fail "$overlays/compose-dtb.sh failed"; }
install -D -m 0644 "$work/dtb-composition/milos-fairphone-fp6-ims-audio.dtb" "$output"
sha256sum "$output" | cut -d' ' -f1
