#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Create the Luma remote's OSTree repository (idempotent).
#
# archive mode is what static HTTP hosting serves; every object is a separate
# compressed file, so the CDN can cache objects forever and only the summary
# and its signature change on publication.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"

if [ -f "$depot_repo/config" ]; then
  depot_log "remote repository exists: $depot_repo"
  exit 0
fi
depot_in_signer ostree init --mode=archive --repo="$depot_repo"
# Keep a publish from failing on a nearly full work volume; the pipeline
# measures space itself before large steps.
depot_in_signer ostree config --repo="$depot_repo" set core.min-free-space-size 500MB
depot_log "created $depot_repo"
