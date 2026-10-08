#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Offline-only structure inspection for already verified postmarketOS images.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-control/inputs.env"

build_dir=${LUMA_FP6_PHYSICAL_BUILD_DIR:-$repo_root/build/mobile/fp6-physical}
artifact_dir="$build_dir/downloads/postmarketos-$FP6_CONTROL_BUILD"
boot_image=${FP6_CONTROL_BOOT_IMAGE%.xz}
rootfs_image=${FP6_CONTROL_ROOTFS_IMAGE%.xz}
raw_rootfs=${rootfs_image%.img}-raw.img
unpack_dir="$artifact_dir/boot-unpacked"
report="$build_dir/control-inspection.txt"
inspector_image=localhost/luma-fp6-control-inspector:fedora44

command -v podman >/dev/null 2>&1 || {
  printf 'error: podman is required for isolated control-image inspection\n' >&2
  exit 1
}
command -v guestfish >/dev/null 2>&1 || {
  printf 'error: guestfish is required for read-only 4096-byte GPT inspection\n' >&2
  exit 1
}

for required in "$artifact_dir/$boot_image" "$artifact_dir/$rootfs_image"; do
  [ -f "$required" ] || {
    printf 'error: decompressed verified control image is missing: %s\n' "$required" >&2
    printf 'Run prepare-fp6-control.sh, then decompress its two retained .xz files.\n' >&2
    exit 1
  }
done

podman build --quiet --tag "$inspector_image" \
  --file "$repo_root/config/mobile/fp6-control/Containerfile" \
  "$repo_root/config/mobile/fp6-control" >/dev/null

container_args=(
  --rm
  --network none
  --userns keep-id
  --user "$(id -u):$(id -g)"
  --security-opt label=disable
  --volume "$artifact_dir:/work"
  --workdir /work
)

if [ ! -f "$artifact_dir/$raw_rootfs" ]; then
  podman run "${container_args[@]}" "$inspector_image" \
    simg2img "$rootfs_image" "$raw_rootfs"
fi

mkdir -p "$unpack_dir"
if [ ! -f "$unpack_dir/kernel" ]; then
  podman run "${container_args[@]}" "$inspector_image" \
    unpack_bootimg --boot_img "$boot_image" --out /work/boot-unpacked >/dev/null
fi

{
  printf 'LUMA_FP6_CONTROL_INSPECTION_VERSION=1\n'
  printf 'SOURCE_BUILD=%s\n' "$FP6_CONTROL_BUILD"
  printf 'PHONE_ACCESSED=false\n'
  printf 'INSTALL_AUTHORIZED=false\n'
  printf '\n[formats]\n'
  podman run "${container_args[@]}" "$inspector_image" \
    file "$boot_image" "$rootfs_image" "$raw_rootfs"
  printf '\n[rootfs-layout]\n'
  guestfish --ro --blocksize=4096 -a "$artifact_dir/$raw_rootfs" \
    run : list-partitions : list-filesystems
  printf '\n[rootfs-os-release]\n'
  guestfish --ro --blocksize=4096 -a "$artifact_dir/$raw_rootfs" \
    run : mount-ro /dev/sda2 / : cat /etc/os-release
  printf '\n[rootfs-kernel-modules]\n'
  guestfish --ro --blocksize=4096 -a "$artifact_dir/$raw_rootfs" \
    run : mount-ro /dev/sda2 / : ls /lib/modules
  printf '\n[rootfs-package-world]\n'
  guestfish --ro --blocksize=4096 -a "$artifact_dir/$raw_rootfs" \
    run : mount-ro /dev/sda2 / : cat /etc/apk/world
  printf '\n[boot-components]\n'
  find "$unpack_dir" -maxdepth 1 -type f -print | sort | while IFS= read -r component; do
    component_type=$(podman run "${container_args[@]}" "$inspector_image" \
      file -b "/work/boot-unpacked/$(basename "$component")")
    printf '%s sha256=%s bytes=%s type=%s\n' \
      "$(basename "$component")" \
      "$(sha256sum "$component" | awk '{print $1}')" \
      "$(wc -c <"$component" | tr -d ' ')" \
      "$component_type"
  done
} >"$report"

printf 'FP6 control image inspected offline: %s\n' "$report"
printf 'No phone or USB device was exposed to the inspector.\n'
