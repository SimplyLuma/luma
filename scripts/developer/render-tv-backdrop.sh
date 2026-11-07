#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Render the TV home's backdrop: the prism wallpaper blurred right out, ahead of
# time, so the shell shows a still image instead of a live blur (a live
# backdrop blur costs a full-screen blit per frame on small GPUs). The recipe is
# the Design Center mockup's (luma-tv-empty.html .bg and .shade): the wallpaper
# covers the screen plus 120 on every side, blur 80, saturate 1.25, brightness
# 0.62, then a shade of black 0.18 at the top fading out by 30% and black 0.35
# at the bottom from 55%. 1920x1080; it is blurred, so it scales to any screen.
# Needs ImageMagick 7 (`magick`).
set -euo pipefail
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
source=${1:-$repo/assets/wallpapers/prism.png}
output=${2:-$repo/src/luma-shell-presentation/tv-backdrop.jpg}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# Blur at a quarter of the size (sigma 80 -> 20): the same result, much faster.
magick "$source" -colorspace sRGB -alpha off -resize '2160x1320^' -gravity center -extent 2160x1320 \
  -resize 540x330 -blur 0x20 -resize 2160x1320 \
  -modulate 100,125 -evaluate multiply 0.62 \
  -gravity center -crop 1920x1080+0+0 +repage "$work/blurred.png"
magick -size 1x1080 xc:black -alpha set -channel A \
  -fx 'j/h < 0.3 ? 0.18*(1 - (j/h)/0.3) : (j/h < 0.55 ? 0 : 0.35*((j/h) - 0.55)/0.45)' +channel \
  -scale '1920x1080!' "$work/shade.png"
magick "$work/blurred.png" "$work/shade.png" -composite -strip -sampling-factor 4:2:0 -quality 88 "$output"
printf '%s\n' "$output"
