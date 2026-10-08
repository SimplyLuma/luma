#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

usage() {
  printf 'usage: %s --verify-only|--execute KERNEL INITRD CMDLINE_FILE\n' "$0" >&2
  exit 2
}

[ "$#" -eq 4 ] || usage
mode=$1
case "$mode" in
  --verify-only|--execute) : ;;
  *) usage ;;
esac

[ "$(id -u)" -eq 0 ] || {
  printf 'error: installer handoff must run as root\n' >&2
  exit 1
}

kernel=$(realpath "$2")
initrd=$(realpath "$3")
cmdline_file=$(realpath "$4")
for path in "$kernel" "$initrd" "$cmdline_file"; do
  [ -f "$path" ] || {
    printf 'error: handoff input is missing: %s\n' "$path" >&2
    exit 1
  }
done

cmdline=$(tr '\n' ' ' < "$cmdline_file")
[ -n "$cmdline" ] || {
  printf 'error: installer kernel command line is empty\n' >&2
  exit 1
}

kexec --load "$kernel" --initrd="$initrd" --command-line="$cmdline"

if [ "$mode" = '--verify-only' ]; then
  kexec --unload
  printf 'kexec_validation=passed\n'
  exit 0
fi

sync
systemctl kexec
