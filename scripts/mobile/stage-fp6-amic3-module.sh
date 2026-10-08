#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Stage the exact AMIC3 WCD9378 SoundWire module for the next RAM boot. This
# script runs on the FP6, makes a recoverable exact-hash backup, and never
# loads a module, restarts a service, reboots, or accesses a partition.

set -euo pipefail
umask 022

candidate=${1:?usage: stage-fp6-amic3-module.sh CANDIDATE_KO}
kernel_release=7.1.2
old_sha=25afa7124d8ee1e8160d72a9478b1ab4336ed90a157e5266722f94357c479c38
new_sha=10b7aaea18a2df37c1ac6ad195db47e9f89f7c7249080e7ef30a8e1221d761d9
destination=/lib/modules/$kernel_release/updates/luma-fp6-microphone/snd-soc-wcd9378-sdw.ko
state_dir=/var/lib/luma/fp6-microphone-amic3-v1
backup=$state_dir/original-snd-soc-wcd9378-sdw.ko

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

[ "$(id -u)" -eq 0 ] || die 'run as root on the FP6'
[ "$(tr -d '\000' </sys/firmware/devicetree/base/model)" = 'The Fairphone (Gen. 6)' ] || die 'device identity differs'
[ "$(uname -r)" = "$kernel_release" ] || die 'kernel release differs'
[ -f "$candidate" ] || die 'candidate module is missing'
[ -f "$destination" ] || die 'installed module is missing'
[ "$(hash "$candidate")" = "$new_sha" ] || die 'candidate module checksum differs'
[ "$(hash "$destination")" = "$old_sha" ] || die 'installed v3 module checksum differs'
[ ! -e "$state_dir" ] || die 'AMIC3 staging state already exists'

install -d -m 0755 "$state_dir"
install -m 0644 "$destination" "$backup"
[ "$(hash "$backup")" = "$old_sha" ] || die 'backup checksum differs'
install -m 0644 "$candidate" "$destination"
[ "$(hash "$destination")" = "$new_sha" ] || die 'installed checksum differs'
depmod -a "$kernel_release"
{
  printf 'LUMA_FP6_AMIC3_MODULE_STAGE_VERSION=1\n'
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'ORIGINAL_SHA256=%s\n' "$old_sha"
  printf 'INSTALLED_SHA256=%s\n' "$new_sha"
  printf 'MODULES_LOADED=false\n'
  printf 'SERVICES_RESTARTED=false\n'
  printf 'REBOOTED=false\n'
  printf 'PARTITIONS_WRITTEN=false\n'
} >"$state_dir/manifest.env"
chmod 0644 "$state_dir/manifest.env"
printf 'staged=%s\n' "$destination"
