#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Upload the Luma remote to where dl.simplyluma.com serves it.
#
#   sync-remote.sh [--catalog] [--media] [--prune]
#
# Two hosting backends, chosen by which configuration exists:
#
# * $DEPOT_KEYS/secrets/spaces.env (mode 0600): object storage (DigitalOcean
#   Spaces, S3 API) defining SPACES_ACCESS_KEY, SPACES_SECRET_KEY,
#   SPACES_REGION (e.g. nyc3) and SPACES_BUCKET, scoped to that one bucket.
# * $DEPOT_KEYS/secrets/dl-origin.env (mode 0600): the dl.simplyluma.com origin
#   server over rsync+SSH, defining DL_RSYNC_DEST (user@host:, an account whose
#   key is restricted with rrsync to the web root) and DL_SSH_KEY. The origin's
#   nginx serves repo/objects, deltas and delta-indexes as immutable and the
#   rest of repo/ as no-cache.
#
# Order matters for clients mid-update. Content-addressed objects and deltas
# go first and are never overwritten; then refs, delta indexes and the
# descriptor files; the summary and its signature last, so a summary is never
# visible before everything it names. --catalog uploads only catalog/.
# --media uploads only media/listings/ (listing icons and screenshots, named
# by their digest, so never overwritten); run it before --catalog so a listing
# never names a picture that is not there yet.
# --prune deletes files the local repository no longer has (pruned commits);
# run it separately, well after the summary that stopped naming them.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"

mode=all
case "${1:-}" in --catalog) mode=catalog ;; --media) mode=media ;; --prune) mode=prune ;; "") ;; *) depot_die "unknown option $1" ;; esac

secrets="$DEPOT_KEYS/secrets/spaces.env"
origin="$DEPOT_KEYS/secrets/dl-origin.env"
if [ ! -f "$secrets" ] && [ -f "$origin" ]; then
  [ "$(stat -c %a "$origin")" = 600 ] || depot_die "$origin must be mode 0600"
  # Runs on the host: the publish key never enters the tools container.
  DL_RSYNC_DEST= DL_SSH_KEY=
  . "$origin"
  [ -n "$DL_RSYNC_DEST" ] && [ -r "$DL_SSH_KEY" ] || depot_die "$origin must define DL_RSYNC_DEST and a readable DL_SSH_KEY"
  rs=(rsync --recursive --links --times --omit-dir-times --chmod=D0755,F0644 --no-owner --no-group
      --partial-dir=.rsync-partial --timeout=600 --mkpath
      -e "ssh -i $DL_SSH_KEY -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=yes")
  repo=$depot_repo site="$DEPOT_ROOT/site" dest=${DL_RSYNC_DEST%:}:
  case "$mode" in
    catalog)
      # Signatures first, then the documents in one transfer renamed into place.
      "${rs[@]}" --include 'catalog-*.json.minisig' --include 'firmware-blocklist.json.minisig' --exclude '*' "$site/catalog/" "${dest}catalog/"
      "${rs[@]}" --delay-updates --include 'catalog-*.json' --include 'firmware-blocklist.json' --exclude '*' "$site/catalog/" "${dest}catalog/"
      ;;
    media)
      [ -d "$site/media/listings" ] && "${rs[@]}" --ignore-existing "$site/media/listings/" "${dest}media/listings/"
      ;;
    prune)
      "${rs[@]}" --delete-after --exclude 'tmp/' --exclude '.lock' "$repo/" "${dest}repo/"
      ;;
    all)
      # 1. immutable content, never overwritten
      for dir in objects deltas; do
        [ -d "$repo/$dir" ] && "${rs[@]}" --ignore-existing "$repo/$dir/" "${dest}repo/$dir/"
      done
      # 2. everything that points at content, except the summary
      "${rs[@]}" --delay-updates --exclude '/objects/' --exclude '/deltas/' --exclude '/tmp/' \
        --exclude '/.lock' --exclude '/summary' --exclude '/summary.sig' --exclude '/summary.idx' \
        --exclude '/summary.idx.sig' --exclude '/state/' "$repo/" "${dest}repo/"
      "${rs[@]}" --delay-updates --exclude '/catalog/' "$site/" "$dest"
      # 3. the summaries and their signatures, in one transfer renamed into place at its end
      "${rs[@]}" --delay-updates --include '/summary' --include '/summary.sig' --include '/summary.idx' \
        --include '/summary.idx.sig' --exclude '*' "$repo/" "${dest}repo/"
      ;;
  esac
  echo "synced to ${DL_RSYNC_DEST%%:*}"
  exit 0
fi
[ -f "$secrets" ] || depot_die "hosting is not configured: $secrets or $origin is missing"
[ "$(stat -c %a "$secrets")" = 600 ] || depot_die "$secrets must be mode 0600"

depot_in_tools bash -s -- "$secrets" "$depot_repo" "$DEPOT_ROOT/site" "$mode" <<'SH'
set -euo pipefail
secrets=$1 repo=$2 site=$3 mode=$4
set -a; . "$secrets"; set +a
export RCLONE_CONFIG_SPACES_TYPE=s3 RCLONE_CONFIG_SPACES_PROVIDER=DigitalOcean \
  RCLONE_CONFIG_SPACES_ACCESS_KEY_ID="$SPACES_ACCESS_KEY" \
  RCLONE_CONFIG_SPACES_SECRET_ACCESS_KEY="$SPACES_SECRET_KEY" \
  RCLONE_CONFIG_SPACES_ENDPOINT="$SPACES_REGION.digitaloceanspaces.com" \
  RCLONE_CONFIG_SPACES_ACL=public-read
dest="spaces:$SPACES_BUCKET"
common=(--fast-list --transfers 16 --checkers 32 --retries 5 --stats-one-line --stats 60s)

if [ "$mode" = catalog ]; then
  rclone copy "${common[@]}" "$site/catalog" "$dest/catalog" --include 'catalog-*.json.minisig'
  rclone copy "${common[@]}" "$site/catalog" "$dest/catalog" --include 'catalog-*.json'
  exit 0
fi
if [ "$mode" = media ]; then
  [ -d "$site/media/listings" ] && rclone copy "${common[@]}" --ignore-existing "$site/media/listings" "$dest/media/listings"
  exit 0
fi
if [ "$mode" = prune ]; then
  rclone sync "${common[@]}" "$repo" "$dest/repo" --delete-after
  exit 0
fi

# 1. immutable content
rclone copy "${common[@]}" --ignore-existing "$repo/objects" "$dest/repo/objects"
rclone copy "${common[@]}" --ignore-existing "$repo/deltas" "$dest/repo/deltas" 2>/dev/null || true
# 2. everything that points at content, except the summary
rclone copy "${common[@]}" "$repo" "$dest/repo" \
  --exclude 'objects/**' --exclude 'deltas/**' --exclude 'tmp/**' --exclude '.lock' \
  --exclude 'summary' --exclude 'summary.sig' --exclude 'summary.idx' --exclude 'summary.idx.sig'
rclone copy "${common[@]}" "$site" "$dest" --exclude 'catalog/**'
# 3. the summary, signature first
for f in summary.idx.sig summary.idx summary.sig summary; do
  [ -f "$repo/$f" ] && rclone copyto "${common[@]}" "$repo/$f" "$dest/repo/$f"
done
echo "synced to $SPACES_BUCKET"
SH
