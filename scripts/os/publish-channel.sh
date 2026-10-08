#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Publish a gate-passing build to its channel (ADR-030 sections 3 to 5).
#
#   publish-channel.sh --build-id YYYYMMDD.N [--dry-run]
#
# Refuses unless: the build is clean; its gate result is "pass" for exactly
# this candidate commit; the gate proved the update from the channel's current
# head and the automatic and manual rollbacks (or the channel is empty, which
# makes this its first release); and the candidate's parent is still the
# channel head. Then the signed candidate is copied into the channel
# repository, deltas are generated, the ref moves, the summary is signed, a
# fresh client verifies it, and the release manifest, provenance, SBOM, gate
# record and package list are written beside the release in the repository
# (luma/releases/<version>/), the manifest signed with the release key.
#
# Publication writes the local staging repositories only; the served copy is
# switched by scripts/os/stage-webroot.sh and scripts/os/sync-to-cdn.sh.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"
. "$(dirname -- "$0")/lib/channel.sh"

build_id=
dry_run=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --build-id) build_id=${2:?}; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    *) printf 'usage: %s --build-id ID [--dry-run]\n' "$0" >&2; exit 2 ;;
  esac
done

luma_os_require_root
luma_os_require_tools ostree gpg python3 flock
luma_os_check_host

build_dir="$LUMA_OS_ROOT/builds/$build_id"
for file in build.env export.env gate-result.json provenance.json sbom.spdx.json packages-installed.tsv os-release; do
  [ -s "$build_dir/$file" ] || luma_os_die "build is missing $file: $build_dir"
done
luma_os_load_env "$build_dir/build.env"
luma_os_load_env "$build_dir/export.env"
channel=$LUMA_OS_CHANNEL
ref=$LUMA_EXPORT_REF
candidate=$LUMA_EXPORT_CANDIDATE
candidate_repo="$LUMA_OS_ROOT/ostree/candidate-repo"
repo=$(luma_os_channel_repo "$channel")

[ "$LUMA_SOURCE_DIRTY" = false ] || luma_os_die 'refusing to publish a build from a dirty source checkout'
[ -z "${LUMA_SOURCE_SNAPSHOT_SHA256:-}" ] && [ -z "${LUMA_RELEASE_CHECKS_SHA256:-}" ] || luma_os_die 'refusing to publish a private source snapshot'

exec 6>"$LUMA_OS_ROOT/locks/publish-$channel.lock"
flock -n 6 || luma_os_die "another publication to $channel is running"
luma_os_init_channel_repo "$repo"
head=$(luma_os_channel_head "$repo" "$ref")

python3 - "$build_dir/gate-result.json" "$candidate" "${head:-}" <<'PY' || luma_os_die 'the gate result does not allow publication'
import json, sys
gate = json.load(open(sys.argv[1], encoding="utf-8"))
candidate, head = sys.argv[2], sys.argv[3]
problems = []
if gate.get("commit") != candidate:
    problems.append(f"gate tested {gate.get('commit')}, not {candidate}")
if gate.get("result") != "pass":
    problems.append(f"gate result is {gate.get('result')}")
if gate.get("fresh") != "pass":
    problems.append(f"fresh install stage is {gate.get('fresh')}")
if gate.get("no_account") != "pass":
    problems.append(f"no-account install stage is {gate.get('no_account')}")
if head:
    if (gate.get("previous_commit") or "") != head:
        problems.append(f"gate updated from {gate.get('previous_commit')}, but the channel head is {head}")
    for stage in ("update", "rollback"):
        if gate.get(stage) != "pass":
            problems.append(f"{stage} stage is {gate.get(stage)} and the channel already has releases")
else:
    for stage in ("update", "rollback"):
        if gate.get(stage) not in ("pass", "not-applicable"):
            problems.append(f"{stage} stage is {gate.get(stage)}")
for problem in problems:
    print("error: " + problem, file=sys.stderr)
sys.exit(1 if problems else 0)
PY

parent=$(ostree rev-parse --repo="$candidate_repo" "$candidate^" 2>/dev/null || true)
[ "$parent" = "$head" ] ||
  luma_os_die "candidate parent ${parent:-none} is not the channel head ${head:-none}; re-export and re-gate"

version=$LUMA_OS_VERSION
release_dir="$repo/luma/releases/$version"
[ ! -e "$release_dir" ] || luma_os_die "release metadata already exists: $release_dir"

if [ "$dry_run" -eq 1 ]; then
  printf 'would publish %s as %s on %s in %s\n' "$candidate" "$version" "$ref" "$repo"
  printf '  parent: %s\n' "${head:-none (first release of the channel)}"
  luma_os_delta_plan "$candidate_repo" "$candidate" | sed 's/^/  delta: /'
  exit 0
fi

luma_os_check_space 15
luma_os_gpg_unlock
trap luma_os_gpg_lock EXIT

luma_os_log "copying $candidate into $repo"
ostree pull-local --repo="$repo" --untrusted "$candidate_repo" "$candidate" >/dev/null
deltas=$(luma_os_advance_channel "$repo" "$ref" "$candidate" | tail -n 1)

published=$(date -u +%Y-%m-%dT%H:%M:%SZ)
install -d -m 0755 "$release_dir"
cp "$build_dir/provenance.json" "$build_dir/sbom.spdx.json" "$build_dir/gate-result.json" \
  "$build_dir/os-release" "$release_dir/"
cut -f1-5 "$build_dir/packages-installed.tsv" >"$release_dir/packages.tsv"
# The exact Luma pin set, which the next release's notes are checked against.
cp "$build_dir/luma-packages.manifest" "$release_dir/luma-packages.manifest"
root_dirtree=$(python3 "$luma_os_repo_root/scripts/os/lib/ostree_metadata.py" root-dirtree "$repo" "$candidate")
python3 "$luma_os_repo_root/scripts/os/lib/provenance.py" manifest \
  --output "$release_dir/manifest.json" \
  --version "$version" --channel "$channel" --ref "$ref" \
  --commit "$candidate" --parent "$head" --root-dirtree "$root_dirtree" \
  --provenance "$build_dir/provenance.json" --gate "$build_dir/gate-result.json" \
  --deltas "$deltas" --published "$published"
for file in manifest.json provenance.json sbom.spdx.json gate-result.json; do
  luma_os_sign_file "$release_dir/$file"
done
# The channel's latest release metadata, for installers and Switchboard.
install -d -m 0755 "$repo/luma/channels"
cp "$release_dir/manifest.json" "$repo/luma/channels/$channel.json"
luma_os_sign_file "$repo/luma/channels/$channel.json"
install -D -m 0644 "$LUMA_OS_KEYS/luma-os-release.gpg" "$repo/luma/keys/luma-os-release.gpg"
install -D -m 0644 "$LUMA_OS_KEYS/luma-os-release.asc" "$repo/luma/keys/luma-os-release.asc"

cat >"$build_dir/publish.env" <<EOF
LUMA_PUBLISH_CHANNEL=$channel
LUMA_PUBLISH_REF=$ref
LUMA_PUBLISH_COMMIT=$candidate
LUMA_PUBLISH_PARENT=$head
LUMA_PUBLISH_VERSION=$version
LUMA_PUBLISH_UTC=$published
EOF
luma_os_log "published $version ($candidate) on $ref"
printf '%s\n' "$candidate"
