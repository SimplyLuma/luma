#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: promote-ostree-commit.sh \
  --source-repo PATH --commit SHA256 --repo PATH \
  --build-report PATH --image-sha256 SHA256 --source-revision GIT_SHA \
  --gpg-key FINGERPRINT --gpg-homedir PATH
EOF
  exit 2
}

source_repo=
commit=
publish_repo=
build_report=
image_sha256=
source_revision=
gpg_key=
gpg_homedir=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --source-repo) source_repo=${2:-}; shift 2 ;;
    --commit) commit=${2:-}; shift 2 ;;
    --repo) publish_repo=${2:-}; shift 2 ;;
    --build-report) build_report=${2:-}; shift 2 ;;
    --image-sha256) image_sha256=${2:-}; shift 2 ;;
    --source-revision) source_revision=${2:-}; shift 2 ;;
    --gpg-key) gpg_key=${2:-}; shift 2 ;;
    --gpg-homedir) gpg_homedir=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/update/recent.env"

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

for value in source_repo commit publish_repo build_report image_sha256 \
  source_revision gpg_key gpg_homedir; do
  [ -n "${!value}" ] || usage
done
for tool in flock git gpg ostree python3 sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || fail "required publishing tool is missing: $tool"
done
[ "$(uname -s)" = Linux ] || fail 'Recent publishing requires the Linux release builder'
[ -f "$source_repo/config" ] || fail "source OSTree repository is invalid: $source_repo"
[ -f "$build_report" ] || fail "build report is missing: $build_report"
[ -d "$gpg_homedir" ] || fail "GPG home is missing: $gpg_homedir"
[[ "$commit" =~ ^[0-9a-f]{64}$ ]] || fail 'deployment commit is not a SHA-256'
[[ "$image_sha256" =~ ^[0-9a-f]{64}$ ]] || fail 'image digest is not a SHA-256'
[[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || fail 'source revision is not a Git SHA-1'

gpg_fingerprint=$(gpg --batch --homedir "$gpg_homedir" \
  --with-colons --fingerprint "$gpg_key" 2>/dev/null |
  awk -F: '$1 == "fpr" { print $10; exit }')
[ -n "$gpg_fingerprint" ] || fail 'publishing key is not present in the supplied GPG home'

mkdir -p "$publish_repo"
exec 9>"$publish_repo/.luma-publish.lock"
flock -n 9 || fail 'another Recent publication is active'

if [ ! -f "$publish_repo/config" ]; then
  ostree init --repo="$publish_repo" --mode=archive
fi
mode=$(ostree config --repo="$publish_repo" get core.mode)
case "$mode" in
  archive|archive-z2) ;;
  *) fail "publication repository must use archive mode, found: $mode" ;;
esac
ostree config --repo="$publish_repo" set core.collection-id \
  "$LUMA_UPDATE_COLLECTION_ID"

accepted_commit=$commit
source_resolved=$(ostree rev-parse --repo="$source_repo" "$accepted_commit")
[ "$source_resolved" = "$accepted_commit" ] || fail 'source repository resolved a different deployment'
ostree fsck --repo="$source_repo" --quiet

# Copy by checksum without trusting the source repository's object store. The
# source deployment is normally bound to Fedora's original ref, so ref-binding
# verification is intentionally replaced by the exact accepted checksum here.
ostree pull-local --repo="$publish_repo" --untrusted \
  --disable-verify-bindings "$source_repo" "$commit"
ostree fsck --repo="$publish_repo" --quiet

report_sha256=$(sha256sum "$build_report" | awk '{print $1}')
completed_utc=$(awk -F= '$1 == "completed_utc" { sub(/^[^=]*=/, ""); print }' \
  "$build_report")
[[ "$completed_utc" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] ||
  fail 'build report has no valid completed_utc record'
previous=$(ostree rev-parse --repo="$publish_repo" \
  "$LUMA_UPDATE_REF" 2>/dev/null || true)

# Publish the accepted package filesystem as a coherent base image.
# Discard only the embedded ancestral RPMDB cache belonging to the builder;
# rpm-ostree must derive the new base sack from the actual exported RPMDB. Client-layer
# metadata describes the builder's local transaction and must not make clients
# interpret the provenance parent as their base. Preserve typed package/boot
# metadata and all other reviewed metadata through OSTree's native copy API.
metadata_policy="$repo_root/scripts/update/ostree-export-metadata.py"
metadata_keys=$(python3 "$metadata_policy" keep "$publish_repo" "$accepted_commit")
metadata_args=()
while IFS= read -r key; do
  [ -z "$key" ] || metadata_args+=("--keep-metadata=$key")
done <<<"$metadata_keys"
normalized_tree=$(python3 "$repo_root/scripts/update/ostree-export-tree.py" normalize "$publish_repo" "$accepted_commit")
commit=$(ostree commit --repo="$publish_repo" \
  --orphan \
  --parent="$accepted_commit" \
  --tree="ref=$normalized_tree" \
  --bind-ref="$LUMA_UPDATE_REF" \
  --timestamp="$completed_utc" \
  --subject="Project Luma Recent $source_revision" \
  --add-metadata-string="org.projectluma.source-revision=$source_revision" \
  --add-metadata-string="org.projectluma.accepted-deployment=$accepted_commit" \
  --add-metadata-string="org.projectluma.image-sha256=$image_sha256" \
  --add-metadata-string="org.projectluma.build-report-sha256=$report_sha256" \
  --add-metadata-string="org.projectluma.export-policy=base-image-v2" \
  "${metadata_args[@]}")

# Fail before signing or advancing any ref if normalization changed anything
# beyond the ancestral RPMDB cache or altered preserved metadata. Parent remains provenance, not a client base layer.
python3 "$metadata_policy" verify "$publish_repo" "$accepted_commit" "$commit"

if [ "$previous" = "$commit" ]; then
  printf 'Recent already points to %s\n' "$commit"
  exit 0
fi

ostree gpg-sign --repo="$publish_repo" --gpg-homedir="$gpg_homedir" \
  "$commit" "$gpg_fingerprint"

if [ "${LUMA_UPDATE_SKIP_STATIC_DELTA:-0}" != 1 ]; then
  if [ -n "$previous" ]; then
    ostree static-delta generate --repo="$publish_repo" \
      --from="$previous" --to="$commit"
  else
    ostree static-delta generate --repo="$publish_repo" \
      --empty --to="$commit"
  fi
fi

created_utc=$completed_utc
release_dir="$publish_repo/luma/releases"
mkdir -p "$release_dir"
manifest="$release_dir/$commit.json"
MANIFEST_PATH="$manifest" \
LUMA_MANIFEST_CHANNEL="$LUMA_UPDATE_CHANNEL" \
LUMA_MANIFEST_REF="$LUMA_UPDATE_REF" \
LUMA_MANIFEST_COMMIT="$commit" \
LUMA_MANIFEST_ACCEPTED="$accepted_commit" \
LUMA_MANIFEST_PREVIOUS="$previous" \
LUMA_MANIFEST_SOURCE="$source_revision" \
LUMA_MANIFEST_IMAGE="$image_sha256" \
LUMA_MANIFEST_REPORT="$report_sha256" \
LUMA_MANIFEST_CREATED="$created_utc" \
python3 - <<'PY'
import json
import os
from pathlib import Path

payload = {
    "schema": "org.projectluma.update-release/v1",
    "channel": os.environ["LUMA_MANIFEST_CHANNEL"],
    "ref": os.environ["LUMA_MANIFEST_REF"],
    "ostree_commit": os.environ["LUMA_MANIFEST_COMMIT"],
    "accepted_deployment_commit": os.environ["LUMA_MANIFEST_ACCEPTED"],
    "previous_commit": os.environ["LUMA_MANIFEST_PREVIOUS"] or None,
    "source_revision": os.environ["LUMA_MANIFEST_SOURCE"],
    "image_sha256": os.environ["LUMA_MANIFEST_IMAGE"],
    "build_report_sha256": os.environ["LUMA_MANIFEST_REPORT"],
    "created_utc": os.environ["LUMA_MANIFEST_CREATED"],
}
Path(os.environ["MANIFEST_PATH"]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
gpg --batch --yes --homedir "$gpg_homedir" --local-user "$gpg_fingerprint" \
  --armor --detach-sign --output "$manifest.asc" "$manifest"

# Advancing the channel ref is deliberately the final repository-content
# change. Publication to the served webroot is a separate generation switch,
# so clients never observe this repository while it is being mutated.
if [ -n "$previous" ]; then
  ostree reset --repo="$publish_repo" "$LUMA_UPDATE_REF" "$commit"
else
  ostree refs --repo="$publish_repo" --create="$LUMA_UPDATE_REF" "$commit"
fi
ostree summary --repo="$publish_repo" --update \
  --gpg-sign="$gpg_fingerprint" --gpg-homedir="$gpg_homedir" \
  --add-metadata="org.projectluma.channel='recent'"

# Prove the exact files a client will consume: exported public key, signed
# summary, signed commit, and advertised ref.
verify_root=$(mktemp -d)
trap 'rm -rf "$verify_root"' EXIT
public_key="$verify_root/luma-recent.gpg"
gpg --batch --homedir "$gpg_homedir" --export "$gpg_fingerprint" >"$public_key"
ostree init --repo="$verify_root/client" --mode=archive
ostree remote add --repo="$verify_root/client" \
  --gpg-import="$public_key" \
  --set=gpg-verify=true --set=gpg-verify-summary=true \
  --collection-id="$LUMA_UPDATE_COLLECTION_ID" \
  verify "file://$publish_repo" "$LUMA_UPDATE_REF"
ostree pull --repo="$verify_root/client" verify "$LUMA_UPDATE_REF"
verified=$(ostree rev-parse --repo="$verify_root/client" \
  "verify:$LUMA_UPDATE_REF")
[ "$verified" = "$commit" ] || fail 'client verification resolved an unexpected commit'
mkdir -m 0700 "$verify_root/verify-gpg"
gpg --batch --homedir "$verify_root/verify-gpg" --import "$public_key" >/dev/null 2>&1
gpg --batch --homedir "$verify_root/verify-gpg" \
  --verify "$manifest.asc" "$manifest" >/dev/null 2>&1

install -D -m 0644 "$public_key" "$publish_repo/luma/luma-recent.gpg"
printf 'channel=%s\nref=%s\ncommit=%s\naccepted_deployment=%s\nsource_revision=%s\nkey=%s\n' \
  "$LUMA_UPDATE_CHANNEL" "$LUMA_UPDATE_REF" "$commit" \
  "$accepted_commit" "$source_revision" "$gpg_fingerprint"
