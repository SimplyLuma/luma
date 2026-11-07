#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Configure the Luma Raspberry Pi kernel: Fedora's configuration for the same
# series, plus everything Raspberry Pi's bcm2711_defconfig enables, plus
# config/kernel/rpi.config; then fail on any unexplained lost option.
# usage: configure-rpi.sh SOURCE OUTPUT FEDORA_CONFIG
set -euo pipefail
repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
source_dir=$(realpath "${1:?usage: configure-rpi.sh SOURCE OUTPUT FEDORA_CONFIG}")
output_dir=$(realpath -m "${2:?}")
base_config=$(realpath "${3:?}")
lock="$repo_root/config/kernel/rpi-7.2.json"
field() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): d=d[k]
print(d)' "$lock" "$1"; }
[ "$(sha256sum "$base_config" | cut -d' ' -f1)" = "$(field base_config.sha256)" ] ||
  { echo 'Fedora kernel configuration differs from the source lock' >&2; exit 1; }
export ARCH=arm64
if [ -z "${CROSS_COMPILE+x}" ]; then
  CROSS_COMPILE=
  [ "$(uname -m)" = aarch64 ] || CROSS_COMPILE=aarch64-linux-gnu-
fi
export CROSS_COMPILE
mkdir -p "$output_dir"
pi_base="$output_dir/.luma-pi-base"
rm -rf "$pi_base"
make -s -C "$source_dir" O="$pi_base" "$(field pi_defconfig)"
merge=(python3 "$repo_root/scripts/kernel/merge-rpi-config.py")
common=(--fedora "$base_config" --pi "$pi_base/.config" --fragment "$repo_root/config/kernel/rpi.config")
"${merge[@]}" merge "${common[@]}" --output "$output_dir/.config"
make -s -C "$source_dir" O="$output_dir" olddefconfig
"${merge[@]}" check "${common[@]}" --final "$output_dir/.config" \
  --exceptions "$repo_root/config/kernel/rpi-config-exceptions.txt" \
  --report "$output_dir/luma-config-exceptions.txt"
cp "$pi_base/.config" "$output_dir/raspberrypi-$(field pi_defconfig).config"
rm -rf "$pi_base"
release=$(make -s -C "$source_dir" O="$output_dir" kernelrelease)
[ "$release" = "$(field release)" ] ||
  { printf 'kernel release differs: %s != %s\n' "$release" "$(field release)" >&2; exit 1; }
printf 'Configured %s\n' "$release"
