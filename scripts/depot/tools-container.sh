#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Create (or recreate) the Depot tools container on the build host.
#
# Everything the distribution pipeline runs -- dnf install roots, flatpak,
# flatpak-builder, ostree, minisign, appstream -- runs here, so the shared host
# needs no packages installed. Build containers receive only the namespaces
# required by flatpak-builder and never receive publisher control or keys. Signing keys are mounted solely
# with an explicit --signing invocation in a distinct signing container.
#
#   tools-container.sh [--recreate]
#
# Storage: set CONTAINERS_STORAGE_CONF to keep the image and container layers
# off a full root filesystem (the production host uses the secondary disk).
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"
. "$depot_repo_root/config/desktop/inputs.env"

signing=0
recreate=0
for argument in "$@"; do
  case "$argument" in
    --signing) signing=1 ;;
    --recreate) recreate=1 ;;
    *) depot_die "usage: tools-container.sh [--recreate] [--signing]" ;;
  esac
done
if [ "$signing" = 1 ]; then
  [ "$DEPOT_TOOLS_CONTAINER" != luma-depot-tools ] || depot_die "signing requires a distinct DEPOT_TOOLS_CONTAINER"
fi
image=${DEPOT_TOOLS_IMAGE:-$FEDORA_RPM_BUILD_CONTAINER}
if [ "$(uname -m)" = aarch64 ]; then image=${DEPOT_TOOLS_IMAGE:-$FEDORA_RPM_BUILD_CONTAINER_AARCH64}; fi

if podman container exists "$DEPOT_TOOLS_CONTAINER"; then
  if [ "$recreate" != 1 ]; then
    depot_log "$DEPOT_TOOLS_CONTAINER exists (pass --recreate to rebuild it)"
    exit 0
  fi
  podman rm -f "$DEPOT_TOOLS_CONTAINER" >/dev/null
fi

real_root=$(realpath "$DEPOT_ROOT")
install -d -m 0755 "$real_root/cache/dnf" "$real_root/tmp"
key_mount=()
work_mount=(--volume "$real_root:$DEPOT_ROOT:exec" --volume "$real_root/cache/dnf:/var/cache/dnf")
# Nested bubblewrap must mount its own procfs. Podman's locked /proc masks
# otherwise deny that namespace operation before a source build can run.
# This is confined to the keyless builder's private PID namespace; signers
# below replace this list with zero capabilities and no unmask option.
isolation=(--cap-add SYS_ADMIN --cap-add NET_ADMIN --device /dev/fuse --security-opt 'unmask=/proc/*')
# Flatpak's root icon validator creates a private network namespace without
# a second user namespace and brings its loopback up. NET_ADMIN is needed
# there; the builder has no host-network namespace or signing material.
role=builder
custody_labels=()
if [ "$signing" = 1 ]; then
  depot_require_signing_control
  [ -n "${DEPOT_BUILD_ROOT:-}" ] && [ -n "${DEPOT_SIGNING_INPUTS:-}" ] ||
    depot_die "signing requires explicit builder root and sealed artifact inputs"
  # No root controlling a builder may contain or mount publisher code/output.
  env -u PYTHONPATH -u PYTHONHOME -u BASH_ENV -u ENV python3 -B -I - "$DEPOT_ROOT" "$DEPOT_SIGNING_CONTROL" "$DEPOT_SIGNING_INPUTS" "$DEPOT_KEYS" "$DEPOT_BUILD_ROOT" <<'PYGUARD'
import os, pathlib, stat, sys
protected = [pathlib.Path(os.path.abspath(p)) for p in sys.argv[1:5]]
build = pathlib.Path(sys.argv[5]).resolve()
for path in protected:
    resolved = path.resolve(strict=True)
    if resolved == build or build in resolved.parents or resolved in build.parents:
        raise SystemExit('publisher custody paths must be separate from builder root')
    for parent in (path, *path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise SystemExit(f'publisher custody directory is unsafe: {parent}')
PYGUARD
  [ -n "${DEPOT_SIGNING_INPUTS_SHA256:-}" ] || depot_die "a pinned artifact snapshot manifest is required"
  env -u PYTHONPATH -u PYTHONHOME -u BASH_ENV -u ENV python3 -B -I "$DEPOT_SIGNING_CONTROL/scripts/depot/seal-signing-control.py" verify-inputs \
    "$DEPOT_SIGNING_INPUTS" "$DEPOT_SIGNING_INPUTS_SHA256" >/dev/null
  # Root of a signer contains no source checkout. Only designated staged outputs
  # are writable; the exact artifact snapshot and control are read-only.
  work_mount=(--volume "$DEPOT_SIGNING_CONTROL:$DEPOT_SIGNING_CONTROL:ro"
              --volume "$DEPOT_SIGNING_INPUTS:$DEPOT_SIGNING_INPUTS:ro")
  for directory in repo site work tmp releases; do
    install -d -m 0700 "$DEPOT_ROOT/$directory"
    work_mount+=(--volume "$DEPOT_ROOT/$directory:$DEPOT_ROOT/$directory")
  done
  [ -d "$DEPOT_KEYS" ] || depot_die "existing protected signing keys are required"
  [ -n "${DEPOT_TOOLS_IMAGE:-}" ] || depot_die "signing requires an explicitly prepared immutable tools image"
  [[ "$image" =~ (^sha256:[0-9a-f]{64}$|^[0-9a-f]{64}$|@sha256:[0-9a-f]{64}$) ]] || depot_die "signing tools image must be immutable"
  key_mount=(--volume "$DEPOT_KEYS:$DEPOT_KEYS")
  isolation=(--cap-drop ALL --network none)
  role=signer
  custody_labels=(--label "org.projectluma.depot.control=$DEPOT_SIGNING_CONTROL_SHA256"
                  --label "org.projectluma.depot.inputs=$DEPOT_SIGNING_INPUTS_SHA256")
fi
case "$(realpath -m "$DEPOT_KEYS")" in
  "$real_root"|"$real_root"/*) depot_die "signing keys cannot be inside the mounted build tree" ;;
esac

podman run --detach --init "${isolation[@]}" --security-opt label=disable \
  --cpus "${DEPOT_TOOLS_CPUS:-2}" --memory "${DEPOT_TOOLS_MEMORY:-4g}" \
  --memory-swap "${DEPOT_TOOLS_MEMORY_SWAP:-5g}" --pids-limit 2048 \
  --cgroup-parent luma-os.slice --label "org.projectluma.depot.role=$role" "${custody_labels[@]}" \
  --name "$DEPOT_TOOLS_CONTAINER" --hostname luma-depot-tools \
  "${work_mount[@]}" \
  "${key_mount[@]}" \
  --env HOME=/root --env "TMPDIR=$DEPOT_ROOT/tmp" \
  --env PYTHONPATH= --env PYTHONHOME= --env BASH_ENV= --env ENV= --env LD_PRELOAD= --env LD_LIBRARY_PATH= \
  "$image" sleep infinity >/dev/null

if [ "$signing" = 0 ]; then
podman exec "$DEPOT_TOOLS_CONTAINER" dnf5 -q -y install \
  flatpak flatpak-builder flatpak-module-tools ostree minisign \
  appstream appstream-compose createrepo_c gnupg2 pinentry \
  python3-pyyaml python3-requests python3-pytest git-core rsync jq \
  desktop-file-utils librsvg2-tools util-linux zstd tar findutils which python3-pillow cpio \
  dnf5-plugins rclone s3cmd
fi
depot_log "$DEPOT_TOOLS_CONTAINER ready ($image)"
