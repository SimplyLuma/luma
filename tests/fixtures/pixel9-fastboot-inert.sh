#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Host-only stand-in for the Pixel 9 temporary-boot helper's final inert
# preflight. It models read-only bootloader queries, records an exact boot
# request, and exits before any transport operation can be accepted.

set -euo pipefail
umask 077

log=${LUMA_PIXEL9_INERT_FASTBOOT_LOG:?inert fastboot log path is required}
expected_sha=${LUMA_PIXEL9_INERT_EXPECTED_SHA256:?expected inert candidate digest is required}

case ${1:-} in
  devices)
    printf 'inert-tokay\tfastboot\n'
    ;;
  getvar)
    case ${2:-} in
      product) value=tokay ;;
      unlocked) value=yes ;;
      is-userspace) value=no ;;
      secure) value=yes ;;
      battery-soc-ok) value=yes ;;
      slot-successful:a|slot-successful:b) value=yes ;;
      slot-unbootable:a|slot-unbootable:b) value=no ;;
      current-slot) value=a ;;
      max-download-size) value=0x0f900000 ;;
      *) printf 'unexpected inert getvar: %s\n' "${2:-absent}" >&2; exit 64 ;;
    esac
    printf '%s: %s\n' "$2" "$value" >&2
    ;;
  boot)
    [ "$#" -eq 2 ] || { printf 'inert boot received unexpected arguments\n' >&2; exit 64; }
    [ -f "$2" ] || { printf 'inert boot candidate is absent\n' >&2; exit 64; }
    actual_sha=$(sha256sum "$2" | awk '{print $1}')
    [ "$actual_sha" = "$expected_sha" ] || { printf 'inert boot digest differs\n' >&2; exit 65; }
    {
      printf 'LUMA_PIXEL9_INERT_FASTBOOT_VERSION=1\n'
      printf 'DEVICE_MODEL=tokay\n'
      printf 'CANDIDATE_BYTES=%s\n' "$(stat -c %s "$2")"
      printf 'CANDIDATE_SHA256=%s\n' "$actual_sha"
      printf 'RAM_BOOT_REQUEST_REACHED=true\n'
      printf 'TRANSPORT_OPERATION_ISSUED=false\n'
      printf 'PARTITIONS_FLASHED=false\n'
      printf 'SLOTS_CHANGED=false\n'
      printf 'CUSTOM_KEY_INSTALLED=false\n'
      printf 'FLASH_AUTHORIZED=false\n'
    } >"$log"
    exit 86
    ;;
  *)
    printf 'unexpected inert fastboot operation: %s\n' "${1:-absent}" >&2
    exit 64
    ;;
esac
