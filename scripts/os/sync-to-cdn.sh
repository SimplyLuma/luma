#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Upload the switched webroot generation to Luma's object storage under the
# os/ prefix, the same bucket and the same ordering rules as Depot's
# scripts/depot/sync-remote.sh (ADR-028 section 12): content-addressed objects
# and deltas first and never overwritten, then everything that points at
# content, the summary and its signature last.
#
#   sync-to-cdn.sh [--apply] [--only repo|preview|graph|keys|media|media-tree]...
#                  [--media FILE]... [--prune]
#
# media-tree uploads $LUMA_OS_ROOT/publish/media, the installer media
# scripts/os/nightly-media.sh publishes (config/os/media.env prefixes).
#
# Default is a dry run. Two origins are supported, chosen by which root-only
# (0600) file exists:
#
# * $LUMA_OS_SECRETS/dl-origin.env: the dl.simplyluma.com origin server over
#   rsync+SSH (restricted to its web root). Keys: DL_RSYNC_DEST (user@host),
#   DL_SSH_KEY (private key path), DL_HTTP_ORIGIN (http://host of the origin,
#   for verification), DL_HOST_HEADER (dl.simplyluma.com), DL_PREVIEW=1 only
#   once the origin keeps os/preview-repo internal behind the credential check.
#   After uploading, the summaries served through the origin must match the
#   local ones byte for byte, the channel heads' commits must be fetchable and
#   signed, and sampled objects must be present.
# * LUMA_OS_SPACES_ENV (default $LUMA_OS_SECRETS/spaces.env, else Depot's
#   /srv/luma-build/depot-keys/secrets/spaces.env): the S3 bucket, defining
#   SPACES_ACCESS_KEY, SPACES_SECRET_KEY, SPACES_REGION and SPACES_BUCKET.
#
# Access: os/repo, os/graph and os/keys are uploaded public-read (the public
# stable channel). os/preview-repo is uploaded private: the CDN Worker must
# fetch it with its own storage credential after checking the device's preview
# credential, so pre-release content is never readable from the bucket origin.
#
# --prune deletes objects the local repositories no longer have (after
# retention pruned old nightlies). Run it separately and only after the
# summaries that stopped naming them have been live for a day.
#
# Exit status 3 means hosting is not configured yet (nothing was attempted).

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

apply=0
prune=0
only=()
media=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) apply=1; shift ;;
    --prune) prune=1; shift ;;
    --only) only+=("${2:?}"); shift 2 ;;
    --media) media+=("$(realpath "${2:?}")"); shift 2 ;;
    *) printf 'usage: %s [--apply] [--only repo|preview|graph|keys|media]... [--media FILE]... [--prune]\n' "$0" >&2; exit 2 ;;
  esac
done
[ "${#only[@]}" -gt 0 ] || only=(repo preview graph keys)

current="$LUMA_OS_ROOT/webroot/current"
[ -L "$current" ] || luma_os_die 'no switched webroot generation; run scripts/os/stage-webroot.sh'
generation=$(readlink -f "$current")

origin_env="$LUMA_OS_SECRETS/dl-origin.env"
if [ -f "$origin_env" ]; then
  [ "$(stat -c %a "$origin_env")" = 600 ] || luma_os_die "$origin_env must be mode 0600"
  luma_os_load_env "$origin_env"
  media_staff_prefix=$(sed -n 's/^LUMA_MEDIA_STAFF_PREFIX=//p' "$luma_os_repo_root/config/os/media.env")
  media_public_prefix=$(sed -n 's/^LUMA_MEDIA_PUBLIC_PREFIX=//p' "$luma_os_repo_root/config/os/media.env")
  install -d -m 0755 "$LUMA_OS_ROOT/publish/media"
  : "${DL_RSYNC_DEST:?}" "${DL_SSH_KEY:?}" "${DL_HTTP_ORIGIN:?}" "${DL_HOST_HEADER:?}"
  [ -f "$DL_SSH_KEY" ] || luma_os_die "origin SSH key is missing: $DL_SSH_KEY"
  known_hosts="$LUMA_OS_SECRETS/dl-origin.known_hosts"
  touch "$known_hosts" && chmod 0600 "$known_hosts"
  if [ "$apply" -eq 1 ] && [ "$prune" -eq 1 ]; then
    # Objects leave the origin only after the summary that stopped naming them
    # has been served for a day, so no client still holds a summary that needs
    # them. (release-runbook.md, "Origin retention")
    summary="$generation/os/preview-repo/summary"
    [ -f "$summary" ] || summary="$generation/os/repo/summary"
    if [ -f "$summary" ] && [ -z "$(find "$summary" -mmin +$((${DL_PRUNE_AFTER_HOURS:-20} * 60)))" ]; then
      luma_os_log "origin prune skipped: the served summary is younger than ${DL_PRUNE_AFTER_HOURS:-20} hours"
      exit 0
    fi
  elif [ "$apply" -eq 1 ]; then
    # Budget: what the origin can hold at most after this upload is every kept
    # generation together (hard links counted once), which is never less than
    # the served generation plus whatever the next prune removes.
    limit_gib=${DL_MAX_GIB:-25}
    bytes=$(du -scb "$LUMA_OS_ROOT"/webroot/generations/*/os 2>/dev/null | tail -n 1 | cut -f1)
    for file in "${media[@]}"; do bytes=$((bytes + $(stat -c %s "$file"))); done
    # Installer media the origin serves (hard links counted once).
    if [ -d "$LUMA_OS_ROOT/publish/media" ]; then
      bytes=$(du -scbL "$LUMA_OS_ROOT"/webroot/generations/*/os "$LUMA_OS_ROOT/publish/media" 2>/dev/null | tail -n 1 | cut -f1)
      for file in "${media[@]}"; do bytes=$((bytes + $(stat -c %s "$file"))); done
    fi
    [ "$bytes" -le $((limit_gib * 1073741824)) ] ||
      luma_os_die "upload refused: $((bytes / 1073741824)) GiB would exceed the origin budget of $limit_gib GiB (DL_MAX_GIB); prune retention first"
    luma_os_log "origin budget: at most $((bytes / 1048576)) MiB of $limit_gib GiB"
  fi
  if [ "${DL_PREVIEW:-0}" != 1 ]; then
    filtered=()
    for part in "${only[@]}"; do
      [ "$part" = preview ] && { luma_os_log 'preview repository not uploaded: the origin does not protect it yet (DL_PREVIEW)'; continue; }
      filtered+=("$part")
    done
    only=("${filtered[@]}")
  fi
  media_mounts=""
  for file in "${media[@]}"; do media_mounts+=" $(dirname "$file")"; done
  LUMA_OS_TOOLS_NETWORK=1 \
  LUMA_OS_TOOLS_MOUNTS="$generation $(dirname "$DL_SSH_KEY") $LUMA_OS_SECRETS${DL_PREVIEW_CREDENTIAL_FILE:+ $(dirname "$DL_PREVIEW_CREDENTIAL_FILE")}$media_mounts $LUMA_OS_ROOT/publish/media" \
    luma_os_tools env DL_RSYNC_DEST="$DL_RSYNC_DEST" DL_SSH_KEY="$DL_SSH_KEY" \
      DL_HTTP_ORIGIN="$DL_HTTP_ORIGIN" DL_HOST_HEADER="$DL_HOST_HEADER" KNOWN_HOSTS="$known_hosts" \
      DL_PREVIEW_CREDENTIAL_FILE="${DL_PREVIEW_CREDENTIAL_FILE:-}" \
      MEDIA_TREE="$LUMA_OS_ROOT/publish/media" \
      MEDIA_STAFF_PREFIX="$media_staff_prefix" MEDIA_PUBLIC_PREFIX="$media_public_prefix" \
      bash -s -- "$generation" "$apply" "$prune" "${only[*]}" "${media[*]:-}" \
      <"$luma_os_repo_root/scripts/os/lib/sync-origin.sh"
  exit $?
fi

secrets=${LUMA_OS_SPACES_ENV:-}
if [ -z "$secrets" ]; then
  for candidate in "$LUMA_OS_SECRETS/spaces.env" /srv/luma-build/depot-keys/secrets/spaces.env; do
    [ -f "$candidate" ] && { secrets=$candidate; break; }
  done
fi
if [ -z "$secrets" ] || [ ! -f "$secrets" ]; then
  luma_os_log 'hosting is not configured (no spaces.env); nothing uploaded'
  exit 3
fi
[ "$(stat -c %a "$secrets")" = 600 ] || luma_os_die "$secrets must be mode 0600"

dry=(--dry-run)
[ "$apply" -eq 1 ] && dry=()
LUMA_OS_TOOLS_NETWORK=1 LUMA_OS_TOOLS_MOUNTS="$generation" LUMA_OS_TOOLS_ENV_FILE="$secrets" \
  luma_os_tools bash -s -- "$generation" "$prune" "${only[*]}" "${dry[*]:-}" <<'SH'
set -euo pipefail
generation=$1 prune=$2 only=$3 dry=$4
export RCLONE_CONFIG_SPACES_TYPE=s3 RCLONE_CONFIG_SPACES_PROVIDER=DigitalOcean \
  RCLONE_CONFIG_SPACES_ACCESS_KEY_ID="$SPACES_ACCESS_KEY" \
  RCLONE_CONFIG_SPACES_SECRET_ACCESS_KEY="$SPACES_SECRET_KEY" \
  RCLONE_CONFIG_SPACES_ENDPOINT="$SPACES_REGION.digitaloceanspaces.com"
dest="spaces:$SPACES_BUCKET"
common=(--fast-list --transfers 16 --checkers 32 --retries 5 --stats-one-line --stats 60s $dry)

sync_repo() {
  local source=$1 prefix=$2 acl=$3 f
  [ -f "$source/config" ] || return 0
  local args=("${common[@]}" --s3-acl "$acl")
  if [ "$prune" = 1 ]; then
    rclone sync "${args[@]}" "$source" "$dest/$prefix" --delete-after
    return 0
  fi
  rclone copy "${args[@]}" --ignore-existing "$source/objects" "$dest/$prefix/objects"
  [ -d "$source/deltas" ] && rclone copy "${args[@]}" --ignore-existing \
    --exclude 'superblock' "$source/deltas" "$dest/$prefix/deltas"
  [ -d "$source/deltas" ] && rclone copy "${args[@]}" --include '**/superblock' "$source/deltas" "$dest/$prefix/deltas"
  rclone copy "${args[@]}" "$source" "$dest/$prefix" \
    --exclude 'objects/**' --exclude 'deltas/**' --exclude 'tmp/**' --exclude 'state/**' \
    --exclude '.lock' --exclude 'summary' --exclude 'summary.sig' \
    --exclude 'summary.idx' --exclude 'summary.idx.sig' --exclude 'summaries/**'
  for f in summaries summary.idx.sig summary.idx summary.sig summary; do
    if [ -d "$source/$f" ]; then
      rclone copy "${args[@]}" "$source/$f" "$dest/$prefix/$f"
    elif [ -f "$source/$f" ]; then
      rclone copyto "${args[@]}" "$source/$f" "$dest/$prefix/$f"
    fi
  done
}

for part in $only; do
  case "$part" in
    repo) sync_repo "$generation/os/repo" os/repo public-read ;;
    preview) sync_repo "$generation/os/preview-repo" os/preview-repo private ;;
    graph) [ -d "$generation/os/graph" ] && {
             rclone copy "${common[@]}" --s3-acl public-read --include '*.minisig' "$generation/os/graph" "$dest/os/graph"
             rclone copy "${common[@]}" --s3-acl public-read --include '*.json' "$generation/os/graph" "$dest/os/graph"
           } ;;
    keys) rclone copy "${common[@]}" --s3-acl public-read "$generation/os/keys" "$dest/os/keys" ;;
  esac
done
echo "os content ${dry:+(dry run) }synced to $SPACES_BUCKET"
SH
