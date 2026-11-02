#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Raspberry Pi 5 device trees for the boot partition, from one Fedora kernel's
# upstream trees (/usr/lib/modules/<release>/dtb/broadcom).
#
#   OUT/bcm2712-rpi-5-b.dtb, OUT/bcm2712d0-rpi-5-b.dtb
#       What the board firmware loads (C1, D0) and hands to U-Boot.
#   OUT/dtb/broadcom/<name>.dtb
#       What U-Boot (rpi_arm64) loads as fdtfile and passes to Linux, for the
#       Pi 5 C1 and D0 and, when the kernel has them, Pi 500, CM5 and CM5 Lite.
#
# Every tree gets a 256 MiB CMA pool below 1 GiB. With --hdmi-audio yes, the
# U-Boot trees also get src/luma-rpi5-platform/card/dtb/luma-rpi5-hdmi-audio
# (and the -d0 fragment on brcm,bcm2712-d0 trees). Only pass yes when the
# system that boots these trees carries luma-rpi5-kmod for that kernel: stock
# vc4 waits forever for a DMA controller it has no driver for.
#
# usage: rpi5-board-dtbs.sh --kernel-dtbs DIR --hdmi-audio yes|no --output DIR
#
# Used by build-rpi5-sd-image.sh (installer card) and
# build-rpi5-disk-image.sh (preinstalled card).
set -euo pipefail
export LC_ALL=C

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
fragments="$repo_root/src/luma-rpi5-platform/card/dtb"
# 256 MiB, inside the trees' own 0-1 GiB alloc-ranges: cma=256M@0M-1024M.
cma_size="0 10000000"
cma_ranges="0 0 0 40000000"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
usage() { sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2; }

src="" hdmi_audio="" out=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --kernel-dtbs) src=${2:?}; shift 2 ;;
    --hdmi-audio) hdmi_audio=${2:?}; shift 2 ;;
    --output) out=${2:?}; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$src" ] && [ -n "$out" ] || usage
case "$hdmi_audio" in yes|no) ;; *) usage ;; esac
for tool in dtc fdtget fdtoverlay fdtput; do
  command -v "$tool" >/dev/null 2>&1 || fail "required tool is missing: $tool"
done
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

install -d -m 0755 "$out/dtb/broadcom"
# The board firmware loads bcm2712-rpi-5-b.dtb on C1 and bcm2712d0-rpi-5-b.dtb
# on D0 (upstream calls the D0 tree bcm2712-d-rpi-5-b.dtb).
install -m 0644 "$work/bcm2712-rpi-5-b.dtb" "$out/bcm2712-rpi-5-b.dtb"
install -m 0644 "$work/bcm2712-d-rpi-5-b.dtb" "$out/bcm2712d0-rpi-5-b.dtb"

if [ "$hdmi_audio" = yes ]; then
  for frag in luma-rpi5-hdmi-audio luma-rpi5-hdmi-audio-d0; do
    dtc -q -@ -I dts -O dtb -o "$work/$frag.dtbo" "$fragments/$frag.dtso"
  done
fi
for name in "${names[@]}"; do
  dst="$out/dtb/broadcom/$name"
  if [ "$hdmi_audio" = no ]; then
    install -m 0644 "$work/$name" "$dst"
    continue
  fi
  # The HDMI DMA request lines differ by stepping: C1 10/17, D0 12/13.
  frags=("$work/luma-rpi5-hdmi-audio.dtbo")
  dreqs="41fa000a 41fa0011"
  if fdtget "$work/$name" / compatible | tr ' ' '\n' | grep -qx 'brcm,bcm2712-d0'; then
    frags+=("$work/luma-rpi5-hdmi-audio-d0.dtbo")
    dreqs="41fa000c 41fa000d"
  fi
  fdtoverlay -i "$work/$name" -o "$dst" "${frags[@]}" || fail "luma-rpi5-hdmi-audio does not apply to $name"
  [ "$(fdtget "$dst" /soc@107c000000/dma@10600 compatible)" = brcm,bcm2712-dma ] ||
    fail "$name: BCM2712 DMA controller missing after the merge"
  got="$(fdtget -t x "$dst" "$(fdtget "$dst" /__symbols__ hdmi0)" dmas | cut -d' ' -f2) $(fdtget -t x "$dst" "$(fdtget "$dst" /__symbols__ hdmi1)" dmas | cut -d' ' -f2)"
  [ "$got" = "$dreqs" ] || fail "$name: HDMI DMA requests are $got, expected $dreqs"
  chmod 0644 "$dst"
done
printf '%s\n' "${names[@]}"
