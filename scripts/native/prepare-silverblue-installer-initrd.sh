#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

usage() {
  printf 'usage: %s SOURCE_INITRD OUTPUT_INITRD NM_KEYFILE KICKSTART [DRIVER_OVERLAY [STAGE2_IMAGE]]\n' \
    "$0" >&2
  exit 2
}

case "$#" in
  4|5|6) : ;;
  *) usage ;;
esac
[ "$(id -u)" -eq 0 ] || {
  printf 'error: this tool must run as root so initramfs ownership is correct\n' >&2
  exit 1
}

source_initrd=$(realpath "$1")
output_initrd=$2
nm_keyfile=$(realpath "$3")
kickstart=$(realpath "$4")
driver_overlay=
stage2_image=
if [ "$#" -ge 5 ]; then
  driver_overlay=$(realpath "$5")
  [ -d "$driver_overlay" ] || {
    printf 'error: driver overlay is not a directory: %s\n' "$driver_overlay" >&2
    exit 1
  }
fi
if [ "$#" -eq 6 ]; then
  stage2_image=$(realpath "$6")
  [ -f "$stage2_image" ] || {
    printf 'error: installer stage 2 is not a file: %s\n' "$stage2_image" >&2
    exit 1
  }
fi

for path in "$source_initrd" "$nm_keyfile" "$kickstart"; do
  [ -f "$path" ] || {
    printf 'error: required input is missing: %s\n' "$path" >&2
    exit 1
  }
done

for tool in cp cpio find install realpath sha256sum sort xz; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

case "$output_initrd" in
  /*) : ;;
  *) output_initrd="$PWD/$output_initrd" ;;
esac

work_dir=$(mktemp -d /var/tmp/luma-silverblue-initrd.XXXXXX)
cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT INT TERM

mkdir -p "$work_dir/root"
(
  cd "$work_dir/root"
  xz -dc "$source_initrd" | cpio --quiet -idmu --no-absolute-filenames
)

install -D -m 0600 "$nm_keyfile" \
  "$work_dir/root/etc/NetworkManager/system-connections/luma-installer.nmconnection"
install -D -m 0600 "$kickstart" "$work_dir/root/luma.ks"
if [ -n "$driver_overlay" ]; then
  cp -a "$driver_overlay/." "$work_dir/root/"
fi
if [ -n "$stage2_image" ]; then
  install -m 0600 "$stage2_image" "$work_dir/root/luma-install.img"
fi

mkdir -p "$(dirname -- "$output_initrd")"
(
  cd "$work_dir/root"
  find . -print0 | sort -z | \
    cpio --quiet --null -o --format=newc --owner=0:0 | \
    if [ -n "$stage2_image" ]; then
      # install.img is already compressed SquashFS.  Re-processing it with an
      # extreme preset only burns time and memory without reducing its size.
      xz --check=crc32 -T0 -1
    else
      xz --check=crc32 -9e
    fi > "$output_initrd"
)
chmod 0600 "$output_initrd"

xz -t "$output_initrd"
archive_list="$work_dir/archive.list"
xz -dc "$output_initrd" | cpio --quiet -it > "$archive_list"
for required_path in \
  etc/NetworkManager/system-connections/luma-installer.nmconnection \
  luma.ks; do
  grep -Fxq "$required_path" "$archive_list" || {
    printf 'error: prepared initramfs is missing %s\n' "$required_path" >&2
    exit 1
  }
done
if [ -n "$stage2_image" ]; then
  grep -Fxq luma-install.img "$archive_list" || {
    printf 'error: prepared initramfs is missing luma-install.img\n' >&2
    exit 1
  }
  extracted_stage2_hash=$(
    xz -dc "$output_initrd" | cpio --quiet -i --to-stdout luma-install.img | sha256sum | awk '{print $1}'
  )
  source_stage2_hash=$(sha256sum "$stage2_image" | awk '{print $1}')
  [ "$extracted_stage2_hash" = "$source_stage2_hash" ] || {
    printf 'error: embedded stage 2 checksum mismatch\n' >&2
    exit 1
  }
fi
if [ -n "$driver_overlay" ]; then
  while IFS= read -r -d '' overlay_file; do
    relative_path=${overlay_file#"$driver_overlay"/}
    grep -Fxq "$relative_path" "$archive_list" || {
      printf 'error: prepared initramfs is missing driver file %s\n' \
        "$relative_path" >&2
      exit 1
    }
  done < <(find "$driver_overlay" -type f -print0)
fi
printf 'prepared_initrd=%s\n' "$output_initrd"
printf 'sha256=%s\n' "$(sha256sum "$output_initrd" | awk '{print $1}')"
