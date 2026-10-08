#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'usage: %s /absolute/path/to/writable-overlay.qcow2\n' "$0" >&2
  exit 2
fi

overlay=$(realpath "$1")

for tool in guestfish sudo; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required overlay-provisioning tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ ! -f "$overlay" ]; then
  printf 'error: writable VM overlay does not exist: %s\n' "$overlay" >&2
  exit 1
fi

deployments=$(sudo env LIBGUESTFS_BACKEND=direct guestfish \
  --ro \
  -a "$overlay" \
  -m /dev/sda3 \
  ls /ostree/deploy/fedora/deploy)
deployment_count=$(printf '%s\n' "$deployments" |
  awk '/^[[:xdigit:]]{64}\.[[:digit:]]+$/ { count += 1 } END { print count + 0 }')
if [ "$deployment_count" -eq 0 ]; then
  printf 'error: no Fedora OSTree deployments found\n' >&2
  exit 1
fi

printf '%s\n' "$deployments" |
  awk '/^[[:xdigit:]]{64}\.[[:digit:]]+$/ { print }' |
  while IFS= read -r deployment_name; do
    deployment="/ostree/deploy/fedora/deploy/$deployment_name"
    while IFS='|' read -r wants_dir unit; do
      link_path="$deployment/etc/systemd/system/$wants_dir/$unit"
      link_exists=$(sudo env LIBGUESTFS_BACKEND=direct guestfish \
        --ro \
        -a "$overlay" \
        -m /dev/sda3 \
        is-symlink "$link_path")
      if [ "$link_exists" = true ]; then
        sudo env LIBGUESTFS_BACKEND=direct guestfish \
          --rw \
          -a "$overlay" \
          -m /dev/sda3 \
          rm "$link_path"
      fi
    done <<'EOF'
multi-user.target.wants|sshd.service
getty.target.wants|serial-getty@ttyS0.service
EOF
  done

printf 'Disabled lab SSH and serial recovery in %s writable Track A deployment(s): %s\n' \
  "$deployment_count" "$overlay"
