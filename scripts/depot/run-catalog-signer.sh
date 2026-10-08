#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# One run of the catalog signing job (started by luma-depot-catalog.timer).
#
# Hub service token: $DEPOT_KEYS/secrets/hub-catalog-token (mode 0600).
# Until it exists the job reports "not configured" and exits 0, so the timer
# can be enabled before Hub serves snapshots. Uploads when hosting is
# configured ($DEPOT_KEYS/secrets/spaces.env, or dl-origin.env for the
# dl.simplyluma.com origin); otherwise the signed files stay in
# $DEPOT_ROOT/site/catalog for serve-remote.sh.
#
# Until Hub serves snapshots, the catalog is authored in this repository
# (src/luma-installer/data) and signed with --from-dir; see
# docs/depot/publishing-runbook.md section 6.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"

podman start "$DEPOT_TOOLS_CONTAINER" >/dev/null
upload=()
[ -f "$DEPOT_KEYS/secrets/spaces.env" ] &&
  upload=(--upload "DEPOT_ROOT=$DEPOT_ROOT DEPOT_KEYS=$DEPOT_KEYS DEPOT_INSIDE_TOOLS=1 bash scripts/depot/sync-remote.sh --catalog")

set +e
depot_in_tools python3 scripts/depot/sign-catalog.py \
  --token-file "$DEPOT_KEYS/secrets/hub-catalog-token" \
  --key "$DEPOT_KEYS/minisign/luma-depot-catalog.key" \
  --public-key "$DEPOT_KEYS/minisign/luma-depot-catalog.pub" \
  --site "$DEPOT_ROOT/site" "${upload[@]}"
status=$?
set -e
[ "$status" = 2 ] && exit 0
# The origin upload runs on the host so its key never enters the container;
# rsync sends nothing when the signed files are unchanged.
if [ "$status" = 0 ] && [ ! -f "$DEPOT_KEYS/secrets/spaces.env" ] && [ -f "$DEPOT_KEYS/secrets/dl-origin.env" ] &&
   [ -f "$DEPOT_ROOT/site/catalog/catalog-4.json.minisig" ]; then
  bash "$(dirname -- "$0")/sync-remote.sh" --catalog
fi
exit "$status"
