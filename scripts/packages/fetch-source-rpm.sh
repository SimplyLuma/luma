#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Fetch an exact upstream source body; never accept a same-name replacement.
set -euo pipefail
[ "$#" -ge 3 ] || { printf 'usage: %s OUTPUT SHA256 URL...\n' "$0" >&2; exit 2; }
destination=$1 expected=$2
shift 2
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || { printf 'error: invalid source SHA256\n' >&2; exit 2; }
verify() { printf '%s  %s\n' "$expected" "$1" | sha256sum --check --status; }
if [ -f "$destination" ]; then
  verify "$destination" || { printf 'error: cached source checksum mismatch: %s\n' "$destination" >&2; exit 1; }
  exit
fi
mkdir -p "$(dirname "$destination")"
temporary=$(mktemp "$destination.download.XXXXXX")
trap 'rm -f "$temporary"' EXIT
for url in "$@"; do
  if curl --fail --location --silent --show-error --retry 2 --connect-timeout 20 \
      --max-time 300 --proto '=https' --output "$temporary" "$url"; then
    verify "$temporary" || { printf 'error: downloaded source checksum mismatch: %s\n' "$url" >&2; exit 1; }
    chmod 0644 "$temporary"
    mv "$temporary" "$destination"
    exit
  fi
done
printf 'error: exact source could not be fetched: %s\n' "$destination" >&2
exit 1
