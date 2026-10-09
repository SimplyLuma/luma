#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# The update-graph signing job (ADR-030 section 5).
#
#   sign-update-graph.sh --channel nightly|beta|stable [--dry-run]
#                        [--token-file FILE] [--source FILE]
#                        [--graph-dir DIR] [--no-sync | --sync]
#
# 1. Fetch the unsigned graph Hub renders from Switchboard's release state:
#    GET https://hub.simplyluma.com/api/updates/graph/<channel>?arch=x86_64
#    with the service token from --token-file (default: the root-only
#    $LUMA_OS_SECRETS/hub-graph-service-token). The token is passed to curl
#    in a 0600 header file, never on a command line or in a log. --source
#    reads a local file instead (tests, recovery).
# 2. Validate it (scripts/os/lib/update_graph.py): schema, freshness, newer
#    than the last signed graph, and every commit and rollback target must be
#    a release this pipeline actually published on that channel with the same
#    version.
# 3. Sign it with the minisign update-graph key; the trusted comment binds
#    channel, arch and generated_at. Verify the signature with the public key.
# 4. Install <channel>.json and <channel>.json.minisig atomically in the graph
#    directory (default $LUMA_OS_ROOT/publish/graph), then switch the served
#    copy (scripts/os/stage-webroot.sh) and upload (scripts/os/sync-to-cdn.sh)
#    only with an explicit --sync. Scheduled signing stages verified graphs;
#    possessing the read-only Hub credential does not authorize publication.
#
# --dry-run performs 1 to 3 in a temporary directory and changes nothing.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

channel=
dry_run=0
token_file="$LUMA_OS_SECRETS/hub-graph-service-token"
source_file=
graph_dir="$LUMA_OS_ROOT/publish/graph"
sync=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --channel) channel=${2:?}; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --token-file) token_file=${2:?}; shift 2 ;;
    --source) source_file=$(realpath "${2:?}"); shift 2 ;;
    --graph-dir) graph_dir=${2:?}; shift 2 ;;
    --no-sync) sync=0; shift ;;
    --sync) sync=1; shift ;;
    *) printf 'usage: %s --channel C [--dry-run] [--token-file F] [--source FILE] [--graph-dir DIR] [--no-sync | --sync]\n' "$0" >&2; exit 2 ;;
  esac
done
ref=$(luma_os_channel_ref "$channel")
luma_os_require_tools curl python3 install flock
minisign_dir="$LUMA_OS_KEYS/update-graph-minisign"
password_file="$LUMA_OS_SECRETS/update-graph-minisign.password"

work=$(mktemp -d "${TMPDIR:-/tmp}/luma-graph.XXXXXX")
chmod 0700 "$work"
trap 'rm -rf "$work"' EXIT

# A graph signing container receives only these fixed paths. In particular it
# must not inherit the general build helper's writable source mounts. Resolve
# the trusted tools image to an immutable ID once, before mounting any key.
graph_tools_image=
mkdir -m 0700 "$work/empty-hooks"
luma_os_graph_tools() {
  local mode=$1 data=$2
  shift 2
  case "$mode" in verify|sign) ;; *) luma_os_die 'invalid graph tools operation' ;; esac
  [ -d "$minisign_dir" ] && [ ! -L "$minisign_dir" ] &&
    [ "$(stat -c %u "$minisign_dir")" = 0 ] &&
    [ "$(stat -c %a "$minisign_dir")" = 700 ] ||
    luma_os_die 'graph key directory must be root-owned mode 0700'
  [ -d "$data" ] && [ ! -L "$data" ] || luma_os_die 'unsafe graph data directory'
  [ -z "$(find "$work/empty-hooks" -mindepth 1 -print -quit)" ] ||
    luma_os_die 'graph tools hooks directory is not empty'
  if [ -z "$graph_tools_image" ]; then
    graph_tools_image=$(luma_os_podman image inspect --format '{{.Id}}' "${LUMA_OS_GRAPH_TOOLS_IMAGE:-$(luma_os_tools_image)}")
    [[ "$graph_tools_image" =~ ^[a-f0-9]{64}$ ]] || luma_os_die 'tools image has no immutable identity'
    [ -z "${LUMA_OS_GRAPH_TOOLS_IMAGE:-}" ] ||
      [ "$graph_tools_image" = "$LUMA_OS_GRAPH_TOOLS_IMAGE" ] || luma_os_die 'pinned graph tools image changed'
  fi
  local access=ro
  [ "$mode" != sign ] || access=rw
  # Unlocking the existing encrypted minisign key needs slightly over 1 GiB
  # for its KDF. Keep verification small; give signing bounded headroom.
  local memory=1g memory_swap=1250m
  if [ "$mode" = sign ]; then
    memory=2g
    memory_swap=2500m
  fi
  local mounts=(--volume "$minisign_dir/luma-update-graph.pub:$minisign_dir/luma-update-graph.pub:ro")
  if [ "$mode" = sign ]; then
    mounts+=(--volume "$minisign_dir/luma-update-graph.key:$minisign_dir/luma-update-graph.key:ro")
  fi
  local key
  for key in luma-update-graph.pub $([ "$mode" != sign ] || printf '%s' luma-update-graph.key); do
    [ -f "$minisign_dir/$key" ] && [ ! -L "$minisign_dir/$key" ] &&
      [ "$(stat -c %u "$minisign_dir/$key")" = 0 ] || luma_os_die 'unsafe graph key file'
    [ "$key" != luma-update-graph.key ] ||
      [ "$(stat -c %a "$minisign_dir/$key")" = 600 ] || luma_os_die 'graph private key is not mode 0600'
  done
  # No caller-selected environment or arbitrary source path enters this key
  # boundary. Private key bytes are read-only, and the passphrase stays stdin.
  luma_os_podman run --rm -i --pull=never --network=none --cap-drop=ALL \
    --security-opt label=disable --security-opt no-new-privileges --read-only \
    --hooks-dir="$work/empty-hooks" --cpus=1 --memory="$memory" --memory-swap="$memory_swap" \
    --pids-limit=128 --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
    "${mounts[@]}" --volume "$data:$data:$access" \
    --env LANG=C.UTF-8 --entrypoint=/usr/bin/env "$graph_tools_image" -i \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/tmp LANG=C.UTF-8 "$@"
}

# Validation and the previous-generation comparison belong to one transaction.
# Never let two timers validate against the same head and replace one another.
install -d -m 0755 "$LUMA_OS_ROOT/locks"
exec 8>"$LUMA_OS_ROOT/locks/sign-graph-$channel.lock"
flock -w 60 8 || luma_os_die "another $channel graph signer holds the lock"

# 1. Fetch.
if [ -n "$source_file" ]; then
  cp "$source_file" "$work/graph.json"
else
  [ -f "$token_file" ] && [ ! -L "$token_file" ] && [ -r "$token_file" ] ||
    luma_os_die "Hub service token must be a readable regular file: $token_file"
  [ "$(stat -c %u "$token_file")" = 0 ] || luma_os_die "Hub service token must be root-owned: $token_file"
  [ "$(stat -c %a "$token_file")" = 600 ] || luma_os_die "Hub service token must be mode 0600: $token_file"
  url=${LUMA_OS_HUB_GRAPH_URL_TEMPLATE//<channel>/$channel}
  umask 077
  printf 'Authorization: Bearer %s\n' "$(tr -d '\r\n' <"$token_file")" >"$work/auth-header"
  curl --fail --silent --show-error --proto '=https' --tlsv1.2 --max-time 60 \
    --header @"$work/auth-header" --header 'Accept: application/json' \
    --output "$work/graph.json" "$url"
  rm -f "$work/auth-header"
fi

# The signer dates the graph. generated_at means "the signing job vouched for
# this graph at this time": luma-update refuses graphs older than three days
# and never accepts one older than the last it saw, so the date must advance
# with every signature whatever Hub put there. Hub's own value is kept in the
# job log. A graph is only ever dated after a successful fetch, so a Hub outage
# still ages the served graph.
hub_generated=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("generated_at", ""))' "$work/graph.json" 2>/dev/null || true)
python3 -c '
import json, sys
from datetime import datetime, timezone
path = sys.argv[1]
graph = json.load(open(path, encoding="utf-8"))
graph["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump(graph, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
' "$work/graph.json" || luma_os_die 'the fetched graph is not JSON'
luma_os_log "$channel graph fetched (Hub generated_at ${hub_generated:-missing}); dated by the signer"

# 2. Validate against what is actually published.
repo=$(luma_os_channel_repo "$channel")
python3 - "$repo" "$channel" >"$work/releases.json" <<'PY'
import glob, json, os, sys
repo, channel = sys.argv[1:3]
releases = {}
for path in glob.glob(os.path.join(repo, "luma", "releases", "*", "manifest.json")):
    manifest = json.load(open(path, encoding="utf-8"))
    commit = manifest["commit"]
    # Retention may have pruned an old release's commit: a graph must not
    # offer what the repository no longer serves.
    present = os.path.exists(os.path.join(repo, "objects", commit[:2], commit[2:] + ".commit"))
    if manifest.get("channel") == channel and present:
        releases[commit] = manifest["version"]
json.dump(releases, sys.stdout)
PY
previous_args=()
if [ -s "$graph_dir/$channel.json" ]; then
  # A recent unsigned or altered staging file cannot suppress a refresh. Verify
  # the actual previous bytes before trusting their timestamp or comparison.
  [ -s "$graph_dir/$channel.json.minisig" ] || luma_os_die 'previous graph has no signature'
  luma_os_graph_tools verify "$graph_dir" \
    minisign -V -p "$minisign_dir/luma-update-graph.pub" \
    -m "$graph_dir/$channel.json" -x "$graph_dir/$channel.json.minisig" >/dev/null ||
    luma_os_die 'previous graph signature rejected; nothing replaced'
  # Unchanged apart from generated_at, and signed less than
  # LUMA_OS_GRAPH_RESIGN_HOURS ago: nothing to do. Otherwise the graph is
  # signed again even with no release change, because luma-update refuses a
  # graph older than three days (a frozen graph must not keep devices on an
  # old release).
  if python3 -c '
import json, sys
from datetime import datetime, timedelta, timezone
new, old = (json.load(open(path, encoding="utf-8")) for path in sys.argv[1:3])
signed = datetime.strptime(old["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
same = {k: v for k, v in new.items() if k != "generated_at"} == {k: v for k, v in old.items() if k != "generated_at"}
recent = datetime.now(timezone.utc) - signed < timedelta(hours=float(sys.argv[3]))
sys.exit(0 if same and recent else 1)' "$work/graph.json" "$graph_dir/$channel.json" "$LUMA_OS_GRAPH_RESIGN_HOURS"; then
    luma_os_log "the $channel graph is unchanged and was signed less than $LUMA_OS_GRAPH_RESIGN_HOURS hours ago; nothing to do"
    exit 0
  fi
  previous_args=(--previous "$graph_dir/$channel.json")
fi
if ! python3 "$luma_os_repo_root/scripts/os/lib/update_graph.py" validate \
    --graph "$work/graph.json" --channel "$channel" --arch "$LUMA_OS_ARCH" \
    --releases "$work/releases.json" --max-age-days "$LUMA_OS_GRAPH_MAX_AGE_DAYS" \
    "${previous_args[@]}" >"$work/problems.txt"; then
  sed 's/^/  /' "$work/problems.txt" >&2
  luma_os_die "the $channel graph failed validation; nothing was signed"
fi

# 3. Sign and verify.
generated=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generated_at"])' "$work/graph.json")
python3 -c 'import json,sys; g=json.load(open(sys.argv[1])); open(sys.argv[2],"w").write(json.dumps(g, indent=2, sort_keys=True)+"\n")' \
  "$work/graph.json" "$work/$channel.json"
comment="luma-update-graph channel=$channel arch=$LUMA_OS_ARCH generated_at=$generated key=$LUMA_OS_GRAPH_KEY_ID"
[ -r "$password_file" ] || luma_os_die "update-graph key password is unavailable: $password_file"
luma_os_graph_tools sign "$work" sh -c '
  set -eu
  minisign -S -s "$1/luma-update-graph.key" -m "$2" -x "$2.minisig" -t "$3" >/dev/null
  minisign -V -p "$1/luma-update-graph.pub" -m "$2" -x "$2.minisig" >/dev/null
' sh "$minisign_dir" "$work/$channel.json" "$comment" <"$password_file"
grep -Fq "trusted comment: $comment" "$work/$channel.json.minisig" ||
  luma_os_die 'signature does not carry the expected trusted comment'

if [ "$dry_run" -eq 1 ]; then
  printf 'dry run: the %s graph (generated %s, %s releases) validated and signed; nothing installed\n' \
    "$channel" "$generated" "$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["releases"]))' "$work/graph.json")"
  exit 0
fi

# 4. Install atomically, then serve and upload.
install -d -m 0755 "$graph_dir"
install -m 0644 "$work/$channel.json" "$graph_dir/.$channel.json.new"
install -m 0644 "$work/$channel.json.minisig" "$graph_dir/.$channel.json.minisig.new"
mv -f "$graph_dir/.$channel.json.minisig.new" "$graph_dir/$channel.json.minisig"
mv -f "$graph_dir/.$channel.json.new" "$graph_dir/$channel.json"
luma_os_log "signed $channel graph generated $generated installed in $graph_dir"
if [ "$sync" -eq 1 ]; then
  "$luma_os_repo_root/scripts/os/stage-webroot.sh"
  "$luma_os_repo_root/scripts/os/sync-to-cdn.sh" --apply --only graph || {
    rc=$?
    [ "$rc" -eq 3 ] || luma_os_die "graph upload failed (exit $rc)"
  }
fi
