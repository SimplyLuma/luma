#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
image_source=${1:-$repo_root/build/android/prairie-windowing/x86_64/images}
image_target=${LUMA_WAYDROID_IMAGE_DIR:-/etc/waydroid-extra/images}
lock_file=/run/lock/luma-waydroid-image-deploy.lock

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  printf 'error: image deployment must run as root\n' >&2
  exit 1
fi

for file in system.img vendor.img; do
  test -r "$image_source/$file" || {
    printf 'error: missing coherent image %s\n' "$image_source/$file" >&2
    exit 1
  }
done
test -r "$image_source/SHA256SUMS" || {
  printf 'error: missing image manifest %s/SHA256SUMS\n' "$image_source" >&2
  exit 1
}

(
  cd "$image_source"
  sha256sum --check --strict SHA256SUMS
)

install -d -m 0755 "$(dirname -- "$lock_file")"
exec 9>"$lock_file"
flock -x 9

if systemctl is-active --quiet waydroid-container.service; then
  printf 'error: stop the Waydroid session and container before deploying images\n' >&2
  exit 1
fi

install -d -m 0755 "$image_target"
stage=$(mktemp -d "$image_target/.prairie-stage.XXXXXX")
backup=$(mktemp -d "$image_target/.prairie-previous.XXXXXX")
committed=0

rollback() {
  if [[ $committed -eq 0 ]]; then
    for file in system.img vendor.img; do
      if [[ -e $backup/$file ]]; then
        mv -f -- "$backup/$file" "$image_target/$file"
      elif [[ ! -e $stage/$file ]]; then
        rm -f -- "$image_target/$file"
      fi
    done
    waydroid init -f >/dev/null 2>&1 || true
  fi
  if [[ -d $stage ]]; then
    rm -f -- "$stage/system.img" "$stage/vendor.img"
    rmdir -- "$stage" 2>/dev/null || true
  fi
}
trap rollback EXIT

for file in system.img vendor.img; do
  install -m 0644 "$image_source/$file" "$stage/$file"
done
sync -f "$stage"

for file in system.img vendor.img; do
  if [[ -e $image_target/$file ]]; then
    mv -- "$image_target/$file" "$backup/$file"
  fi
done
for file in system.img vendor.img; do
  mv -- "$stage/$file" "$image_target/$file"
done

restorecon -RF "$image_target" 2>/dev/null || true
sync -f "$image_target"
waydroid init -f
committed=1
trap - EXIT
rmdir -- "$stage"

printf 'Installed coherent Prairie Waydroid images.\n'
printf 'Rollback pair retained at %s\n' "$backup"
