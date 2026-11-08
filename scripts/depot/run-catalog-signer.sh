#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Publish Depot's catalog from the Hub, once.
#
#   run-catalog-signer.sh [--dry-run]
#
# The Hub is the only source of Depot listings (admin.simplyluma.com, Depot >
# Apps); nothing in this repository authors one. luma-depot-review.service runs
# the same signer every minute (tools/depot-review, depot_review/catalog4.py);
# this is the manual run. sign-catalog.py fetches the Hub's schema 4 and schema 3
# snapshots with the Hub service token, copies listing pictures the site lacks
# from the Hub, validates both with the rules installed Depots run, signs them
# with the catalog key and writes them under $DEPOT_ROOT/site/catalog; pictures
# upload before documents. An unchanged catalogue publishes nothing. The
# firmware block list (data/firmware-blocklist.json, not a listing) is signed
# here too when its bytes change. Holds the Depot publication lock.
#
# Hub token: $DEPOT_HUB_TOKEN (default $DEPOT_KEYS/secrets/hub-pipeline.token,
# mode 0600). Upload: sync-remote.sh (dl-origin.env or spaces.env).
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"

dry_run=0
[ "${1:-}" = --dry-run ] && dry_run=1
token=${DEPOT_HUB_TOKEN:-$DEPOT_KEYS/secrets/hub-pipeline.token}
lock=${DEPOT_PUBLICATION_LOCK:-/srv/luma-build/depot-publication.lock}
here=$(cd -- "$(dirname -- "$0")" && pwd)

exec 9>>"$lock"
flock 9

upload=()
[ "$dry_run" = 1 ] ||
  upload=(--upload "bash '$here/sync-remote.sh' --media && bash '$here/sync-remote.sh' --catalog")
site="$DEPOT_ROOT/site"
if [ "$dry_run" = 1 ]; then
  # Signs into a scratch site; pictures copied from the Hub stay in the real one.
  site=$(mktemp -d); trap 'rm -rf "$site"' EXIT
  cp -a "$DEPOT_ROOT/site/catalog" "$site/" 2>/dev/null || true
  mkdir -p "$DEPOT_ROOT/site/media" && ln -s "$DEPOT_ROOT/site/media" "$site/media"
fi

# On the host: it needs the Hub, and the origin key never enters a container.
python3 -I "$here/sign-catalog.py" \
  --token-file "$token" \
  --key "$DEPOT_KEYS/minisign/luma-depot-catalog.key" \
  --public-key "$DEPOT_KEYS/minisign/luma-depot-catalog.pub" \
  --site "$site" "${upload[@]}"
[ "$dry_run" = 1 ] && exit 0

# The firmware block list (org.projectluma.firmware-blocklist/v1, read by
# luma_installer/depot_firmware.py), signed with the same key and served beside
# the catalog. Bump generated_at with every change: Depot keeps the newest
# verified copy.
blocklist="$depot_repo_root/src/luma-installer/data/firmware-blocklist.json"
served="$DEPOT_ROOT/site/catalog/firmware-blocklist.json"
if [ -f "$blocklist" ] && { [ ! -f "$served.minisig" ] || ! cmp -s "$blocklist" "$served"; }; then
  work=$(mktemp -d)
  install -m 0644 "$blocklist" "$work/firmware-blocklist.json"
  generated=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["schema"] == "org.projectluma.firmware-blocklist/v1" and isinstance(d["entries"], list); print(d["generated_at"])' "$blocklist")
  minisign -S -s "$DEPOT_KEYS/minisign/luma-depot-catalog.key" -m "$work/firmware-blocklist.json" \
    -x "$work/firmware-blocklist.json.minisig" -t "firmware-blocklist generated $generated" </dev/null >/dev/null
  minisign -V -q -p "$DEPOT_KEYS/minisign/luma-depot-catalog.pub" -m "$work/firmware-blocklist.json" \
    -x "$work/firmware-blocklist.json.minisig"
  install -d -m 0755 "$(dirname -- "$served")"
  install -m 0644 "$work/firmware-blocklist.json.minisig" "$served.minisig.next"
  install -m 0644 "$work/firmware-blocklist.json" "$served.next"
  mv -f "$served.next" "$served" && mv -f "$served.minisig.next" "$served.minisig"
  rm -rf "$work"
  depot_log "signed firmware-blocklist.json (generated $generated)"
  bash "$here/sync-remote.sh" --catalog
fi
