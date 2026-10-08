# SPDX-License-Identifier: Apache-2.0
# Shared settings for the Depot distribution scripts. Sourced, never executed.
#
# Every path can be overridden from the environment so the same scripts run
# on the production build host, a developer machine, or in CI.

depot_repo_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)

# Work tree: package repository, runtime assembly, app builds, the remote.
: "${DEPOT_ROOT:=/srv/luma-build/depot}"
# Signing material. Private keys never leave this directory.
: "${DEPOT_KEYS:=/srv/luma-build/depot-keys}"
# Container that carries flatpak, flatpak-builder, flatpak-module-tools,
# ostree, minisign and appstream. Created by scripts/depot/tools-container.sh.
: "${DEPOT_TOOLS_CONTAINER:=luma-depot-tools}"
: "${DEPOT_ARCH:=$(uname -m)}"
: "${DEPOT_RUNTIME_BRANCH:=44}"
: "${DEPOT_FEDORA_RELEASE:=44}"
: "${DEPOT_REMOTE_NAME:=luma}"
: "${DEPOT_PUBLIC_URL:=https://dl.simplyluma.com}"
: "${DEPOT_COLLECTION_ID:=org.projectluma.Depot}"

# A checkout inside the work tree is addressed through DEPOT_ROOT, the path the
# tools container mounts it at, so paths are the same inside and outside.
if [ -e "$DEPOT_ROOT" ]; then
  depot_real_root=$(realpath "$DEPOT_ROOT")
  case "$depot_repo_root" in
    "$depot_real_root"/*) depot_repo_root="$DEPOT_ROOT/${depot_repo_root#"$depot_real_root"/}" ;;
  esac
fi

depot_repo="$DEPOT_ROOT/repo"
depot_rpms="$DEPOT_ROOT/rpms/$DEPOT_ARCH"
depot_out="${DEPOT_SIGNING_INPUTS:-$DEPOT_ROOT/out}"
depot_logs="$DEPOT_ROOT/logs"
depot_flatpak_user_dir="$DEPOT_ROOT/flatpak-user"

depot_log() {
  printf '%s depot: %s\n' "$(date -u +%FT%TZ)" "$*" >&2
}

depot_die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

depot_gpg_fingerprint() {
  local file="$DEPOT_KEYS/gpg-fingerprint.txt"
  [ -s "$file" ] || depot_die "no Depot signing key; run scripts/depot/generate-keys.sh"
  tr -d ' \n' <"$file"
}

# Run a command inside the tools container when not already inside it.
depot_in_tools() {
  if [ -f /run/.containerenv ] || [ "${DEPOT_INSIDE_TOOLS:-0}" = 1 ]; then
    "$@"
  else
    local workdir=$depot_repo_root
    podman exec --interactive --env DEPOT_INSIDE_TOOLS=1 --env "DEPOT_ROOT=$DEPOT_ROOT" \
      --env "DEPOT_KEYS=$DEPOT_KEYS" --env "FLATPAK_USER_DIR=$depot_flatpak_user_dir" \
      --workdir "$workdir" "$DEPOT_TOOLS_CONTAINER" "$@"
  fi
}

# Publishers must be invoked from the separately sealed administrator control.
# Builders do not mount this tree or the staged signing root.
depot_require_signing_control() {
  [ -n "${DEPOT_SIGNING_CONTROL:-}" ] && [ -n "${DEPOT_SIGNING_CONTROL_SHA256:-}" ] ||
    depot_die "a separately sealed publisher control and manifest digest are required"
  [ "$(realpath "$depot_repo_root")" = "$(realpath "$DEPOT_SIGNING_CONTROL")" ] ||
    depot_die "invoke the publisher from its sealed control snapshot"
  env -u PYTHONPATH -u PYTHONHOME -u BASH_ENV -u ENV python3 -B -I "$DEPOT_SIGNING_CONTROL/scripts/depot/seal-signing-control.py" verify \
    "$DEPOT_SIGNING_CONTROL" "$DEPOT_SIGNING_CONTROL_SHA256" >/dev/null || depot_die "publisher control verification failed"
}

depot_in_signer() {
  depot_require_signing_control
  [ -n "${DEPOT_SIGNING_CONTAINER:-}" ] || depot_die "an explicit DEPOT_SIGNING_CONTAINER is required"
  [ -n "${DEPOT_SIGNING_INPUTS:-}" ] && [ -n "${DEPOT_SIGNING_INPUTS_SHA256:-}" ] ||
    depot_die "sealed signing artifact identity is required"
  podman inspect "$DEPOT_SIGNING_CONTAINER" | env -u PYTHONPATH -u PYTHONHOME -u BASH_ENV -u ENV python3 -B -I \
    "$DEPOT_SIGNING_CONTROL/scripts/depot/verify-signing-container.py" \
    --control "$DEPOT_SIGNING_CONTROL" --control-sha "$DEPOT_SIGNING_CONTROL_SHA256" \
    --inputs "$DEPOT_SIGNING_INPUTS" --inputs-sha "$DEPOT_SIGNING_INPUTS_SHA256" \
    --output "$DEPOT_ROOT" --keys "$DEPOT_KEYS" || depot_die "signer custody verification failed"
  local role network
  role=$(podman inspect "$DEPOT_SIGNING_CONTAINER" --format '{{index .Config.Labels "org.projectluma.depot.role"}}') || depot_die "cannot inspect signer"
  [ "$role" = signer ] || depot_die "refusing signing in an application build container"
  network=$(podman inspect "$DEPOT_SIGNING_CONTAINER" --format '{{.HostConfig.NetworkMode}}') || depot_die "cannot inspect signer network"
  [ "$network" = none ] || depot_die "the signing container must have no network"
  podman exec --interactive --workdir "$DEPOT_SIGNING_CONTROL" "$DEPOT_SIGNING_CONTAINER" \
    /usr/bin/env -i PATH=/usr/bin:/bin HOME=/root "TMPDIR=$DEPOT_ROOT/tmp" \
    DEPOT_INSIDE_TOOLS=1 "DEPOT_ROOT=$DEPOT_ROOT" "DEPOT_KEYS=$DEPOT_KEYS" \
    "DEPOT_SIGNING_INPUTS=$DEPOT_SIGNING_INPUTS" \
    "FLATPAK_USER_DIR=$depot_flatpak_user_dir" "$@"
}
