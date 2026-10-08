# SPDX-License-Identifier: Apache-2.0
# Channel repository operations shared by publication and promotion.
# Sourced after lib/common.sh, never executed.
#
# The safety rules carried over from scripts/update/promote-ostree-commit.sh:
# the target repository is fsck'd before its ref moves; the ref only ever moves
# forward (the new commit's parent is the current head); the ref move is the
# last content change before the signed summary; and the result is verified by
# a fresh client that holds nothing but the exported public key.

luma_os_init_channel_repo() {
  local repo=$1
  if [ ! -f "$repo/config" ]; then
    install -d -m 0755 "$repo"
    ostree init --repo="$repo" --mode=archive --collection-id="$LUMA_OS_COLLECTION_ID"
  fi
  case "$(ostree config --repo="$repo" get core.mode)" in
    archive|archive-z2) ;;
    *) luma_os_die "channel repository must be an archive repository: $repo" ;;
  esac
  [ "$(ostree config --repo="$repo" get core.collection-id 2>/dev/null)" = "$LUMA_OS_COLLECTION_ID" ] ||
    luma_os_die "channel repository has the wrong collection id: $repo"
}

luma_os_channel_head() {
  ostree rev-parse --repo="$1" "$2" 2>/dev/null || true
}

# Print "<from> <to>" delta pairs for a new head: from its nearest
# LUMA_OS_DELTA_PREDECESSORS ancestors on the channel, and from empty.
luma_os_delta_plan() {
  local repo=$1 new=$2 parent count=0
  parent=$(ostree rev-parse --repo="$repo" "$new^" 2>/dev/null || true)
  while [ -n "$parent" ] && [ "$count" -lt "$LUMA_OS_DELTA_PREDECESSORS" ]; do
    # Only a predecessor whose content is in this repository can be a delta
    # base (a promoted channel's parent is always in its own repository).
    if ostree ls --repo="$repo" "$parent" / >/dev/null 2>&1; then
      printf '%s %s\n' "$parent" "$new"
      count=$((count + 1))
    fi
    parent=$(ostree rev-parse --repo="$repo" "$parent^" 2>/dev/null || true)
  done
  if [ "$LUMA_OS_EMPTY_DELTA" = 1 ]; then
    printf 'empty %s\n' "$new"
  fi
}

# luma_os_advance_channel REPO REF NEW_COMMIT
# NEW_COMMIT must already be in REPO, signed, with its parent equal to the
# current head of REF. Generates deltas, moves REF, signs the summary, prunes
# the from-empty delta of the previous head, and verifies as a fresh client.
# Prints the delta sizes as JSON on the last line.
luma_os_advance_channel() {
  local repo=$1 ref=$2 new=$3 head parent from to delta_json='[]' size
  head=$(luma_os_channel_head "$repo" "$ref")
  parent=$(ostree rev-parse --repo="$repo" "$new^" 2>/dev/null || true)
  if [ "$head" = "$new" ]; then
    luma_os_log "$ref already points to $new"
    printf '[]\n'
    return 0
  fi
  [ "$parent" = "$head" ] ||
    luma_os_die "forward-only: $new has parent ${parent:-none}, but $ref is at ${head:-nothing}"
  luma_os_log "advancing $ref from ${head:-nothing} to $new"

  luma_os_log "fsck $repo"
  ostree fsck --repo="$repo" --quiet ||
    luma_os_die "repository fsck failed: $repo"

  while read -r from to; do
    [ -n "$to" ] || continue
    if [ "$from" = empty ]; then
      luma_os_log "static delta: empty -> $to"
      ostree static-delta generate --repo="$repo" --empty --to="$to" >/dev/null
      size=$(du -sb "$repo/deltas/$(python3 "$luma_os_repo_root/scripts/os/lib/delta_path.py" "" "$to")" | awk '{ print $1 }')
    else
      luma_os_log "static delta: $from -> $to"
      ostree static-delta generate --repo="$repo" --from="$from" --to="$to" >/dev/null
      size=$(du -sb "$repo/deltas/$(python3 "$luma_os_repo_root/scripts/os/lib/delta_path.py" "$from" "$to")" | awk '{ print $1 }')
    fi
    delta_json=$(python3 -c 'import json, sys; d = json.loads(sys.argv[1]); d.append({"from": None if sys.argv[2] == "empty" else sys.argv[2], "to": sys.argv[3], "bytes": int(sys.argv[4])}); print(json.dumps(d))' \
      "$delta_json" "$from" "$to" "$size")
  done < <(luma_os_delta_plan "$repo" "$new")

  # Moving the ref is the last content change; the signed summary follows.
  if [ -n "$head" ]; then
    ostree reset --repo="$repo" "$ref" "$new"
  else
    ostree refs --repo="$repo" --create="$ref" "$new"
  fi
  if [ -n "$head" ] && [ "$LUMA_OS_EMPTY_DELTA" = 1 ]; then
    ostree static-delta delete --repo="$repo" "$head" >/dev/null 2>&1 || true
  fi
  ostree summary --repo="$repo" --update \
    --gpg-sign="$(luma_os_gpg_fingerprint)" --gpg-homedir="$(luma_os_gpg_home)" \
    --add-metadata="org.projectluma.generated-utc='$(date -u +%Y-%m-%dT%H:%M:%SZ)'"

  luma_os_verify_as_client "$repo" "$ref" "$new"
  printf '%s\n' "$delta_json"
}

# A fresh client with only the exported public key must accept the signed
# summary, the signed commit and its ref binding, and see REF at COMMIT.
luma_os_verify_as_client() {
  local repo=$1 ref=$2 expected=$3 client got
  client=$(mktemp -d "$LUMA_OS_ROOT/tmp/client-verify.XXXXXX")
  ostree init --repo="$client/repo" --mode=archive >/dev/null
  ostree remote add --repo="$client/repo" \
    --gpg-import="$LUMA_OS_KEYS/luma-os-release.gpg" \
    --set=gpg-verify=true --set=gpg-verify-summary=true \
    --collection-id="$LUMA_OS_COLLECTION_ID" \
    verify "file://$repo" "$ref"
  ostree remote summary --repo="$client/repo" verify >/dev/null ||
    { rm -rf "$client"; luma_os_die 'client verification: summary signature rejected'; }
  ostree pull --repo="$client/repo" --commit-metadata-only verify "$ref" >/dev/null ||
    { rm -rf "$client"; luma_os_die 'client verification: commit signature or binding rejected'; }
  got=$(ostree rev-parse --repo="$client/repo" "verify:$ref")
  rm -rf "$client"
  [ "$got" = "$expected" ] || luma_os_die "client verification: $ref resolved $got, expected $expected"
  luma_os_log "client verification passed: $ref = $expected"
}

luma_os_sign_file() {
  # logind may retire the root agent socket between summary signing and this
  # signature. Re-seed from the existing protected key owner, as gate.sh does;
  # never retry unsigned or request an interactive pinentry in a release job.
  luma_os_gpg_unlock
  gpg --batch --yes --homedir "$(luma_os_gpg_home)" --local-user "$(luma_os_gpg_fingerprint)" \
    --armor --detach-sign --output "$1.asc" "$1"
}
