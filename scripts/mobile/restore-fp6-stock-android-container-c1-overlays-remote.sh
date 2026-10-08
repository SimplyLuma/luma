#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Restore only the preserved C1 overlay mounts after a host reboot. The exact
# stock partition loop mounts must already have been restored read-only. This
# never starts Android and never exposes a loop or block device to C1.

set -Eeuo pipefail
umask 077

name=luma-stock-android-c1
state=/var/lib/luma/stock-android-container1
config=/var/lib/lxc/$name/config
manifest=$state/manifest.json
expected_config_sha=db477c4d10c1511d1444c91a093fe8c4cca3a506a263f5177b33c26b23c1ae6f
expected_manifest_sha=65a317645db47fb0dfa7b2b8f3ee9b923fa03a543ece43deb158c2e0c14ebfd8

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d' ' -f1; }

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] ||
  die 'device identity mismatch'
[[ $(uname -r) == 7.1.2-luma-fp-ims1 ]] || die 'unexpected kernel release'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not on slot b'
[[ $(lxc-info -P /var/lib/lxc -n "$name" -sH 2>/dev/null || printf STOPPED) == STOPPED ]] ||
  die 'container is active'
[[ -f $config && ! -L $config && $(hash "$config") == "$expected_config_sha" ]] ||
  die 'C1 config identity mismatch'
[[ -f $manifest && ! -L $manifest && $(hash "$manifest") == "$expected_manifest_sha" ]] ||
  die 'C1 manifest identity mismatch'

for partition in system vendor system_ext product odm; do
  lower=/var/tmp/luma-stock-qrel1695-$partition
  overlay=$state/overlays/$partition
  upper=$overlay/upper
  work=$overlay/work
  target=$state/mounts/$partition

  [[ -d $lower && ! -L $lower ]] || die "lower directory differs: $partition"
  [[ $(findmnt -rn -T "$lower" -o TARGET) == "$lower" ]] ||
    die "lower is not a mountpoint: $partition"
  findmnt -rn -T "$lower" -o OPTIONS | tr ',' '\n' | grep -qx ro ||
    die "lower is not read-only: $partition"
  for directory in "$upper" "$work" "$target"; do
    [[ -d $directory && ! -L $directory ]] ||
      die "overlay directory differs: $directory"
  done

  if mountpoint -q "$target"; then
    [[ $(findmnt -rn -T "$target" -o FSTYPE) == overlay ]] ||
      die "existing target is not overlay: $partition"
  else
    mount -t overlay overlay \
      -o "lowerdir=$lower,upperdir=$upper,workdir=$work,index=off,metacopy=off" \
      "$target"
  fi
  [[ $(findmnt -rn -T "$target" -o TARGET,FSTYPE) == "$target overlay" ]] ||
    die "overlay mount verification failed: $partition"
done

printf 'STOCK_ANDROID_C1_OVERLAYS_RESTORED=true\n'
printf 'OVERLAY_MOUNTS=5\n'
printf 'CONTAINER_STARTED=false\n'
printf 'BLOCK_DEVICES_EXPOSED=false\n'
