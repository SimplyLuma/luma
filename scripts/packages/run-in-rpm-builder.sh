#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [ "$#" -ne 3 ]; then
  printf 'usage: %s WORKDIR IMAGE COMMAND\n' "$0" >&2
  exit 2
fi

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
build_root=$(realpath -m "$repo_root/build")
# Build trees hold hundreds of thousands of files. Desktop file indexers
# (localsearch indexes the whole home folder) otherwise crawl every build and
# keep a laptop's fans running long after it finishes.
mkdir -p "$build_root" && : >"$build_root/.trackerignore"
host_workdir=$(realpath "$1")
builder_image=$2
builder_command=$3
builder_name=${LUMA_RPM_BUILDER_NAME:-luma-rpm-builder-f44}
builder_architecture=${LUMA_RPM_BUILDER_ARCHITECTURE:-}
builder_mode=${LUMA_RPM_BUILDER_MODE:-cached}
disable_selinux_label=${LUMA_RPM_BUILDER_DISABLE_SELINUX_LABEL:-0}

case "$builder_architecture" in
  ''|x86_64|aarch64) ;;
  *)
    printf 'error: unsupported RPM builder architecture: %s\n' \
      "$builder_architecture" >&2
    exit 1
    ;;
esac
case "$builder_mode" in
  cached|build) ;;
  *) printf 'error: RPM builder mode must be cached or build\n' >&2; exit 1 ;;
esac
case "$disable_selinux_label" in
  0|1) ;;
  *)
    printf 'error: LUMA_RPM_BUILDER_DISABLE_SELINUX_LABEL must be 0 or 1\n' >&2
    exit 1
    ;;
esac

case "$host_workdir" in
  "$build_root"/*) ;;
  *)
    printf 'error: RPM builder workdir must be beneath %s\n' "$build_root" >&2
    exit 1
    ;;
esac

container_workdir="/build/${host_workdir#"$build_root"/}"

# Local package validation can use an existing Fedora development container
# when nested image-loader sandboxes are unavailable in the pinned builder.
if [ -n "${LUMA_RPM_BUILDER_EXISTING:-}" ]; then
  podman exec --user root --workdir "$host_workdir" \
    "$LUMA_RPM_BUILDER_EXISTING" /bin/bash -lc "$builder_command"
  exit
fi

# The build output is intentionally shared by several cached package builders.
# Use Fedora's shared-container label, not a private MCS category: a private
# `:Z` relabel by one builder otherwise revokes another builder's access.
if [ "$disable_selinux_label" = 0 ] && \
   command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = Enforcing ]; then
  # A diagnostic container may have applied a private MCS category to a
  # parent directory before this build begins. Relabel each mount ancestor as
  # shared as well as the work payload; otherwise the cached builder cannot
  # traverse to correctly labelled children and reports a misleading EACCES.
  label_path=$host_workdir
  while :; do
    chcon -t container_file_t -l s0 "$label_path"
    [ "$label_path" = "$build_root" ] && break
    label_path=$(dirname -- "$label_path")
  done
  chcon -R -t container_file_t -l s0 "$host_workdir"
fi

# A normal image-build transaction is useful on constrained shared hosts: no
# persistent container or keepalive process is needed. The same command,
# base image, labelled output mount and outer guarded resource limits apply.
if [ "$builder_mode" = build ]; then
  [ "$disable_selinux_label" = 0 ] || {
    printf 'error: one-shot RPM builds require normal SELinux labelling\n' >&2
    exit 1
  }
  control_dir=$(mktemp -d "$host_workdir/.luma-builder.XXXXXX")
  trap 'rm -rf "$control_dir"' EXIT
  printf '%s\n' "$builder_command" > "$control_dir/command.sh"
  python3 - "$control_dir" "$build_root" "$container_workdir" <<'PY'
import json, pathlib, sys
control, build_root, working = sys.argv[1:]
script = '/build/' + str(pathlib.Path(control).relative_to(build_root) / 'command.sh')
(pathlib.Path(control) / 'Containerfile').write_text(
    'ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nWORKDIR ' + json.dumps(working) +
    '\nRUN ' + json.dumps(['/bin/bash', script]) + '\n')
PY
  architecture_options=()
  [ -z "$builder_architecture" ] || architecture_options=(--arch "$builder_architecture")
  podman build --no-cache "${architecture_options[@]}" \
    --build-arg "BASE_IMAGE=$builder_image" \
    --volume "$build_root:/build:z" \
    --file "$control_dir/Containerfile" "$control_dir"
  exit
fi

if podman container exists "$builder_name"; then
  actual_image=$(podman inspect --format '{{.ImageName}}' "$builder_name")
  if [ "$actual_image" != "$builder_image" ]; then
    printf 'error: cached RPM builder uses %s, expected %s\n' \
      "$actual_image" "$builder_image" >&2
    printf 'remove %s explicitly after reviewing the base-image change\n' \
      "$builder_name" >&2
    exit 1
  fi

  if [ -n "$builder_architecture" ]; then
    builder_image_id=$(podman inspect --format '{{.Image}}' "$builder_name")
    actual_architecture=$(podman image inspect --format '{{.Architecture}}' \
      "$builder_image_id")
    case "$actual_architecture" in
      amd64) actual_architecture=x86_64 ;;
      arm64) actual_architecture=aarch64 ;;
    esac
    if [ "$actual_architecture" != "$builder_architecture" ]; then
      printf 'error: cached RPM builder architecture is %s, expected %s\n' \
        "$actual_architecture" "$builder_architecture" >&2
      printf 'choose a new builder name or remove %s explicitly after review\n' \
        "$builder_name" >&2
      exit 1
    fi
  fi

  actual_build_root=$(podman inspect --format \
    '{{range .Mounts}}{{if eq .Destination "/build"}}{{.Source}}{{end}}{{end}}' \
    "$builder_name")
  # An isolated candidate may live beneath the build tree already mounted by
  # a long-lived builder. Reuse that exact read/write mount instead of creating
  # a second dependency-heavy container, while still refusing unrelated roots.
  if [ -n "$actual_build_root" ]; then
    case "$build_root/" in
      "$actual_build_root"/*)
        container_workdir="/build/${host_workdir#"$actual_build_root"/}"
        ;;
    esac
  fi
  if [ "$actual_build_root" != "$build_root" ]; then
    case "$build_root/" in
      "$actual_build_root"/*) [ -n "$actual_build_root" ] ;;
      *)
        if [ "$(podman inspect --format '{{.State.Running}}' "$builder_name")" = true ]; then
          printf 'error: cached RPM builder is active with a different source tree: %s\n' \
            "$actual_build_root" >&2
          exit 1
        fi
        podman rm "$builder_name" >/dev/null
        ;;
    esac
  fi
fi

if ! podman container exists "$builder_name"; then
  install -d -m 0755 "$build_root"
  architecture_options=()
  security_options=()
  volume_spec="$build_root:/build:z"
  if [ -n "$builder_architecture" ]; then
    architecture_options=(--arch "$builder_architecture")
  fi
  # Some dedicated build volumes cannot carry Podman's MCS relabel even
  # though the isolated source tree itself is correctly confined by Unix
  # ownership. This explicit, opt-in mode disables labels only for the
  # disposable rootless RPM builder; it never changes host enforcement or a
  # produced package. Callers must choose a unique builder name so a labeled
  # and unlabeled container can never be confused.
  if [ "$disable_selinux_label" = 1 ]; then
    security_options=(--security-opt label=disable)
    volume_spec="$build_root:/build"
  fi
  # --init: every build is a podman exec whose children outlive it, and
  # sleep cannot reap them, so a long-lived builder collected zombies.
  podman run --detach --init \
    "${architecture_options[@]}" \
    "${security_options[@]}" \
    --name "$builder_name" \
    --hostname luma-rpm-builder \
    --volume "$volume_spec" \
    --env HOME=/tmp/luma-rpm-home \
    "$builder_image" \
    sleep infinity >/dev/null
fi

if [ "$(podman inspect --format '{{.State.Running}}' "$builder_name")" != true ]; then
  podman start "$builder_name" >/dev/null
fi

podman exec \
  --env "LUMA_RPM_BUILDER_ARCHITECTURE=$builder_architecture" \
  --workdir "$container_workdir" \
  "$builder_name" \
  /bin/bash -lc "$builder_command"
