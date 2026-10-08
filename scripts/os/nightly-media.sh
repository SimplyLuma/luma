#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Nightly installer media: every release published on a media channel gets
# ready-to-flash USB media carrying exactly that release, built, verified and
# published without anyone running a command.
#
#   nightly-media.sh [--channel nightly] [--no-publish] [--rebuild] [--interim]
#
# --interim (owner request 2026-09-16, for a release that predates the
# out-of-box checks): the medium is verified for what it already ships (no
# credential, UEFI unattended install boots this build, the installer
# renders) without LUMA_MEDIA_REQUIRED_CHECKS, the expected packages, the
# enrollment expectation or the update path, and its index entry says
# "interim": true. The next full run supersedes it.
#
# Run by luma-os-media.service, which luma-os-nightly.service starts on
# success; the image is already published by then, so media never hold up the
# nightly. It takes the channel's current head (scripts/os/publish-channel.sh
# published it) and does nothing when that release already has published media.
#
# 1. Build one medium, luma-<channel>-<build>.iso, carrying no credential
#    (scripts/os/build-staff-media.sh, Atlas from config/os/media.env).
# 2. Verify, each in a disposable UEFI VM (scripts/os/verify-media.sh):
#      a fresh install from the medium is this build, carries the packages
#        media.env names, holds no credential, offers the channel choice and
#        passes every release-blocking out-of-box check in
#        LUMA_MEDIA_REQUIRED_CHECKS (Viola is the default browser, update
#        channels, the live Depot store, the install commands, Fedora cannot
#        replace Luma packages), with no SELinux denials, plus verify-media.sh's
#        own checks (release, remote, key, image contract);
#      the previous verified medium, installed fresh, picks Nightly, receives
#        this build as a delta from the real origin, boots it, rolls back and
#        returns to Official;
#      scripts/os/verify-media-ui.sh: the interactive installer renders.
# 3. Publish only when everything passed: the medium, its .sha256, latest.json
#    and the stable luma-<channel>-latest.iso (a hard link to the newest
#    medium) are served from $LUMA_OS_ROOT/publish/media/public/<channel> and
#    uploaded (scripts/os/sync-to-cdn.sh --only media-tree). Retention:
#    LUMA_MEDIA_KEEP, fewer when the origin budget cannot hold them. A failure
#    publishes nothing, the previous medium stays the latest, and the unit's
#    OnFailure alert fires (scripts/os/alert.sh).
#
# Status: $LUMA_OS_ROOT/status/media-<channel>.json, updated at every step,
# including timings and sizes. Evidence: $LUMA_OS_ROOT/media/<channel>/<build>/.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"
. "$(dirname -- "$0")/lib/media-transport.sh"
luma_os_load_env "$luma_os_repo_root/config/os/media.env"

channel=nightly
publish=1
rebuild=0
interim=0
candidate=
publish_verified=
notes=
nightly_date_arg=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --channel) channel=${2:?}; shift 2 ;;
    --no-publish) publish=0; shift ;;
    --rebuild) rebuild=1; shift ;;
    --interim) interim=1; shift ;;
    --candidate) candidate=${2:?}; shift 2 ;;
    --publish-verified) publish_verified=${2:?}; shift 2 ;;
    --notes) notes=$(realpath "${2:?}"); shift 2 ;;
    --nightly-date) nightly_date_arg=${2:?}; shift 2 ;;
    *) printf 'usage: %s [--channel nightly] [--no-publish] [--rebuild] [--interim] [--candidate BUILD_ID [--no-publish] | --publish-verified BUILD_ID --notes FILE --nightly-date YYYY-MM-DD]\n' "$0" >&2; exit 2 ;;
  esac
done
case " $LUMA_MEDIA_CHANNELS " in *" $channel "*) ;; *) luma_os_die "no media are made for channel $channel (config/os/media.env)" ;; esac

luma_os_require_root
luma_os_check_host
install -d -m 0755 "$LUMA_OS_ROOT/locks" "$LUMA_OS_ROOT/status" "$LUMA_OS_ROOT/media/$channel" "$LUMA_OS_ROOT/publish/media"
exec 6>"$LUMA_OS_ROOT/locks/media-$channel.lock"
flock -n 6 || { luma_os_log "media for $channel are already being made"; exit 0; }

status="$LUMA_OS_ROOT/status/media-$channel.json"
set_status() {
  python3 - "$status" "$@" <<'PY'
import json, sys, os
path, pairs = sys.argv[1], sys.argv[2:]
data = json.load(open(path)) if os.path.exists(path) else {}
for pair in pairs:
    key, value = pair.split("=", 1)
    data[key] = json.loads(value)
data["schema"] = "org.projectluma.os-media-status/v1"
tmp = path + ".new"
json.dump(data, open(tmp, "w"), indent=2, sort_keys=True)
open(tmp, "a").write("\n")
os.replace(tmp, path)
PY
}
jstr() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# The release: the channel's published head.
repo=$(luma_os_channel_repo "$channel")
ref=$(luma_os_channel_ref "$channel")
if [ -n "$candidate$publish_verified" ]; then
  # The atomic nightly (docs/os/release-process.md): the medium of a gated,
  # not yet published candidate is built and verified first (--candidate
  # --no-publish), and published with the update (--publish-verified).
  build_id=${candidate:-$publish_verified}
  luma_os_load_env "$LUMA_OS_ROOT/builds/$build_id/build.env"
  luma_os_load_env "$LUMA_OS_ROOT/builds/$build_id/export.env"
  repo="$LUMA_OS_ROOT/ostree/candidate-repo"
  head=$LUMA_EXPORT_CANDIDATE
  version=$LUMA_OS_VERSION
  ostree show --repo="$repo" "$head" >/dev/null 2>&1 || repo=$(luma_os_channel_repo "$channel")
else
  head=$(ostree rev-parse --repo="$repo" "$ref" 2>/dev/null) || luma_os_die "no published release on $ref"
  version=$(python3 "$luma_os_repo_root/scripts/os/lib/ostree_metadata.py" version "$repo" "$head")
fi
build_id=${build_id:-${version##*-"$channel".}}
[[ "$build_id" =~ ^[0-9]{8}\.[0-9]+$ ]] || luma_os_die "cannot tell the build id of $version"
dir="$LUMA_OS_ROOT/media/$channel/$build_id"
if [ "$rebuild" -eq 0 ] && [ -f "$dir/result.json" ] &&
   python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("result") == "pass" and d.get("published") else 1)' "$dir/result.json"; then
  luma_os_log "media for $version are already published"
  exit 0
fi

if [ -n "$publish_verified" ]; then
  [ -n "$notes" ] && [ -s "$notes" ] || luma_os_die 'publishing a verified medium needs its release notes (--notes)'
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("result") == "pass" and d.get("commit") == sys.argv[2] else 1)' \
    "$dir/result.json" "$head" 2>/dev/null || luma_os_die "no verified medium of $build_id ($head) to publish"
fi
job_log="$LUMA_OS_ROOT/logs/media-$channel-$build_id-$(date -u +%Y%m%dT%H%M%SZ).log"
install -d -m 0755 "$LUMA_OS_ROOT/logs"
exec > >(tee -a "$job_log") 2>&1
export LUMA_OS_JOB_LOG="$job_log"
started=$SECONDS
if [ -z "$publish_verified" ]; then
rm -f "$status"
set_status "state=\"starting\"" "channel=$(jstr "$channel")" "build_id=$(jstr "$build_id")" \
  "version=$(jstr "$version")" "commit=$(jstr "$head")" "started_utc=$(jstr "$(now)")" \
  "result=null" "published=false" "error=null" "log=$(jstr "$(basename "$job_log")")"
fi
fail() {
  set_status "state=\"failed\"" "result=\"fail\"" "error=$(jstr "$1")" "completed_utc=$(jstr "$(now)")" \
    "duration_seconds=$((SECONDS - started))"
  [ -d "$dir" ] && cp "$status" "$dir/result.json" 2>/dev/null || true
  luma_os_die "$1 (nothing published; the previous media stay the latest)"
}
step() { set_status "state=$(jstr "$1")" "step_${1}_started_utc=$(jstr "$(now)")"; step_started=$SECONDS; }
step_done() { set_status "step_${1}_seconds=$((SECONDS - step_started))"; }

if [ -z "$publish_verified" ]; then
# Room: this build host's own older media first, then the pipeline's guard.
mapfile -t old < <(find "$LUMA_OS_ROOT/media/$channel" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended \
  -regex '.*/[0-9]{8}\.[0-9]+' ! -name "$build_id" -printf '%f\n' | sort -t. -k1,1n -k2,2n | head -n "-$((LUMA_MEDIA_LOCAL_KEEP - 1))")
for id in "${old[@]}"; do
  # Media still served stay on this host: the served tree hard-links them.
  rm -rf -- "${LUMA_OS_ROOT:?}/media/$channel/$id"
  luma_os_log "removed local media of $id"
done
luma_os_check_space 35 || fail 'not enough disk space for installer media'
rm -rf "$dir"
install -d -m 0755 "$dir"

# Atlas: the pinned revision from the pipeline mirror, and the RPM with its
# pinned digest.
step atlas
atlas="$LUMA_OS_ROOT/tmp/media-atlas-${LUMA_MEDIA_ATLAS_REVISION:0:12}"
if [ "$(git -C "$atlas" rev-parse HEAD 2>/dev/null)" != "$LUMA_MEDIA_ATLAS_REVISION" ]; then
  rm -rf "$atlas"
  git clone --quiet --no-checkout "$LUMA_OS_ROOT/git/ProjectLuma.git" "$atlas" || fail 'cannot clone the source mirror for Atlas'
  git -C "$atlas" fetch --quiet origin "+refs/heads/$LUMA_MEDIA_ATLAS_BRANCH:refs/remotes/origin/atlas" ||
    fail "the mirror has no $LUMA_MEDIA_ATLAS_BRANCH"
  git -C "$atlas" merge-base --is-ancestor "$LUMA_MEDIA_ATLAS_REVISION" origin/atlas ||
    fail "Atlas $LUMA_MEDIA_ATLAS_REVISION is not on $LUMA_MEDIA_ATLAS_BRANCH"
  git -C "$atlas" checkout --quiet --detach "$LUMA_MEDIA_ATLAS_REVISION" || fail 'cannot check out the pinned Atlas revision'
fi
atlas_rpms="$LUMA_OS_ROOT/rpms/media/$LUMA_MEDIA_ATLAS_NEVRA"
if [ "$(sha256sum "$atlas_rpms/$LUMA_MEDIA_ATLAS_NEVRA.rpm" 2>/dev/null | cut -d' ' -f1)" != "$LUMA_MEDIA_ATLAS_SHA256" ]; then
  # Its own name: $candidate is the build id of --candidate.
  atlas_rpm=$(find "$LUMA_OS_ROOT/../incoming" /srv/luma-build/incoming -name "$LUMA_MEDIA_ATLAS_NEVRA.rpm" 2>/dev/null |
    while read -r file; do [ "$(sha256sum "$file" | cut -d' ' -f1)" = "$LUMA_MEDIA_ATLAS_SHA256" ] && echo "$file"; done | head -n 1)
  [ -n "$atlas_rpm" ] || fail "no $LUMA_MEDIA_ATLAS_NEVRA.rpm with the pinned SHA-256 in incoming"
  rm -rf "$atlas_rpms"
  install -d -m 0755 "$atlas_rpms"
  install -m 0644 "$atlas_rpm" "$atlas_rpms/"
fi
step_done atlas

# The preview credential, only to prove the medium holds it nowhere.
credential=
[ -f "$LUMA_OS_SECRETS/dl-origin.env" ] && credential=$(sed -n 's/^DL_PREVIEW_CREDENTIAL_FILE=//p' "$LUMA_OS_SECRETS/dl-origin.env")
forbid_args=()
[ -n "$credential" ] && [ -r "$credential" ] && forbid_args=(--forbid-credential-file "$credential")

# Packages every medium's system must carry, and that this release pins.
expect_args=()
release_packages="$repo/luma/releases/$version/packages.tsv"
[ -z "$candidate" ] || release_packages="$LUMA_OS_ROOT/builds/$build_id/packages-installed.tsv"
[ "$interim" -eq 1 ] && LUMA_MEDIA_EXPECT_PACKAGES=' '
for package in ${LUMA_MEDIA_EXPECT_PACKAGES:-viola-browser-stable luma-charlie}; do
  grep -q "^$package[[:space:]]" "$release_packages" 2>/dev/null ||
    fail "release $version does not carry $package, which its media must"
  expect_args+=(--expect-package "$package")
done
# The release-blocking out-of-box checks (tests/os/gate/*.sh from this
# release's source) on the system installed from the medium. In media.env a
# "+" stands for a space inside one check's arguments.
check_args=()
[ "$interim" -eq 1 ] && LUMA_MEDIA_REQUIRED_CHECKS=
for spec in $LUMA_MEDIA_REQUIRED_CHECKS; do
  check_args+=(--require-check "${spec//+/ }")
done
if [ -n "$candidate" ]; then
  source_revision=$LUMA_SOURCE_REVISION
else
  source_revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_revision"])' "$repo/luma/releases/$version/manifest.json") ||
    fail "no published release manifest for $version"
fi

iso="$dir/luma-$channel-$build_id.iso"
step build
"$luma_os_repo_root/scripts/os/build-staff-media.sh" --atlas-source "$atlas" --atlas-rpms "$atlas_rpms" \
  --channel "$channel" ${candidate:+--candidate "$candidate"} --output "$iso" || fail 'building the medium failed'
step_done build
set_status "iso_bytes=$(stat -c %s "$iso")" "iso_sha256=$(jstr "$(cat "$iso.sha256")")"

failures_of() { grep '^FAIL' "$1" | cut -c7- | paste -sd';' - | cut -c1-600; }

step verify_install
scope_args=(--expect-enrollment none "${check_args[@]}")
[ "$interim" -eq 1 ] && scope_args=(--skip-release-checks)
set_status "interim=$([ "$interim" -eq 1 ] && echo true || echo false)"
"$luma_os_repo_root/scripts/os/verify-media.sh" --iso "$iso" "${forbid_args[@]}" \
  --expect-build-id "$build_id" "${expect_args[@]}" "${scope_args[@]}" >"$dir/verify-install.log" 2>&1 ||
  fail "a fresh install from the medium failed verification: $(failures_of "$dir/verify-install.log") (see verify-install.log)"
step_done verify_install

# The update path out of the box: the previous verified medium, installed
# fresh, picks Nightly and receives this build as a delta from the real
# origin, boots it, rolls back and returns to Official. The first nightly
# medium has no predecessor.
step verify_update
previous=$(find "$LUMA_OS_ROOT/media/$channel" -mindepth 2 -maxdepth 2 -regextype posix-extended \
  -regex ".*/[0-9]{8}\.[0-9]+/luma-$channel-[0-9]{8}\.[0-9]+\.iso" ! -path "*/$build_id/*" -printf '%h\n' |
  while read -r d; do
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("result") == "pass" else 1)' "$d/result.json" 2>/dev/null && basename "$d"
  done | sort -t. -k1,1n -k2,2n | tail -n 1)
if [ -n "$candidate" ]; then
  # Before publication there is nothing on the origin to update to; the gate's
  # update, rollback and channel stages proved this build's update path.
  luma_os_log "candidate medium: the update path was proved by the gate"
  set_status "update_verified_from=null"
elif [ "$interim" -eq 1 ]; then
  luma_os_log "interim medium: the update path is checked by the next full run"
  set_status "update_verified_from=null"
elif [ -n "$previous" ]; then
  "$luma_os_repo_root/scripts/os/verify-media.sh" --iso "$LUMA_OS_ROOT/media/$channel/$previous/luma-$channel-$previous.iso" \
    --checks-revision "$source_revision" --update-from-origin-to "$version" >"$dir/verify-update.log" 2>&1 ||
    fail "updating an install from the $previous medium to $version failed: $(failures_of "$dir/verify-update.log") (see verify-update.log)"
  set_status "update_verified_from=$(jstr "$previous")"
else
  luma_os_log "no earlier verified $channel medium on this host: the update path is checked from the next build on"
  set_status "update_verified_from=null"
fi
step_done verify_update

step verify_ui
/usr/bin/bash "$luma_os_repo_root/scripts/os/verify-media-ui.sh" --iso "$iso" >"$dir/verify-ui.log" 2>&1 ||
  fail 'the interactive installer did not render from the medium (see verify-ui.log)'
step_done verify_ui
set_status "result=\"pass\""

if [ "$publish" -eq 0 ]; then
  set_status "state=\"verified\"" "completed_utc=$(jstr "$(now)")" "duration_seconds=$((SECONDS - started))"
  cp "$status" "$dir/result.json"
  luma_os_log "media for $version verified; not published (--no-publish)"
  exit 0
fi
else
  iso="$dir/luma-$channel-$build_id.iso"
  [ -s "$iso" ] && [ -s "$iso.sha256" ] || fail "the verified medium $iso is missing"
  interim=$(python3 -c 'import json,sys; print(1 if json.load(open(sys.argv[1])).get("interim") else 0)' "$dir/result.json")
  set_status "state=\"publishing\"" "publish_started_utc=$(jstr "$(now)")"
fi

# The served tree: the newest media of the channel as hard links to the build
# directories, with the stable latest name and latest.json. Nightly media are
# open (owner decision 2026-09-16): no credential, an unlisted public path.
step publish
served="$LUMA_OS_ROOT/publish/media"
tree="$served/public/$channel"
latest="luma-$channel-latest.iso"
base="https://dl.simplyluma.com/$LUMA_MEDIA_PUBLIC_PREFIX/$channel"
install -d -m 0755 "$tree"
name=$(basename "$iso")
ln -f "$iso" "$tree/$name"
luma_media_transport_link "$iso" "$tree/$name.download"
install -m 0644 "$iso.sha256" "$tree/$name.sha256"
ln -f "$iso" "$tree/.$latest.new" && mv -f "$tree/.$latest.new" "$tree/$latest"
install -m 0644 "$iso.sha256" "$tree/$latest.sha256"
luma_media_transport_link "$iso" "$tree/$latest.download"
# This medium's entry for the channel index (latest.json, below), kept beside
# the medium so the index can list every medium the origin still serves.
committed=$(ostree show --repo="$repo" "$head" | sed -n 's/^Date: *//p')
# Release notes, signed beside the index (schema org.projectluma.os-release-notes/v1,
# scripts/os/lib/release_notes.py): notes/<build_id>.json and .minisig.
notes_url=
notes_summary=
if [ -n "$notes" ]; then
  install -d -m 0755 "$tree/notes"
  install -m 0644 "$notes" "$tree/notes/$build_id.json.new"
  notes_generated=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generated_utc"])' "$notes")
  LUMA_OS_TOOLS_MOUNTS="$LUMA_OS_KEYS/update-graph-minisign $tree" luma_os_tools sh -c '
    set -e
    minisign -S -s "$1/luma-update-graph.key" -m "$2" -x "$2.minisig" -t "$3" >/dev/null
    minisign -V -p "$1/luma-update-graph.pub" -m "$2" -x "$2.minisig" >/dev/null
  ' sh "$LUMA_OS_KEYS/update-graph-minisign" "$tree/notes/$build_id.json.new" \
    "luma-release-notes channel=$channel build_id=$build_id generated_utc=$notes_generated" \
    <"$LUMA_OS_SECRETS/update-graph-minisign.password" || fail 'signing the release notes failed'
  mv -f "$tree/notes/$build_id.json.new.minisig" "$tree/notes/$build_id.json.minisig"
  mv -f "$tree/notes/$build_id.json.new" "$tree/notes/$build_id.json"
  notes_url="$base/notes/$build_id.json"
  notes_summary=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["summary"])' "$notes")
fi
python3 - "$tree/$name.json" "$channel" "$build_id" "$version" "$head" "$name" \
  "$(cat "$iso.sha256")" "$(stat -c %s "$iso")" "$base" "$interim" "$committed" "$nightly_date_arg" "$notes_url" "$notes_summary" "${LUMA_DISPLAY_NAME:-}" "$luma_os_repo_root/scripts/os/lib" <<'ENTRY'
import json, sys, datetime, zoneinfo
sys.path.insert(0, sys.argv[-1])
path, channel, build, version, commit, name, sha, size, base, interim, committed, nightly_arg, notes_url, notes_summary, display_arg, lib = sys.argv[1:]
# The nightly's labelled day: the day the build id names, or the Central day
# the build was made on when that is earlier (20260917.3 was built on the
# evening of the 16th and is the 16th's nightly).
id_day = datetime.date(int(build[0:4]), int(build[4:6]), int(build[6:8]))
built = datetime.datetime.strptime(committed.strip(), "%Y-%m-%d %H:%M:%S %z")
built_day = built.astimezone(zoneinfo.ZoneInfo("America/Chicago")).date()
nightly_date = nightly_arg or min(id_day, built_day).isoformat()
json.dump({
    "build_id": build, "version": version, "channel": channel, "arch": "x86_64", "commit": commit,
    "nightly_date": nightly_date,
    # The release's name as people see it (ADR-040): the build's own, or for a
    # build from before release names, rendered from the release identity.
    "display_name": display_arg or __import__("release_identity").display_name(
        __import__("release_identity").load(), channel, nightly_date),
    "public": bool(notes_url) and interim != "1",
    "notes_url": notes_url or None,
    "summary": notes_summary or None,
    "published_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "file": name, "url": f"{base}/{name}.download",
    "download_file": name + ".download", "download_url": f"{base}/{name}.download",
    "sha256_url": f"{base}/{name}.sha256",
    "size_bytes": int(size), "sha256": sha.split()[0], "interim": interim == "1",
    # Whether the medium itself is still on the origin. The entry outlives it:
    # retention takes the download away and leaves the record.
    "iso_available": True,
}, open(path, "w"), indent=2, sort_keys=True)
open(path, "a").write("\n")
ENTRY

# Retention: the newest LUMA_MEDIA_KEEP media, then fewer while the origin
# budget cannot hold them: the repository generation, the served media and
# the origin's other trees (LUMA_MEDIA_DL_OTHER_GIB).
dl_max_gib=25
[ -f "$LUMA_OS_SECRETS/dl-origin.env" ] &&
  dl_max_gib=$(sed -n 's/^DL_MAX_GIB=//p' "$LUMA_OS_SECRETS/dl-origin.env" | tail -n 1) && [ -n "$dl_max_gib" ] || dl_max_gib=25
builds_in() {
  find "$tree" -maxdepth 1 -regextype posix-extended -regex ".*/luma-$channel-[0-9]{8}\.[0-9]+\.iso" -printf '%f\n' |
    sed "s/^luma-$channel-//; s/\.iso$//" | sort -t. -k1,1n -k2,2n
}
# Every build the origin still has a record of, downloadable or retired.
entries_in() {
  find "$tree" -maxdepth 1 -regextype posix-extended -regex ".*/luma-$channel-[0-9]{8}\.[0-9]+\.iso\.json" -printf '%f\n' |
    sed "s/^luma-$channel-//; s/\.iso\.json$//" | sort -t. -k1,1n -k2,2n
}
# Retiring a medium takes away the download, not the record of it: the entry
# and the signed release notes are a few kilobytes and are what lets the
# website show a history instead of whatever single build is downloadable
# today. The entry is marked so nothing offers a link that would 404.
drop_oldest() {
  local oldest
  oldest=$(builds_in | head -n 1)
  [ -n "$oldest" ] && [ "$oldest" != "$build_id" ] || return 1
  luma_media_transport_retire "$tree/luma-$channel-$oldest.iso"
  python3 - "$tree/luma-$channel-$oldest.iso.json" <<'RETIRE' || true
import datetime, json, sys
path = sys.argv[1]
try:
    entry = json.load(open(path))
except (OSError, ValueError):
    raise SystemExit(0)
entry["iso_available"] = False
entry["iso_retired_utc"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump(entry, open(path, "w"), indent=2, sort_keys=True)
open(path, "a").write("\n")
RETIRE
  luma_os_log "media retention: $oldest is no longer downloadable; its entry and notes stay"
}
# One public nightly per day: a rerun replaces that day's medium.
for other in $(builds_in); do
  [ "$other" != "$build_id" ] || continue
  same=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("nightly_date") == sys.argv[2])' \
    "$tree/luma-$channel-$other.iso.json" "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["nightly_date"])' "$tree/$name.json")" 2>/dev/null || echo False)
  if [ "$same" = True ]; then
    # A rerun replaces that day's medium outright: the build it replaces was
    # never a nightly people were given, so its record goes with it.
    luma_media_transport_retire "$tree/luma-$channel-$other.iso"
    rm -f -- "$tree/luma-$channel-$other.iso.sha256" "$tree/luma-$channel-$other.iso.json" \
      "$tree/notes/$other.json" "$tree/notes/$other.json.minisig"
    luma_os_log "media: $build_id replaces $other as that day's nightly"
  fi
done
while [ "$(builds_in | wc -l)" -gt "$LUMA_MEDIA_KEEP" ]; do drop_oldest || break; done
origin_bytes() {
  local local_bytes
  local_bytes=$(du -scbL "$LUMA_OS_ROOT/webroot/current/os" "$served" 2>/dev/null | tail -n 1 | cut -f1)
  echo $((local_bytes + ${LUMA_MEDIA_DL_OTHER_GIB:-0} * 1073741824))
}
while [ "$(origin_bytes)" -gt $((dl_max_gib * 1073741824)) ]; do drop_oldest || break; done
[ "$(origin_bytes)" -le $((dl_max_gib * 1073741824)) ] ||
  fail "the newest medium alone does not fit the origin budget of $dl_max_gib GiB"
set_status "served_builds=$(builds_in | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().split()))')" \
  "origin_bytes=$(origin_bytes)"

# The channel index the website and other clients read:
#   https://dl.simplyluma.com/<public prefix>/<channel>/latest.json (+ .minisig)
# Each entry's nightly_date is the day the nightly closes out (America/Chicago)
# and published_utc when it was published; date repeats published_utc for
# readers of the first index.
# schema org.projectluma.os-media-index/v1: channel, generated_utc, latest (the
# newest entry that can still be downloaded) and entries (every published build
# the origin has a record of, newest first, each with iso_available saying
# whether its medium is still there and url null when it is not; every other
# field, including file, size_bytes, sha256, sha256_url and notes_url, stays
# valid so a reader can still list the build),
# with build_id, version, display_name, channel, arch, commit, date, file, url,
# sha256_url, size_bytes and sha256. Signed with the minisign update-graph key
# (dl.simplyluma.com/os/keys/luma-update-graph.pub); the trusted comment binds
# the channel and generated_utc.
python3 - "$tree" "$channel" "$latest" "$base" "$(luma_os_channel_repo "$channel")" $(entries_in | sort -t. -k1,1nr -k2,2nr) <<'INDEX' || fail 'writing the media index failed'
import datetime, glob, json, os, sys
tree, channel, latest, base, published_repo, builds = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6:]
entries = []
for build in builds:
    path = os.path.join(tree, f"luma-{channel}-{build}.iso.json")
    if os.path.exists(path):
        entry = json.load(open(path))
        # Media published before release names (ADR-040) were all Prairie
        # Beta 0 nightlies.
        if "display_name" not in entry and entry.get("channel") == "nightly" and entry.get("nightly_date"):
            entry["display_name"] = f"Luma (Prairie, Beta 0, Nightly {entry['nightly_date'].replace('-', '')})"
        # Entries written before media retention kept records were only ever
        # written for media that were there.
        entry.setdefault("iso_available", True)
        # The record outlives the download: say so rather than offering a link
        # to a file the origin no longer has.
        if entry["iso_available"] and not os.path.exists(
                os.path.join(tree, f"luma-{channel}-{build}.iso")):
            entry["iso_available"] = False
            # Retention stamps the entry as it retires a medium; a medium that
            # went some other way is stamped here, so no entry ever says it is
            # gone without saying when.
            entry.setdefault("iso_retired_utc", datetime.datetime.now(
                datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        # Only the medium goes: its .sha256 stays beside the index and is a few
        # dozen bytes, so the checksum link keeps working and only the download
        # link is withdrawn.
        if not entry["iso_available"]:
            entry["url"] = None
            if "download_url" in entry:
                entry["download_url"] = None
        entries.append(entry)
if not entries:
    sys.exit("no media entries")
# Published releases of the channel with no public entry here (earlier nightlies
# without notes, same-day reruns, validation builds): kept for devices' update
# paths, listed only so a reader knows to hide them.
public = [e for e in entries if e.get("public", True)]
# The newest medium someone can actually download.
downloadable = [e for e in public if e.get("iso_available")]
newest = (downloadable or public or entries)[0]
listed = {e["build_id"] for e in entries}
superseded = []
for path in sorted(glob.glob(os.path.join(published_repo, "luma", "releases", "*", "manifest.json"))):
    manifest = json.load(open(path))
    if manifest.get("channel") != channel or manifest.get("build_id") in listed:
        continue
    superseded.append({"build_id": manifest.get("build_id"), "version": manifest.get("version"),
                       "published_utc": manifest.get("published_utc"), "public": False,
                       "note": f"Superseded; see the {datetime.date.fromisoformat(newest['nightly_date']).strftime('%b %-d').replace('Sep ', 'Sept ')} nightly"})
json.dump({
    "schema": "org.projectluma.os-media-index/v1", "channel": channel,
    "generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "latest_url": f"{base}/{latest}", "latest": newest, "entries": entries, "superseded": superseded,
}, open(os.path.join(tree, "latest.json.new"), "w"), indent=2, sort_keys=True)
open(os.path.join(tree, "latest.json.new"), "a").write("\n")
INDEX
generated=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generated_utc"])' "$tree/latest.json.new")
minisign_dir="$LUMA_OS_KEYS/update-graph-minisign"
LUMA_OS_TOOLS_MOUNTS="$minisign_dir $tree" luma_os_tools sh -c '
  set -e
  minisign -S -s "$1/luma-update-graph.key" -m "$2" -x "$2.minisig" -t "$3" >/dev/null
  minisign -V -p "$1/luma-update-graph.pub" -m "$2" -x "$2.minisig" >/dev/null
' sh "$minisign_dir" "$tree/latest.json.new" "luma-media-index channel=$channel generated_utc=$generated" \
  <"$LUMA_OS_SECRETS/update-graph-minisign.password" || fail 'signing the media index failed'
mv -f "$tree/latest.json.new.minisig" "$tree/latest.json.minisig"
mv -f "$tree/latest.json.new" "$tree/latest.json"

"$luma_os_repo_root/scripts/os/sync-to-cdn.sh" --apply --only media-tree || fail 'uploading the media failed'
step_done publish
set_status "state=\"published\"" "published=true" "completed_utc=$(jstr "$(now)")" \
  "duration_seconds=$((SECONDS - started))" "latest_url=$(jstr "$base/$latest")" "url=$(jstr "$base/$name")"
cp "$status" "$dir/result.json"
luma_os_log "media for $version published in $((SECONDS - started))s: $base/$latest"
