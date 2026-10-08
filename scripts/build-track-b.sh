#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/image/inputs.env"

for tool in git image-builder podman sha256sum skopeo sudo; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != "$TARGET_ARCH" ]; then
  printf 'error: Track B requires the canonical Linux/%s builder\n' "$TARGET_ARCH" >&2
  exit 1
fi

"$repo_root/scripts/prepare-test-provisioning.sh"

output_dir="$repo_root/build/track-b"
cache_dir="$repo_root/build/cache"
blueprint="$repo_root/build/provisioning/track-b-blueprint.toml"
report="$output_dir/build-report.txt"
provisioned_tag=localhost/project-luma-track-b-provisioned:baseline
mkdir -p "$output_dir" "$cache_dir/image-builder" "$cache_dir/rpmmd"

declared_digest=${TRACK_B_CONTAINER##*@}
registry_digest=$(skopeo inspect --format '{{.Digest}}' "docker://$TRACK_B_CONTAINER")
if [ "$registry_digest" != "$declared_digest" ]; then
  printf 'error: Track B registry digest does not match the lock file\n' >&2
  exit 1
fi

sudo podman pull "$TRACK_B_CONTAINER"
root_store_digest=$(sudo podman image inspect --format '{{.Digest}}' "$TRACK_B_CONTAINER")
if [ "$root_store_digest" != "$declared_digest" ]; then
  printf 'error: pulled Track B image does not match the lock file\n' >&2
  exit 1
fi

sudo podman build \
  --pull=never \
  --build-arg "BASE_IMAGE=$TRACK_B_CONTAINER" \
  --tag "$provisioned_tag" \
  --file "$repo_root/image/track-b/Containerfile" \
  "$repo_root"
provisioned_image_id=$(sudo podman image inspect --format '{{.Id}}' "$provisioned_tag")

source_revision=$(git -C "$repo_root" rev-parse --verify HEAD 2>/dev/null || printf 'unborn')
if [ -n "$(git -C "$repo_root" status --porcelain)" ]; then
  source_state=dirty
else
  source_state=clean
fi

{
  printf 'track=B\n'
  printf 'started_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'source_revision=%s\n' "$source_revision"
  printf 'source_state=%s\n' "$source_state"
  printf 'target_arch=%s\n' "$TARGET_ARCH"
  printf 'base_container=%s\n' "$TRACK_B_CONTAINER"
  printf 'provisioned_container=%s\n' "$provisioned_tag"
  printf 'provisioned_image_id=%s\n' "$provisioned_image_id"
  image-builder version
} >"$report"

sudo image-builder build \
  --bootc-ref "$provisioned_tag" \
  --bootc-default-fs ext4 \
  --blueprint "$blueprint" \
  --image-size "$IMAGE_SIZE_BYTES" \
  --seed "$IMAGE_BUILDER_SEED" \
  --cache "$cache_dir/image-builder" \
  --rpmmd-cache "$cache_dir/rpmmd" \
  --output-dir "$output_dir" \
  --output-name luma-track-b-baseline \
  --progress verbose \
  --with-buildlog \
  --with-manifest \
  --with-sbom \
  --with-metrics \
  qcow2 2>&1 | tee "$output_dir/image-builder.log"

sudo chown -R "$(id -u):$(id -g)" "$output_dir" "$cache_dir"
find "$output_dir" -maxdepth 2 -type f -name '*.qcow2' -exec sha256sum {} \; >"$output_dir/SHA256SUMS"
printf 'completed_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$report"
printf 'Track B build complete: %s\n' "$output_dir"
