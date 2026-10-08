#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Promote a release to the next channel without rebuilding (ADR-030 section 4).
#
#   promote-channel.sh --from nightly --to beta   --commit SHA256 [--dry-run]
#   promote-channel.sh --from beta    --to stable --commit SHA256 --version 1.0.0 [--dry-run]
#                      [--soak-override "reason"] [--summary TEXT] [--notes-url URL]
#
# The promoted commit is a new commit on the target ref whose tree is the
# source commit's tree, object for object (verified), with the target
# channel's version, the source build's provenance metadata, and
# org.projectluma.promoted-from naming the source commit. It is signed, its
# deltas from the target channel's previous heads are generated, the target
# ref moves forward, the summary is signed, and a fresh client verifies it.
#
# Rules: nightly -> beta -> stable only; the source commit must be a published
# release on the source channel with its gate record; it must have been on the
# source channel for 24 hours (beta) or 72 hours (stable) unless an override
# reason is recorded; versions only increase; stable needs an explicit version.
# --dry-run prints the plan and changes nothing.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"
. "$(dirname -- "$0")/lib/channel.sh"

from=
to=
source_commit=
version=
soak_override=
summary=
notes_url=
dry_run=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --from) from=${2:?}; shift 2 ;;
    --to) to=${2:?}; shift 2 ;;
    --commit) source_commit=${2:?}; shift 2 ;;
    --version) version=${2:?}; shift 2 ;;
    --soak-override) soak_override=${2:?}; shift 2 ;;
    --summary) summary=${2:?}; shift 2 ;;
    --notes-url) notes_url=${2:?}; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    *) printf 'usage: %s --from CHANNEL --to CHANNEL --commit SHA [--version V] [--soak-override REASON] [--summary TEXT] [--notes-url URL] [--dry-run]\n' "$0" >&2; exit 2 ;;
  esac
done

case "$from:$to" in
  nightly:beta) soak_hours=24 ;;
  beta:stable) soak_hours=72 ;;
  *) luma_os_die "promotion must be nightly -> beta or beta -> stable, not $from -> $to" ;;
esac
[[ "$source_commit" =~ ^[0-9a-f]{64}$ ]] || luma_os_die '--commit must be a full SHA-256 commit checksum'

luma_os_require_root
luma_os_require_tools ostree gpg python3 flock
luma_os_check_host

from_ref=$(luma_os_channel_ref "$from")
to_ref=$(luma_os_channel_ref "$to")
from_repo=$(luma_os_channel_repo "$from")
to_repo=$(luma_os_channel_repo "$to")
[ -f "$from_repo/config" ] || luma_os_die "source channel repository does not exist: $from_repo"

# The source commit must be on the source channel's history.
on_channel=0
walk=$(luma_os_channel_head "$from_repo" "$from_ref")
while [ -n "$walk" ]; do
  if [ "$walk" = "$source_commit" ]; then on_channel=1; break; fi
  walk=$(ostree rev-parse --repo="$from_repo" "$walk^" 2>/dev/null || true)
done
[ "$on_channel" -eq 1 ] || luma_os_die "$source_commit is not a release on $from_ref"

meta() { python3 "$luma_os_repo_root/scripts/os/lib/ostree_metadata.py" "$@"; }
source_version=$(meta version "$from_repo" "$source_commit")
source_release="$from_repo/luma/releases/$source_version"
[ -s "$source_release/manifest.json" ] && [ -s "$source_release/gate-result.json" ] ||
  luma_os_die "source release metadata is missing: $source_release"
# A tree names its own release in os-release (ADR-040): a nightly says "Luma
# (Prairie, Beta 0, Nightly 20260916)". Promoting it unchanged would show a
# beta or stable computer a nightly's name, so such a tree is never promoted;
# the beta or stable release is built from the same source with its own
# identity (config/os/release.env). Trees from before ADR-040's names carry no
# LUMA_RELEASE_CHANNEL and keep the old rule.
tree_channel=$(sed -n 's/^LUMA_RELEASE_CHANNEL=//p' "$source_release/os-release" 2>/dev/null | tr -d '"' | tail -n 1)
[ -z "$tree_channel" ] || [ "$tree_channel" = "$to" ] ||
  luma_os_die "$source_version names itself a $tree_channel release in its os-release; build the $to release instead of promoting it"
python3 - "$source_release/gate-result.json" "$source_release/manifest.json" "$source_commit" <<'PY' ||
import json, sys
gate = json.load(open(sys.argv[1], encoding="utf-8"))
manifest = json.load(open(sys.argv[2], encoding="utf-8"))
commit = sys.argv[3]
root = manifest.get("promoted_from") or {}
tested = gate.get("commit")
# A nightly's gate tested this commit; a beta's gate record is the nightly's,
# whose commit the beta manifest names as promoted_from.
ok = gate.get("result") == "pass" and (tested == commit or tested == root.get("commit"))
sys.exit(0 if ok else 1)
PY
  luma_os_die "no passing gate record for $source_commit"

published=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["published_utc"])' "$source_release/manifest.json")
age_hours=$(python3 -c 'import sys; from datetime import datetime, timezone; t = datetime.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc); print(int((datetime.now(timezone.utc) - t).total_seconds() // 3600))' "$published")
if [ "$age_hours" -lt "$soak_hours" ]; then
  [ -n "$soak_override" ] ||
    luma_os_die "$source_version has been on $from for ${age_hours}h; $to requires ${soak_hours}h (or --soak-override with a reason)"
  luma_os_log "soak override recorded: $soak_override"
fi

luma_os_init_channel_repo "$to_repo"
to_head=$(luma_os_channel_head "$to_repo" "$to_ref")
head_version=
[ -z "$to_head" ] || head_version=$(meta version "$to_repo" "$to_head")

# Target version.
case "$to" in
  beta)
    base=$LUMA_OS_PRODUCT_VERSION
    n=1
    if [[ "$head_version" =~ ^${base//./\\.}-beta\.([0-9]+)$ ]]; then
      n=$((BASH_REMATCH[1] + 1))
    fi
    [ -n "$version" ] || version="$base-beta.$n"
    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+$ ]] || luma_os_die "invalid beta version: $version"
    ;;
  stable)
    [ -n "$version" ] || luma_os_die 'a stable promotion needs --version (for example 1.0.0)'
    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || luma_os_die "invalid stable version: $version"
    ;;
esac
if [ -n "$head_version" ]; then
  python3 "$luma_os_repo_root/scripts/os/lib/versions.py" greater "$version" "$head_version" ||
    luma_os_die "version $version is not newer than $to head $head_version"
fi
[ ! -e "$to_repo/luma/releases/$version" ] || luma_os_die "release $version already exists on $to"

if [ "$dry_run" -eq 1 ]; then
  cat <<EOF
would promote:
  source:  $source_commit ($source_version) on $from_ref, published $published (${age_hours}h ago)
  target:  $to_ref in $to_repo
  version: $version
  parent:  ${to_head:-none (first release of $to)}${head_version:+ ($head_version)}
  tree:    $(meta root-dirtree "$from_repo" "$source_commit") (unchanged)
  soak:    ${soak_hours}h required${soak_override:+; override: $soak_override}
EOF
  exit 0
fi

exec 6>"$LUMA_OS_ROOT/locks/publish-$to.lock"
flock -n 6 || luma_os_die "another publication to $to is running"
luma_os_check_space 15
luma_os_gpg_unlock
trap luma_os_gpg_lock EXIT

if [ "$from_repo" != "$to_repo" ]; then
  ostree pull-local --repo="$to_repo" --untrusted "$from_repo" "$source_commit" >/dev/null
fi

mapfile -t copied < <(meta copy-args "$from_repo" "$source_commit" \
  ostree.bootable ostree.linux rpmostree.rpmdb.pkglist \
  org.projectluma.build-id org.projectluma.build-version org.projectluma.source-revision \
  org.projectluma.source-dirty org.projectluma.base-image-digest \
  org.projectluma.provenance-sha256 org.projectluma.sbom-sha256 org.projectluma.image-tree)
parent_args=(--orphan)
[ -z "$to_head" ] || parent_args=(--orphan --parent="$to_head")
override_args=()
[ -z "$soak_override" ] || override_args=(--add-metadata-string="org.projectluma.soak-override=$soak_override")
# The promoted commit keeps the source commit's timestamp. libostree refuses
# to move a device to a commit older than its booted one, so a promotion dated
# at promotion time could be refused by a device that booted a newer nightly
# or beta build of the same line; the release is judged newer by its version.
timestamp=$(meta timestamp "$from_repo" "$source_commit")
[ -n "$timestamp" ] || luma_os_die "could not read the timestamp of $source_commit"
promoted=$(ostree commit --repo="$to_repo" "${parent_args[@]}" \
  --tree="ref=$source_commit" \
  --bind-ref="$to_ref" \
  --timestamp="$timestamp" \
  --subject="Luma $version" \
  --body="Promoted from $from $source_version ($source_commit)" \
  --add-metadata-string="version=$version" \
  --add-metadata-string="org.projectluma.channel=$to" \
  --add-metadata-string="org.projectluma.promoted-from=$source_commit" \
  --add-metadata-string="org.projectluma.promoted-from-channel=$from" \
  --add-metadata-string="org.projectluma.promoted-from-version=$source_version" \
  "${override_args[@]}" \
  "${copied[@]}")
meta same-tree "$to_repo" "$source_commit" "$promoted" ||
  luma_os_die 'promoted commit does not carry the source tree exactly'
ostree gpg-sign --repo="$to_repo" --gpg-homedir="$(luma_os_gpg_home)" "$promoted" "$(luma_os_gpg_fingerprint)"
deltas=$(luma_os_advance_channel "$to_repo" "$to_ref" "$promoted" | tail -n 1)

published_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
release_dir="$to_repo/luma/releases/$version"
install -d -m 0755 "$release_dir"
cp "$source_release/provenance.json" "$source_release/sbom.spdx.json" "$source_release/gate-result.json" \
  "$source_release/os-release" "$source_release/packages.tsv" "$release_dir/"
python3 "$luma_os_repo_root/scripts/os/lib/provenance.py" manifest \
  --output "$release_dir/manifest.json" \
  --version "$version" --channel "$to" --ref "$to_ref" \
  --commit "$promoted" --parent "$to_head" \
  --root-dirtree "$(meta root-dirtree "$to_repo" "$promoted")" \
  --provenance "$release_dir/provenance.json" --gate "$release_dir/gate-result.json" \
  --deltas "$deltas" --published "$published_utc" \
  --promoted-from "$source_commit" --promoted-from-channel "$from" \
  --promoted-from-version "$source_version"
python3 - "$release_dir/manifest.json" "$summary" "$notes_url" "$soak_override" <<'PY'
import json, sys
path, summary, notes, override = sys.argv[1:5]
manifest = json.load(open(path, encoding="utf-8"))
manifest["summary"] = summary or None
manifest["notes_url"] = notes or None
manifest["soak_override"] = override or None
with open(path, "w", encoding="utf-8") as stream:
    json.dump(manifest, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
for file in manifest.json provenance.json sbom.spdx.json gate-result.json; do
  luma_os_sign_file "$release_dir/$file"
done
install -d -m 0755 "$to_repo/luma/channels" "$to_repo/luma/keys"
cp "$release_dir/manifest.json" "$to_repo/luma/channels/$to.json"
luma_os_sign_file "$to_repo/luma/channels/$to.json"
install -m 0644 "$LUMA_OS_KEYS/luma-os-release.gpg" "$LUMA_OS_KEYS/luma-os-release.asc" "$to_repo/luma/keys/"

luma_os_log "promoted $source_version ($source_commit) to $to as $version ($promoted)"
printf '%s\n' "$promoted"
