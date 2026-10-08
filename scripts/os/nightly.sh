#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# The nightly OS release job (ADR-030): build -> export -> gate -> publish.
#
#   nightly.sh [--source-branch BRANCH] [--base pinned|latest] [--no-publish]
#   nightly.sh --resume-build YYYYMMDD.N [--no-publish]
#   nightly.sh --release-only YYYYMMDD.N
#
# A nightly publishes atomically (docs/os/release-process.md): after the gate
# passes, the release notes must cover every changed package, and the release
# medium of the exact candidate must pass its install checks. Only then are the
# update (channel ref, release metadata, graph with notes) and the medium
# (ISO, signed index, signed notes) published together. If anything fails,
# neither is published and the previous nightly stays the head.
#
# --release-only runs only that release step for a build whose candidate was
# exported and passed the gate but was not published.
#
# --resume-build takes an already built image through export (again, if its
# candidate was discarded), the gate and publication, for a night that failed
# after the build for a reason outside the image (the recorded provenance still
# names the image's own source revision).
#
# Run by luma-os-nightly.service (config/os/systemd) on the build host. It:
#   * holds one lock, so runs never overlap (a second start exits at once);
#   * refuses unless the dedicated volume and its disk have room, and prunes
#     old builds it created first (scripts/os/prune.sh);
#   * updates a clean checkout of BRANCH from the host's pipeline Git mirror
#     and builds from exactly that revision (a dirty build never publishes);
#   * builds the image, exports the signed candidate, gates it in VMs, and
#     publishes to nightly only when the gate passes, then switches the served
#     generation and uploads when hosting is configured;
#   * writes status/nightly.json (and a per-build copy) for Hub and Switchboard,
#     with build id, version, commit, source revision, gate result, timestamps
#     and sizes, at every step, so a failed night is visible too.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

branch=${LUMA_OS_SOURCE_BRANCH:-stage}
base_policy=${LUMA_OS_NIGHTLY_BASE:-latest}
publish=1
resume=
release_only=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --resume-build) resume=${2:?}; shift 2 ;;
    --release-only) resume=${2:?}; release_only=1; shift 2 ;;
    --source-branch) branch=${2:?}; shift 2 ;;
    --base) base_policy=${2:?}; shift 2 ;;
    --no-publish) publish=0; shift ;;
    *) printf 'usage: %s [--source-branch BRANCH] [--base pinned|latest] [--no-publish]\n' "$0" >&2; exit 2 ;;
  esac
done

luma_os_require_root
luma_os_check_host
install -d -m 0700 "$LUMA_OS_ROOT/locks"
install -d -m 0755 "$LUMA_OS_ROOT/status" "$LUMA_OS_ROOT/logs/nightly"
exec 4>"$LUMA_OS_ROOT/locks/nightly.lock"
if ! flock -n 4; then
  luma_os_log 'another nightly run is active; not starting a second one'
  exit 0
fi

run_started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
run_log="$LUMA_OS_ROOT/logs/nightly/$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$run_log") 2>&1
status="$LUMA_OS_ROOT/status/nightly.json"
provenance="$luma_os_repo_root/scripts/os/lib/provenance.py"

set_status() {
  python3 "$provenance" status --output "$status" --merge "$@"
}
# Each run starts a fresh record; the previous one stays in its build directory.
rm -f "$status"
set_status --set "run_started_utc=$run_started" --set 'state="starting"' \
  --set 'build_id=null' --set 'version=null' --set 'commit=null' --set 'source_revision=null' \
  --set 'gate_result=null' --set 'published=false' --set 'error=null' \
  --set "log=\"$(basename "$run_log")\""

fail_run() {
  local message=$1
  set_status --set 'state="failed"' --set "error=$(luma_os_json_string "$message")" \
    --set "run_completed_utc=\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
  [ -z "${build_dir:-}" ] || cp "$status" "$build_dir/status.json" 2>/dev/null || true
  luma_os_die "$message"
}
trap 'rc=$?; fstrim "$LUMA_OS_ROOT" >/dev/null 2>&1 || true; [ "$rc" -eq 0 ] || [ "$(python3 -c "import json;print(json.load(open(\"$status\")).get(\"state\"))" 2>/dev/null)" = failed ] || set_status --set "state=\"failed\"" --set "error=\"exit $rc\""' EXIT

# Room first: prune what earlier runs left, hand freed blocks back to the
# shared disk, then require room for the whole run on the volume and on the
# disk that carries it. A fresh build needs no earlier image; a resumed build
# needs its own. The image (11 GB) plus its OCI export and the gate's VM disks
# (25 GiB) must fit together: 20260918.1 started with 36 GiB free, its build
# and export took 16, and the gate then refused with 20 GiB left. 42 GiB is
# that measured 16 plus the gate's 25, plus one. A resume needs its own room:
# on 2026-09-20 importing the kept image and copying the tree into the
# candidate repository took 17 GiB before the gate started, so a resume asks
# for that plus the gate's 30.
if [ -n "$resume" ]; then
  "$luma_os_repo_root/scripts/os/prune.sh" --apply --trim --keep-images 1000 || fail_run 'pruning old builds failed'
  luma_os_check_space 45 || fail_run 'not enough disk space to resume a nightly'
else
  "$luma_os_repo_root/scripts/os/prune.sh" --apply --trim --keep-images 0 || fail_run 'pruning old builds failed'
  luma_os_check_space 42 || fail_run 'not enough disk space for a nightly'
fi
# Origin retention: remove from dl.simplyluma.com what the served generation no
# longer names, before tonight's content is uploaded, so the origin stays
# within its budget. sync-to-cdn.sh only prunes once the served summary has
# been live for a day; a failure never blocks the build and is retried next run.
if [ "$publish" -eq 1 ]; then
  "$luma_os_repo_root/scripts/os/sync-to-cdn.sh" --apply --prune --only repo --only preview ||
    luma_os_log "origin retention did not complete (exit $?); retried next run"
fi

if [ -n "$resume" ]; then
  build_dir="$LUMA_OS_ROOT/builds/$resume"
  [ -s "$build_dir/build.env" ] || fail_run "no completed build to resume: $resume"
  build_id=$resume
  luma_os_load_env "$build_dir/build.env"
  os="$luma_os_repo_root/scripts/os"
  set_status --set "build_id=\"$build_id\"" --set "version=\"$LUMA_OS_VERSION\"" \
    --set "source_revision=\"$LUMA_SOURCE_REVISION\"" --set "resumed=true" --set 'state="exporting"'
else
# Source: a clean checkout of the branch from the pipeline mirror.
mirror="$LUMA_OS_ROOT/git/ProjectLuma.git"
checkout="$LUMA_OS_ROOT/src/nightly"
if [ ! -d "$checkout/.git" ]; then
  git clone --quiet "$mirror" "$checkout" || fail_run 'cannot clone the source mirror'
fi
git -C "$checkout" fetch --quiet origin "+refs/heads/$branch:refs/remotes/origin/$branch" ||
  fail_run "cannot fetch $branch from the source mirror"
git -C "$checkout" checkout --quiet --force --detach "origin/$branch"
git -C "$checkout" clean -fdxq
revision=$(git -C "$checkout" rev-parse HEAD)
set_status --set "source_branch=\"$branch\"" --set "source_revision=\"$revision\"" --set 'state="building"'

# Pipeline scripts come from the same revision as the recipe.
os="$checkout/scripts/os"
# System apps (config/desktop/system-apps.txt) must be pinned at their newest
# released build; an older pin fails the nightly before anything is built.
python3 "$os/lib/system_apps_current.py" pins --source "$checkout" --pool "$LUMA_OS_ROOT/rpms/pool" \
  --incoming "$LUMA_OS_ROOT/../incoming" >>"$run_log" 2>&1 ||
  fail_run "a system app is pinned older than its newest release: $(grep '^FAIL' "$run_log" | tail -n 1 | cut -c6-)"
build_dir=$("$os/build-image.sh" --source "$checkout" --base "$base_policy" | tail -n 1) ||
  fail_run 'image build failed'
build_id=$(basename "$build_dir")
luma_os_load_env "$build_dir/build.env"
image_bytes=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image"]["size_bytes"])' "$build_dir/provenance.json")
set_status --set "build_id=\"$build_id\"" --set "version=\"$LUMA_OS_VERSION\"" \
  --set "image_bytes=$image_bytes" --set "build_completed_utc=\"$LUMA_BUILD_COMPLETED\"" --set 'state="exporting"'

fi

if [ "$release_only" -eq 1 ]; then
  luma_os_load_env "$build_dir/export.env"
  candidate=$LUMA_EXPORT_CANDIDATE
  ostree show --repo="$LUMA_OS_ROOT/ostree/candidate-repo" "$candidate" >/dev/null 2>&1 ||
    fail_run "the candidate of $build_id is no longer exported"
  python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("result") == "pass" else 1)' "$build_dir/gate-result.json" ||
    fail_run "$build_id did not pass the gate"
  set_status --set "commit=\"$candidate\"" --set 'state="releasing"'
  gate=pass
else
candidate=$("$os/export-ostree.sh" --build-id "$build_id" | tail -n 1) || fail_run 'OSTree export failed'
set_status --set "commit=\"$candidate\"" --set 'state="gating"'

if "$os/gate.sh" --build-id "$build_id"; then
  gate=pass
else
  gate=fail
fi
fi
gate_json="$build_dir/gate-result.json"
set_status --set "gate_result=\"$gate\"" \
  --set "gate=$(python3 -c 'import json,sys; g=json.load(open(sys.argv[1])); print(json.dumps({k: g.get(k) for k in ("result","fresh","no_account","update","rollback","run_id","completed_utc")}))' "$gate_json" 2>/dev/null || echo null)"
if [ "$gate" != pass ]; then
  "$os/discard-candidate.sh" --build-id "$build_id" || true
  fail_run "gate failed for $build_id; nothing published"
fi

# Steps 1 and 2 run for every build, --no-publish candidates included, so a
# candidate fails on incomplete release notes before the timer does
# (2026-09-29: 20260928.1 passed its gate, then stopped here at 01:53).
{
  # 1. The day this nightly closes out (America/Chicago): the build id's day,
  #    or the Central day it was built on when that is earlier.
  nightly_date=$(python3 - "$build_id" "$LUMA_BUILD_COMPLETED" <<'PY'
import datetime, sys, zoneinfo
build, completed = sys.argv[1], sys.argv[2]
id_day = datetime.date(int(build[0:4]), int(build[4:6]), int(build[6:8]))
built = datetime.datetime.fromisoformat(completed.replace("Z", "+00:00")).astimezone(zoneinfo.ZoneInfo("America/Chicago")).date()
print(min(id_day, built).isoformat())
PY
) || fail_run 'cannot tell the nightly date'
  # The build fixed its label when it started (build.env LUMA_NIGHTLY_DATE,
  # which the image's os-release names), and a rerun that replaces an earlier
  # day's failed nightly passes that day's label the same way.
  [ -z "${LUMA_NIGHTLY_DATE:-}" ] || nightly_date=$LUMA_NIGHTLY_DATE
  set_status --set 'state="release-notes"' --set "nightly_date=\"$nightly_date\""
  # 2. Release notes: every package changed since the previous published
  #    nightly must be covered by a fragment, or nothing is published.
  channel_repo=$(luma_os_channel_repo nightly)
  previous_manifest= previous_source=
  if [ -s "$channel_repo/luma/channels/nightly.json" ]; then
    previous_version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$channel_repo/luma/channels/nightly.json")
    previous_build=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["build_id"])' "$channel_repo/luma/channels/nightly.json")
    previous_source=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_revision"])' "$channel_repo/luma/channels/nightly.json")
    for candidate_manifest in "$channel_repo/luma/releases/$previous_version/luma-packages.manifest" "$LUMA_OS_ROOT/builds/$previous_build/luma-packages.manifest"; do
      [ -s "$candidate_manifest" ] && previous_manifest=$candidate_manifest && break
    done
    [ -n "$previous_manifest" ] || fail_run "the previous nightly's package set ($previous_build) is gone; release notes cannot be checked"
  fi
  notes_repo=$luma_os_repo_root
  git -c "safe.directory=$notes_repo" -C "$notes_repo" merge-base --is-ancestor "$LUMA_SOURCE_REVISION" HEAD 2>/dev/null ||
    fail_run "the release notes source does not contain the build's source revision $LUMA_SOURCE_REVISION"
  notes="$build_dir/release-notes.json"
  python3 "$luma_os_repo_root/scripts/os/lib/release_notes.py" build --repo "$notes_repo" \
    --from "$previous_source" --to HEAD --previous-manifest "$previous_manifest" \
    --manifest "$build_dir/luma-packages.manifest" --exceptions "$luma_os_repo_root/config/os/release-notes-exceptions.txt" \
    --channel nightly --build-id "$build_id" --version "$LUMA_OS_VERSION" --nightly-date "$nightly_date" \
    --display-name "${LUMA_DISPLAY_NAME:-}" --out "$notes" >"$build_dir/release-notes.log" 2>&1 ||
    fail_run "release notes are incomplete: $(grep '^FAIL' "$build_dir/release-notes.log" | head -n 3 | cut -c7- | paste -sd';' -); nothing published"
  python3 "$luma_os_repo_root/scripts/os/lib/system_apps_current.py" notes --repo "$notes_repo" \
    --from "$previous_source" --to HEAD --previous-manifest "$previous_manifest" \
    --manifest "$build_dir/luma-packages.manifest" >>"$build_dir/release-notes.log" 2>&1 ||
    fail_run "a system app update has no \"Updated ... to X.Y\" note: $(grep '^FAIL' "$build_dir/release-notes.log" | tail -n 1 | cut -c6-); nothing published"
}
if [ "$publish" -eq 1 ]; then
  # 3. The release medium of this exact candidate, verified before anything is public.
  set_status --set 'state="verifying-media"'
  /usr/bin/bash "$luma_os_repo_root/scripts/os/nightly-media.sh" --channel nightly --candidate "$build_id" --no-publish ||
    fail_run "the release medium did not pass its checks; nothing published"
  # 4. Publish the update and the medium together.
  set_status --set 'state="publishing"'
  "$os/publish-channel.sh" --build-id "$build_id" || fail_run 'publication failed'
  set_status --set 'published=true' --set 'state="serving"'
  # Retention and the delta contract before anything is served or uploaded.
  "$os/prune.sh" --apply >>"$run_log" 2>&1 || fail_run 'retention after publication failed'
  install -m 0644 "$notes" "$(luma_os_channel_repo nightly)/luma/releases/$LUMA_OS_VERSION/notes.json"
  # The nightly graph: Hub's signing job owns graphs once it has its service
  # token; until then the nightly graph comes from the releases published here.
  if [ ! -r "$LUMA_OS_SECRETS/hub-graph-service-token" ]; then
    graph_source="$LUMA_OS_ROOT/tmp/nightly-graph-$build_id.json"
    python3 "$luma_os_repo_root/scripts/os/lib/graph_from_releases.py" \
      "$(luma_os_channel_repo nightly)" nightly "$LUMA_OS_ARCH" >"$graph_source" ||
      fail_run 'rendering the nightly graph failed'
    "$os/sign-update-graph.sh" --channel nightly --source "$graph_source" --no-sync ||
      fail_run 'signing the nightly graph failed'
    rm -f "$graph_source"
  fi
  /usr/bin/bash "$luma_os_repo_root/scripts/os/nightly-media.sh" --channel nightly --publish-verified "$build_id" \
    --notes "$notes" --nightly-date "$nightly_date" || fail_run 'publishing the release medium failed'
  "$os/stage-webroot.sh" || fail_run 'switching the served generation failed'
  sync_state=not-configured
  if "$os/sync-to-cdn.sh" --apply; then
    sync_state=uploaded
  else
    rc=$?
    [ "$rc" -eq 3 ] || fail_run "upload failed (exit $rc)"
  fi
  deltas=$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["deltas"]))' \
    "$(luma_os_channel_repo nightly)/luma/releases/$LUMA_OS_VERSION/manifest.json")
  set_status --set 'published=true' --set "upload=\"$sync_state\"" --set "deltas=$deltas" \
    --set "repo_bytes=$(du -sb "$(luma_os_channel_repo nightly)" | awk '{ print $1 }')"
fi

set_status --set 'state="succeeded"' --set "run_completed_utc=\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
cp "$status" "$build_dir/status.json"
luma_os_log "nightly $build_id ($LUMA_OS_VERSION) $candidate: gate $gate, published=$publish"
