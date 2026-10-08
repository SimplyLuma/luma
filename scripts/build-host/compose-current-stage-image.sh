#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
build_root=${LUMA_BUILD_ROOT:-/srv/luma-build}
artifact_root=${LUMA_ARTIFACT_ROOT:-"$build_root/artifacts/stage"}
current_link="$build_root/artifacts/current"
protected_vm=${LUMA_PROTECTED_VM:-viola-windows-builder}
base_image="$repo_root/build/track-a/luma-track-a-baseline.qcow2"

git_repo() {
  git -c "safe.directory=$repo_root" -C "$repo_root" "$@"
}

if [ "$(id -u)" -ne 0 ]; then
  printf 'error: Stage image composition requires root for its bounded libvirt worker\n' >&2
  exit 1
fi
for tool in git sha256sum virsh; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Stage composition tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done
if [ ! -f "$base_image" ]; then
  printf 'error: admitted Stage base is missing: %s\n' "$base_image" >&2
  exit 1
fi

protected_state=$(virsh domstate "$protected_vm" 2>/dev/null | tr -d '\r' || true)
if [ "$protected_state" != running ]; then
  printf 'error: protected VM %s is %s; refusing composition\n' \
    "$protected_vm" "${protected_state:-unavailable}" >&2
  exit 75
fi
available_mib=$(awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo)
if [ "${available_mib:-0}" -lt 10240 ]; then
  printf 'error: composition requires 10240 MiB available; host has %s MiB\n' \
    "${available_mib:-0}" >&2
  exit 75
fi

source_revision=$(git_repo rev-parse HEAD)
if [ -n "$(git_repo status --porcelain --untracked-files=no)" ]; then
  printf 'error: tracked source is dirty; refusing to label a Stage image\n' >&2
  exit 1
fi
build_origin=$(git_repo remote get-url build-origin 2>/dev/null || true)
if [ "$build_origin" != "$build_root/git/ProjectLuma.git" ]; then
  printf 'error: protected build-origin is not configured for this checkout\n' >&2
  exit 1
fi
mirror_revision=$(git_repo ls-remote "$build_origin" refs/heads/stage |
  awk 'NR == 1 { print $1 }')
if [ "$mirror_revision" != "$source_revision" ]; then
  printf 'error: Stage checkout %s is not admitted to build-origin (%s)\n' \
    "$source_revision" "${mirror_revision:-missing}" >&2
  exit 1
fi
output_dir="$artifact_root/$source_revision"
if [ -e "$output_dir" ]; then
  printf 'error: immutable Stage artifact directory already exists: %s\n' \
    "$output_dir" >&2
  exit 1
fi
if [ -e "$current_link" ] && [ ! -L "$current_link" ]; then
  printf 'error: current artifact pointer is not a symlink: %s\n' \
    "$current_link" >&2
  exit 1
fi
work_dir="$artifact_root/.compose-$source_revision-$$"
output_image="$work_dir/luma-desktop.qcow2"
report="$work_dir/build-report.txt"
passed=0
retain_failed_composition() {
  if [ "$passed" -eq 0 ] && [ -d "$work_dir" ]; then
    failed_dir="$artifact_root/.failed-$source_revision-$(date -u +%Y%m%dT%H%M%SZ)"
    mv "$work_dir" "$failed_dir"
    printf 'Stage composition diagnostics retained: %s\n' "$failed_dir" >&2
  fi
}
trap retain_failed_composition EXIT

install -d -o luma-build -g luma-build -m 0750 "$work_dir"
"$repo_root/scripts/vm/write-desktop-build-report.sh" "$base_image" "$report"
reported_revision=$(
  "$repo_root/scripts/update/read-build-report-value.sh" "$report" source_revision
)
if [ "$reported_revision" != "$source_revision" ]; then
  printf 'error: build report revision %s does not match admitted Stage %s\n' \
    "$reported_revision" "$source_revision" >&2
  exit 1
fi
LUMA_VM_DIR="$build_root/vm/compose" \
  "$repo_root/scripts/vm/compose-desktop-image.sh" "$base_image" "$output_image"
{
  printf 'output_sha256=%s\n' "$(sha256sum "$output_image" | awk '{print $1}')"
  printf 'completed_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >>"$report"
(
  cd "$work_dir"
  sha256sum luma-desktop.qcow2 luma-desktop.qcow2.deployment.json \
    build-report.txt >SHA256SUMS
)
chown -R luma-build:luma-build "$work_dir"
chmod 0640 "$output_image"
chmod 0644 "$output_image.sha256" "$output_image.deployment.json" \
  "$report" "$work_dir/SHA256SUMS"

mv "$work_dir" "$output_dir"
passed=1

next_link="$build_root/artifacts/.current-$source_revision-$$"
ln -s "stage/$source_revision" "$next_link"
mv -Tf "$next_link" "$current_link"

printf 'Current Stage image: %s/luma-desktop.qcow2\n' "$output_dir"
