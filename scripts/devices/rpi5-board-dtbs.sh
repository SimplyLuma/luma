#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Device trees for the Raspberry Pi 5 boot partition, in one of two layouts.
#
# --layout firmware  (kernel-luma-rpi; /usr/lib/modules/<release>/dtb)
#   OUT/<name>.dtb      every broadcom/bcm27*.dtb, at the root where the board
#                       firmware looks; it applies config.txt's dtoverlay= and
#                       dtparam= lines and its own fixups (MAC, Bluetooth
#                       address, serial, memory) and hands the tree to U-Boot,
#                       which passes it on to GRUB and Linux unchanged.
#   OUT/overlays/       the kernel's overlays (overlay_map.dtb included).
#   No dtb/ directory: with one, U-Boot would load that instead.
#
# --layout uboot     (Fedora's kernel; /usr/lib/modules/<release>/dtb/broadcom)
#   OUT/bcm2712-rpi-5-b.dtb, OUT/bcm2712d0-rpi-5-b.dtb
#       What the board firmware loads (C1, D0) and hands to U-Boot.
#   OUT/dtb/broadcom/<name>.dtb
#       What U-Boot (rpi_arm64) loads as fdtfile and passes to Linux. Fedora's
#       kernel hangs on the firmware's runtime-edited tree, so it must get one
#       of these. Every tree gets a 256 MiB CMA pool below 1 GiB.
#   With --prefix DIR the uboot trees go to OUT/DIR/broadcom/ only (for GRUB
#   devicetree entries that boot Fedora's kernel from a firmware-layout card).
#
# usage: rpi5-board-dtbs.sh --layout firmware|uboot --kernel-dtbs DIR --output DIR [--prefix DIR]
#
# Used by build-rpi5-sd-image.sh (installer card) and
# build-rpi5-disk-image.sh (preinstalled card).
set -euo pipefail
export LC_ALL=C

# 256 MiB, inside the trees' own 0-1 GiB alloc-ranges: cma=256M@0M-1024M.
cma_size="0 10000000"
cma_ranges="0 0 0 40000000"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
usage() { sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2; }

layout="" src="" out="" prefix=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --layout) layout=${2:?}; shift 2 ;;
    --kernel-dtbs) src=${2:?}; shift 2 ;;
    --output) out=${2:?}; shift 2 ;;
    --prefix) prefix=${2:?}; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$src" ] && [ -n "$out" ] || usage
for tool in fdtget fdtput; do
  command -v "$tool" >/dev/null 2>&1 || fail "required tool is missing: $tool"
done

if [ "$layout" = firmware ]; then
  [ -z "$prefix" ] || usage
  for f in broadcom/bcm2712-rpi-5-b.dtb broadcom/bcm2712d0-rpi-5-b.dtb overlays/overlay_map.dtb \
           overlays/vc4-kms-v3d-pi5.dtbo; do
    [ -f "$src/$f" ] || fail "not a Raspberry Pi kernel's device trees: $f is missing"
  done
  install -d -m 0755 "$out/overlays"
  find "$src/broadcom" -maxdepth 1 -name 'bcm27*.dtb' -print0 | sort -z |
    xargs -0 -I{} install -m 0644 {} "$out/"
  find "$src/overlays" -maxdepth 1 -type f \( -name '*.dtbo' -o -name '*.dtb' -o -name README \) -print0 | sort -z |
    xargs -0 -I{} install -m 0644 {} "$out/overlays/"
  [ ! -e "$out/dtb" ] || fail "a firmware-layout partition must not have a dtb/ directory"
  (cd "$out" && ls bcm27*.dtb)
  exit 0
fi

[ "$layout" = uboot ] || usage
for name in bcm2712-rpi-5-b.dtb bcm2712-d-rpi-5-b.dtb; do
  [ -f "$src/$name" ] || fail "the kernel has no $name"
done
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM

# Names U-Boot asks for as fdtfile; the Pi 5 pair is required, the others go
# on when the kernel has them.
names=()
for name in bcm2712-rpi-5-b.dtb bcm2712-d-rpi-5-b.dtb bcm2712-rpi-500.dtb \
            bcm2712-rpi-cm5-cm5io.dtb bcm2712-rpi-cm5l-cm5io.dtb; do
  [ -f "$src/$name" ] && names+=("$name")
done
for name in "${names[@]}"; do
  dtb="$work/$name"
  install -m 0644 "$src/$name" "$dtb"
  node=/reserved-memory/linux,cma
  [ "$(fdtget "$dtb" "$node" compatible)" = shared-dma-pool ] || fail "$name: no CMA pool"
  [ "$(fdtget -t x "$dtb" "$node" alloc-ranges)" = "$cma_ranges" ] || fail "$name: unexpected CMA range"
  # shellcheck disable=SC2086
  fdtput -t x "$dtb" "$node" size $cma_size
  [ "$(fdtget -t x "$dtb" "$node" size)" = "$cma_size" ] || fail "$name: CMA size not applied"
done

dir=${prefix:-dtb}
install -d -m 0755 "$out/$dir/broadcom"
if [ -z "$prefix" ]; then
  # The board firmware loads bcm2712-rpi-5-b.dtb on C1 and bcm2712d0-rpi-5-b.dtb
  # on D0 (upstream calls the D0 tree bcm2712-d-rpi-5-b.dtb).
  install -m 0644 "$work/bcm2712-rpi-5-b.dtb" "$out/bcm2712-rpi-5-b.dtb"
  install -m 0644 "$work/bcm2712-d-rpi-5-b.dtb" "$out/bcm2712d0-rpi-5-b.dtb"
fi
for name in "${names[@]}"; do
  install -m 0644 "$work/$name" "$out/$dir/broadcom/$name"
done
printf '%s\n' "${names[@]}"
