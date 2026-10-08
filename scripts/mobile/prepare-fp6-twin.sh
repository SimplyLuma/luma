#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-twin/inputs.env"

build_dir=${LUMA_TWIN_BUILD_DIR:-$repo_root/build/mobile/fp6-twin}
downloads_dir="$build_dir/downloads"
base_image="$downloads_dir/$FEDORA_CLOUD_IMAGE"
disk="$build_dir/luma-fp6-twin.qcow2"
seed="$build_dir/luma-fp6-twin-seed.iso"
ssh_key="$build_dir/luma-fp6-twin-ed25519"
user_data="$build_dir/user-data"
meta_data="$build_dir/meta-data"
manifest="$build_dir/manifest.txt"
expected="$FEDORA_CLOUD_SHA256  $base_image"

for tool in curl qemu-img ssh-keygen; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required FP6 twin preparation tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    printf 'error: sha256sum or shasum is required\n' >&2
    exit 1
  fi
}

mkdir -p "$downloads_dir"

if [ ! -f "$base_image" ]; then
  partial="$base_image.partial"
  if [ -e "$partial" ]; then
    printf 'error: incomplete prior download exists: %s\n' "$partial" >&2
    printf 'Remove that one partial file after confirming no download is active.\n' >&2
    exit 1
  fi
  curl --fail --location --proto '=https' --tlsv1.2 \
    --output "$partial" "$FEDORA_CLOUD_URL"
  mv "$partial" "$base_image"
fi

actual=$(sha256_file "$base_image")
if [ "$actual" != "$FEDORA_CLOUD_SHA256" ]; then
  printf 'error: Fedora Cloud image checksum mismatch\n' >&2
  printf 'expected: %s\nactual:   %s\n' "$FEDORA_CLOUD_SHA256" "$actual" >&2
  exit 1
fi

if [ ! -f "$ssh_key" ]; then
  ssh-keygen -q -t ed25519 -N '' -C luma-fp6-twin -f "$ssh_key"
fi

public_key=$(<"$ssh_key.pub")
sed "s|__LUMA_TWIN_SSH_PUBLIC_KEY__|$public_key|" \
  "$repo_root/config/mobile/fp6-twin/user-data.in" >"$user_data"
install -m 0644 "$repo_root/config/mobile/fp6-twin/meta-data" "$meta_data"

if command -v cloud-localds >/dev/null 2>&1; then
  cloud-localds "$seed.new" "$user_data" "$meta_data"
elif command -v xorriso >/dev/null 2>&1; then
  xorriso -as mkisofs -quiet -output "$seed.new" -volid cidata \
    -joliet -rock "$user_data" "$meta_data"
elif command -v genisoimage >/dev/null 2>&1; then
  genisoimage -quiet -output "$seed.new" -volid cidata \
    -joliet -rock "$user_data" "$meta_data"
else
  printf 'error: cloud-localds, xorriso, or genisoimage is required\n' >&2
  exit 1
fi
mv "$seed.new" "$seed"

if [ ! -f "$disk" ]; then
  qemu-img create -q -f qcow2 -F qcow2 -b "$base_image" "$disk"
fi

{
  printf 'LUMA_TWIN_MANIFEST_VERSION=1\n'
  printf 'FEDORA_CLOUD_RELEASE=%s\n' "$FEDORA_CLOUD_RELEASE"
  printf 'FEDORA_CLOUD_COMPOSE=%s\n' "$FEDORA_CLOUD_COMPOSE"
  printf 'FEDORA_CLOUD_IMAGE=%s\n' "$FEDORA_CLOUD_IMAGE"
  printf 'FEDORA_CLOUD_SHA256=%s\n' "$FEDORA_CLOUD_SHA256"
  printf 'FEDORA_CLOUD_VERIFICATION=%s\n' "$expected"
  printf 'ROOTFS_CONTRACT=mobile-v1\n'
  printf 'HARDWARE_BACKEND=virtual\n'
  printf 'KERNEL_CONTRACT=qemu-virt\n'
  printf 'USER_DATA_SHA256=%s\n' "$(sha256_file "$user_data")"
  printf 'SEED_SHA256=%s\n' "$(sha256_file "$seed")"
  printf 'QEMU_IMG_VERSION=%s\n' "$(qemu-img --version | head -n 1)"
} >"$manifest"

printf 'FP6 behavioral twin prepared:\n'
printf '  disk:     %s\n' "$disk"
printf '  seed:     %s\n' "$seed"
printf '  SSH key:  %s\n' "$ssh_key"
printf '  manifest: %s\n' "$manifest"
