#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Make the luma-messages-bridges source archive: the helpers plus their pinned Go
# modules (go mod vendor, verified against go.sum), so the RPM builds offline.
# Needs Go 1.26 and network access for the module download; run in the RPM builder.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'usage: %s OUTPUT.tar.gz\n' "$0" >&2
  exit 2
fi
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output=$(realpath -m "$1")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

cp -R "$repo_root/src/luma-messages-bridges" "$work/luma-messages-bridges"
export GOTOOLCHAIN=local GOFLAGS=-mod=mod
for helper in gmessages whatsapp; do
  (cd "$work/luma-messages-bridges/$helper" && go mod verify >/dev/null && go mod vendor)
done
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
  -C "$work" -czf "$output" luma-messages-bridges
sha256sum "$output"
