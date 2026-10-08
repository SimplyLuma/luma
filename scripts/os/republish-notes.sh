#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Rewrite and re-sign the release notes of an already published nightly
# (docs/os/release-process.md). Touches neither its medium nor its OSTree ref.
#
#   republish-notes.sh --build-id ID --from REV --membership REV
#                      [--previous-manifest FILE] [--dry-run]
#
# --from        the previous published nightly's source revision
# --membership  the source revision whose changes/ decide which fragments
#               belong to this nightly (the text is read from this checkout)
#
# 1. Assembles the notes again from the fragments now in this checkout and
#    checks every changed package is still covered.
# 2. Replaces luma/releases/<version>/notes.json in the channel repository,
#    re-renders and re-signs the update graph (summary, notes URL).
# 3. Replaces and re-signs media/<channel>/notes/<build>.json, updates the
#    medium's index entry summary, re-signs latest.json.
# 4. Stages the served generation and uploads graph, preview metadata and the
#    media tree, then verifies the origin.
set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"
build_id= from= membership= dry_run=0 channel=nightly previous_manifest=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --build-id) build_id=${2:?}; shift 2 ;;
    --from) from=${2:?}; shift 2 ;;
    --membership) membership=${2:?}; shift 2 ;;
    --previous-manifest) previous_manifest=$(realpath "${2:?}"); shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    *) printf 'usage: %s --build-id ID --from REV --membership REV [--dry-run]\n' "$0" >&2; exit 2 ;;
  esac
done
[ -n "$build_id" ] && [ -n "$from" ] && [ -n "$membership" ] || { printf 'missing arguments\n' >&2; exit 2; }
luma_os_require_root
luma_os_load_env "$luma_os_repo_root/config/os/media.env"
build_dir="$LUMA_OS_ROOT/builds/$build_id"
luma_os_load_env "$build_dir/build.env"
repo=$(luma_os_channel_repo "$channel")
release_dir="$repo/luma/releases/$LUMA_OS_VERSION"
[ -s "$release_dir/manifest.json" ] || luma_os_die "$LUMA_OS_VERSION is not published on $channel"
tree="$LUMA_OS_ROOT/publish/media/public/$channel"
entry="$tree/luma-$channel-$build_id.iso.json"
[ -s "$entry" ] || luma_os_die "no published medium entry for $build_id"
nightly_date=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["nightly_date"])' "$entry")
previous_build=$(python3 - "$repo" "$channel" "$LUMA_OS_VERSION" <<'PY'
import glob, json, os, sys
repo, channel, version = sys.argv[1:]
releases = []
for path in glob.glob(os.path.join(repo, "luma", "releases", "*", "manifest.json")):
    m = json.load(open(path))
    if m.get("channel") == channel:
        releases.append(m)
releases.sort(key=lambda m: m["published_utc"])
names = [m["version"] for m in releases]
i = names.index(version)
print(releases[i - 1]["build_id"] if i > 0 else "")
PY
)
[ -n "$previous_manifest" ] ||
  for candidate in "$repo/luma/releases/1.0.0-$channel.$previous_build/luma-packages.manifest" "$LUMA_OS_ROOT/builds/$previous_build/luma-packages.manifest"; do
    [ -s "$candidate" ] && previous_manifest=$candidate && break
  done
[ -n "$previous_manifest" ] || luma_os_die "the previous release's package set ($previous_build) is gone"

work=$(mktemp -d "$LUMA_OS_ROOT/tmp/republish-notes.XXXXXX")
trap 'rm -rf "$work"' EXIT
python3 "$luma_os_repo_root/scripts/os/lib/release_notes.py" build --repo "$luma_os_repo_root" \
  --from "$from" --membership "$membership" --to HEAD \
  --previous-manifest "$previous_manifest" --manifest "$build_dir/luma-packages.manifest" \
  --exceptions "$luma_os_repo_root/config/os/release-notes-exceptions.txt" \
  --channel "$channel" --build-id "$build_id" --version "$LUMA_OS_VERSION" --nightly-date "$nightly_date" \
  --display-name "${LUMA_DISPLAY_NAME:-}" --out "$work/notes.json"
if [ "$dry_run" -eq 1 ]; then
  cat "$work/notes.json"
  exit 0
fi

minisign_sign() {
  # minisign_sign FILE COMMENT: FILE.minisig with the update-graph key, verified.
  LUMA_OS_TOOLS_MOUNTS="$LUMA_OS_KEYS/update-graph-minisign $(dirname "$1")" luma_os_tools sh -c '
    set -e
    minisign -S -s "$1/luma-update-graph.key" -m "$2" -x "$2.minisig" -t "$3" >/dev/null
    minisign -V -p "$1/luma-update-graph.pub" -m "$2" -x "$2.minisig" >/dev/null
  ' sh "$LUMA_OS_KEYS/update-graph-minisign" "$1" "$2" <"$LUMA_OS_SECRETS/update-graph-minisign.password"
}

# 2. Release metadata and graph.
install -m 0644 "$work/notes.json" "$release_dir/notes.json"
cp "$work/notes.json" "$build_dir/release-notes.json"
graph_source="$work/graph.json"
python3 "$luma_os_repo_root/scripts/os/lib/graph_from_releases.py" "$repo" "$channel" "$LUMA_OS_ARCH" >"$graph_source"
"$luma_os_repo_root/scripts/os/sign-update-graph.sh" --channel "$channel" --source "$graph_source" --no-sync

# 3. Media tree: notes, entry summary, index.
generated=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generated_utc"])' "$work/notes.json")
install -m 0644 "$work/notes.json" "$tree/notes/$build_id.json.new"
minisign_sign "$tree/notes/$build_id.json.new" "luma-release-notes channel=$channel build_id=$build_id generated_utc=$generated"
python3 - "$entry" "$work/notes.json" <<'PY'
import json, sys
entry, notes = sys.argv[1], json.load(open(sys.argv[2]))
data = json.load(open(entry))
data["summary"] = notes["summary"]
json.dump(data, open(entry, "w"), indent=2, sort_keys=True)
open(entry, "a").write("\n")
PY
python3 - "$tree/latest.json" "$tree" <<'PY'
import datetime, json, os, sys
path, tree = sys.argv[1], sys.argv[2]
index = json.load(open(path))
entries = []
for e in index["entries"]:
    fresh = os.path.join(tree, e["file"] + ".json")
    entries.append(json.load(open(fresh)) if os.path.exists(fresh) else e)
index["entries"] = entries
public = [e for e in entries if e.get("public", True)]
index["latest"] = public[0] if public else entries[0]
index["generated_utc"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump(index, open(path + ".new", "w"), indent=2, sort_keys=True)
open(path + ".new", "a").write("\n")
PY
index_generated=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generated_utc"])' "$tree/latest.json.new")
minisign_sign "$tree/latest.json.new" "luma-media-index channel=$channel generated_utc=$index_generated"
mv -f "$tree/notes/$build_id.json.new.minisig" "$tree/notes/$build_id.json.minisig"
mv -f "$tree/notes/$build_id.json.new" "$tree/notes/$build_id.json"
mv -f "$tree/latest.json.new.minisig" "$tree/latest.json.minisig"
mv -f "$tree/latest.json.new" "$tree/latest.json"

# 4. Serve and upload.
"$luma_os_repo_root/scripts/os/stage-webroot.sh"
"$luma_os_repo_root/scripts/os/sync-to-cdn.sh" --apply --only graph --only preview --only media-tree
luma_os_log "release notes of $build_id republished"
