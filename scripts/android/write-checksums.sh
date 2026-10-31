#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Publish a complete checksum manifest without truncating a previous export.
luma_android_write_checksums() {
  local directory=$1 temporary
  shift
  temporary=$(mktemp "$directory/.luma-checksums-XXXXXX") || return
  if ! (
    set -o pipefail
    cd "$directory" || exit
    if [ "$#" -gt 0 ]; then
      sha256sum -- "$@"
    else
      find . -type f ! -name SHA256SUMS ! -name '.luma-checksums-*' \
        -exec sha256sum {} + | sed 's#  \./#  #' | sort
    fi
  ) >"$temporary"; then
    rm -f -- "$temporary"
    return 1
  fi
  chmod 0644 "$temporary" && mv -fT -- "$temporary" "$directory/SHA256SUMS"
}
