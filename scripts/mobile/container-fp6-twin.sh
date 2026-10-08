#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
runtime_image=localhost/luma-fp6-twin-runtime:fedora44
runtime_name=luma-fp6-twin-runtime
qmp_socket="$repo_root/build/mobile/fp6-twin/qmp.sock"

command -v podman >/dev/null 2>&1 || {
  printf 'error: podman is required for the containerized twin runtime\n' >&2
  exit 1
}

usage() {
  printf 'usage: %s build | prepare | start | smoke | logs | stop\n' "$0" >&2
  exit 2
}

container_args=(
  --security-opt label=disable
  --userns=keep-id
  --volume "$repo_root:/workspace"
  --workdir /workspace
)

[ "$#" -eq 1 ] || usage
case "$1" in
  build)
    podman build \
      --tag "$runtime_image" \
      --file "$repo_root/config/mobile/fp6-twin/Containerfile" \
      "$repo_root/config/mobile/fp6-twin"
    ;;
  prepare)
    podman image exists "$runtime_image" || {
      printf 'error: build the twin runtime first\n' >&2
      exit 1
    }
    podman run --rm "${container_args[@]}" "$runtime_image" \
      ./scripts/mobile/prepare-fp6-twin.sh
    ;;
  start)
    podman image exists "$runtime_image" || {
      printf 'error: build the twin runtime first\n' >&2
      exit 1
    }
    if podman container exists "$runtime_name"; then
      printf 'error: twin runtime container already exists: %s\n' "$runtime_name" >&2
      printf 'Inspect it with the logs command; stop it explicitly when finished.\n' >&2
      exit 1
    fi
    podman run --detach \
      --name "$runtime_name" \
      --network host \
      "${container_args[@]}" \
      "$runtime_image" ./scripts/mobile/run-fp6-twin.sh --headless
    ;;
  smoke)
    "$repo_root/scripts/mobile/smoke-fp6-twin.sh"
    ;;
  logs)
    podman logs "$runtime_name"
    ;;
  stop)
    if ! podman container exists "$runtime_name"; then
      printf 'Twin runtime container is not present.\n'
      exit 0
    fi
    podman stop --time 20 "$runtime_name"
    podman rm "$runtime_name"
    if [ -S "$qmp_socket" ]; then
      rm -f -- "$qmp_socket"
      printf 'Retired stopped twin QMP socket: %s\n' "$qmp_socket"
    fi
    ;;
  *) usage ;;
esac
