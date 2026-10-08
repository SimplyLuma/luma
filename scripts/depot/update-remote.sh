#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Regenerate the Luma remote's signed summary, AppStream branches and static
# deltas, then write the descriptor files (luma.flatpakrepo, .flatpakref, keys).
#
#   update-remote.sh
#
# Deltas: from each ref's previous commit and from empty, so installs and
# updates both download a single compressed file per object set. The SDK
# (developers only) and the Locale extension (installed as per-language
# subsets, which cannot use a whole-ref delta) are left to object fetches. Old commits
# beyond DEPOT_PRUNE_DEPTH are pruned so the hosted repository stays bounded;
# clients that are further behind fall back to fetching objects individually.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"
depot_require_signing_control

: "${DEPOT_PRUNE_DEPTH:=3}"
fingerprint=$(depot_gpg_fingerprint)
site="$DEPOT_ROOT/site"

depot_log "updating summary, appstream and static deltas"
depot_in_signer flatpak build-update-repo \
  --gpg-sign="$fingerprint" --gpg-homedir="$DEPOT_KEYS/gnupg" \
  --title="Luma" \
  --comment="Apps made for Luma, for every Linux desktop" \
  --homepage="https://simplyluma.com/apps" \
  --icon="$DEPOT_PUBLIC_URL/media/luma-remote.svg" \
  --default-branch=beta \
  --generate-static-deltas --static-delta-jobs=4 \
  --static-delta-ignore-ref='*.Sdk' --static-delta-ignore-ref='*.Locale' \
  --static-delta-ignore-ref='*.Debug' --static-delta-ignore-ref='*.Sources' \
  --prune --prune-depth="$DEPOT_PRUNE_DEPTH" \
  "$depot_repo"

depot_log "writing descriptor files under $site"
catalog_key=()
[ -f "$DEPOT_KEYS/minisign/luma-depot-catalog.pub" ] &&
  catalog_key=(--catalog-key "$DEPOT_KEYS/minisign/luma-depot-catalog.pub")
depot_in_signer python3 scripts/depot/generate-remote-files.py \
  --repo "$depot_repo" --site "$site" \
  --gpg-key "$DEPOT_KEYS/luma-depot.gpg" --gpg-key-armored "$DEPOT_KEYS/luma-depot.asc" \
  "${catalog_key[@]}" \
  --remote-icon assets/icon-theme/Prairie/scalable/apps/org.projectluma.Depot.svg \
  --titles packaging/flatpak/apps/titles.json \
  --releases "$DEPOT_ROOT/releases" \
  --base-url "$DEPOT_PUBLIC_URL"

# A remote that fails its own verification must never be synced.
"$(dirname -- "$0")/verify-remote.sh"
depot_log "remote updated: $(du -sh "$depot_repo" | cut -f1)"
