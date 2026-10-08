#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Sign and publish the Depot catalog authored in this repository.
#
#   publish-repo-catalog.sh [--dry-run]
#
# Stopgap until Hub serves catalog snapshots (ADR-028 section 4.1). The
# listings are the reviewed inputs in src/luma-installer/data:
# depot-catalog.json (schema 3, also published as catalog-3.json),
# depot-first-party.json, depot-verified.json and depot-channels.json, plus the
# generated depot-listings.json (enrich-listings.py, whose pictures must already
# be under $DEPOT_ROOT/site/media/listings), combined by generate-depot-seed.py
# into schema 4. Nothing here bypasses the signing job:
# sign-catalog.py --from-dir applies the same validation as a Hub snapshot,
# signs with the catalog key and verifies the signature before anything is
# written under $DEPOT_ROOT/site/catalog, then sync-remote.sh --catalog uploads
# signatures before documents.
#
# Devices refuse a catalog older than the one they verified, so a published
# document is dated when it is made. It is only re-signed when the listings
# changed; an unchanged run publishes nothing.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"

dry_run=0
[ "${1:-}" = --dry-run ] && dry_run=1

work=$(mktemp -d "$DEPOT_ROOT/work/catalog.XXXXXX")
trap 'rm -rf "$work"' EXIT
data="$depot_repo_root/src/luma-installer/data"
published="$DEPOT_ROOT/site/catalog/catalog-4.json"

now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python3 "$depot_repo_root/src/luma-installer/tools/generate-depot-seed.py" \
  --generated-at "$now" "$work/catalog-4.json" >/dev/null
install -m 0644 "$data/depot-catalog.json" "$work/catalog-3.json"

if [ -f "$published" ] && python3 - "$published" "$work/catalog-4.json" <<'PY'
import json, sys
old, new = (json.load(open(path)) for path in sys.argv[1:])
old.pop('generated_at', None); new.pop('generated_at', None)
sys.exit(0 if old == new else 1)
PY
then
  # Keep the published schema 4 bytes so the signer sees no change.
  install -m 0644 "$published" "$work/catalog-4.json"
fi

depot_log "catalog: $(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["applications"]))' "$work/catalog-4.json") applications"
[ "$dry_run" = 1 ] && { depot_log "dry run: not signed"; exit 0; }

depot_in_tools python3 scripts/depot/sign-catalog.py \
  --from-dir "$work" \
  --token-file "$DEPOT_KEYS/secrets/hub-catalog-token" \
  --key "$DEPOT_KEYS/minisign/luma-depot-catalog.key" \
  --public-key "$DEPOT_KEYS/minisign/luma-depot-catalog.pub" \
  --site "$DEPOT_ROOT/site"

# The firmware block list (org.projectluma.firmware-blocklist/v1, read by
# luma_installer/depot_firmware.py) is signed with the same catalog key and
# served beside the catalog. It is re-signed only when its bytes change; bump
# generated_at with every change, since Depot keeps the newest verified copy.
blocklist="$data/firmware-blocklist.json"
served="$DEPOT_ROOT/site/catalog/firmware-blocklist.json"
if [ -f "$blocklist" ] && { [ ! -f "$served.minisig" ] || ! cmp -s "$blocklist" "$served"; }; then
  install -m 0644 "$blocklist" "$work/firmware-blocklist.json"
  generated=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["schema"] == "org.projectluma.firmware-blocklist/v1" and isinstance(d["entries"], list); print(d["generated_at"])' "$blocklist")
  depot_in_tools sh -c 'minisign -S -s "$1" -m "$2" -x "$2.minisig" -t "$3" </dev/null >/dev/null &&
    minisign -V -q -p "$4" -m "$2" -x "$2.minisig"' _ \
    "$DEPOT_KEYS/minisign/luma-depot-catalog.key" "$work/firmware-blocklist.json" \
    "firmware-blocklist generated $generated" "$DEPOT_KEYS/minisign/luma-depot-catalog.pub"
  install -d -m 0755 "$(dirname -- "$served")"
  install -m 0644 "$work/firmware-blocklist.json.minisig" "$served.minisig.next"
  install -m 0644 "$work/firmware-blocklist.json" "$served.next"
  mv -f "$served.next" "$served" && mv -f "$served.minisig.next" "$served.minisig"
  depot_log "signed firmware-blocklist.json (generated $generated)"
fi

# Listing pictures first: the catalogue names them by digest, and the signer's
# quality gate has already checked each one is in the site with that digest.
bash "$(dirname -- "$0")/sync-remote.sh" --media
bash "$(dirname -- "$0")/sync-remote.sh" --catalog
