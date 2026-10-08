#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/image/inputs.env"

for tool in git image-builder ostree python3 sha256sum sudo; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != "$TARGET_ARCH" ]; then
  printf 'error: Track A requires the canonical Linux/%s builder\n' "$TARGET_ARCH" >&2
  exit 1
fi

"$repo_root/scripts/prepare-test-provisioning.sh"

output_dir="$repo_root/build/track-a"
cache_dir="$repo_root/build/cache"
ostree_repo="$cache_dir/track-a-ostree/repo-archive"
blueprint="$repo_root/build/provisioning/track-a-blueprint.toml"
report="$output_dir/build-report.txt"
http_port=${LUMA_OSTREE_HTTP_PORT:-18080}
mkdir -p "$output_dir" "$cache_dir/track-a-ostree" "$cache_dir/image-builder" "$cache_dir/rpmmd"

if [ ! -f "$ostree_repo/config" ]; then
  # Archive mode preserves ownership and setuid metadata while remaining
  # writable by the unprivileged build user.
  ostree init --repo="$ostree_repo" --mode=archive
fi

ostree remote add \
  --repo="$ostree_repo" \
  --force \
  --gpg-import="/etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-${FEDORA_RELEASE}-primary" \
  --contenturl="$TRACK_A_OSTREE_CONTENT_URL" \
  fedora "$TRACK_A_OSTREE_METADATA_URL"

if ! ostree show --repo="$ostree_repo" "$TRACK_A_OSTREE_COMMIT" >/dev/null 2>&1; then
  ostree pull --repo="$ostree_repo" fedora "$TRACK_A_OSTREE_COMMIT"
fi

resolved_commit=$(ostree rev-parse --repo="$ostree_repo" "$TRACK_A_OSTREE_COMMIT")
if [ "$resolved_commit" != "$TRACK_A_OSTREE_COMMIT" ]; then
  printf 'error: Track A resolved to an unexpected OSTree commit\n' >&2
  exit 1
fi
current_local_commit=$(ostree rev-parse --repo="$ostree_repo" "$TRACK_A_LOCAL_REF" 2>/dev/null || true)
if [ "$current_local_commit" != "$TRACK_A_OSTREE_COMMIT" ]; then
  if [ -n "$current_local_commit" ]; then
    ostree refs --repo="$ostree_repo" --delete="$TRACK_A_LOCAL_REF"
  fi
  ostree refs --repo="$ostree_repo" --create="$TRACK_A_LOCAL_REF" "$TRACK_A_OSTREE_COMMIT"
fi

python3 -m http.server "$http_port" --bind 127.0.0.1 --directory "$ostree_repo" \
  >"$output_dir/ostree-http.log" 2>&1 &
http_pid=$!
cleanup() {
  kill "$http_pid" >/dev/null 2>&1 || true
  wait "$http_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
sleep 1
kill -0 "$http_pid" 2>/dev/null || {
  printf 'error: failed to start the loopback-only OSTree server\n' >&2
  exit 1
}

source_revision=$(git -C "$repo_root" rev-parse --verify HEAD 2>/dev/null || printf 'unborn')
if [ -n "$(git -C "$repo_root" status --porcelain)" ]; then
  source_state=dirty
else
  source_state=clean
fi

{
  printf 'track=A\n'
  printf 'started_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'source_revision=%s\n' "$source_revision"
  printf 'source_state=%s\n' "$source_state"
  printf 'target_arch=%s\n' "$TARGET_ARCH"
  printf 'fedora_release=%s\n' "$FEDORA_RELEASE"
  printf 'ostree_ref=%s\n' "$TRACK_A_OSTREE_REF"
  printf 'ostree_commit=%s\n' "$TRACK_A_OSTREE_COMMIT"
  image-builder version
} >"$report"

sudo image-builder build \
  --distro "fedora-${FEDORA_RELEASE}" \
  --arch "$TARGET_ARCH" \
  --blueprint "$blueprint" \
  --ostree-url "http://127.0.0.1:${http_port}" \
  --ostree-ref "$TRACK_A_LOCAL_REF" \
  --image-size "$IMAGE_SIZE_BYTES" \
  --seed "$IMAGE_BUILDER_SEED" \
  --cache "$cache_dir/image-builder" \
  --rpmmd-cache "$cache_dir/rpmmd" \
  --output-dir "$output_dir" \
  --output-name luma-track-a-baseline \
  --progress verbose \
  --with-buildlog \
  --with-manifest \
  --with-sbom \
  --with-metrics \
  silverblue-qcow2 2>&1 | tee "$output_dir/image-builder.log"

sudo chown -R "$(id -u):$(id -g)" "$output_dir" "$cache_dir"
find "$output_dir" -maxdepth 2 -type f -name '*.qcow2' -exec sha256sum {} \; >"$output_dir/SHA256SUMS"
printf 'completed_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$report"
printf 'Track A build complete: %s\n' "$output_dir"
