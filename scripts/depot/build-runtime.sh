#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Assemble the Luma Platform runtime, SDK and platform extensions from RPMs.
#
#   build-runtime.sh [COMPONENT...]
#
# COMPONENT is Platform, Sdk, Platform.GL.default or Platform.Office (default:
# Platform, Sdk and Platform.GL.default). Each component's overlay under
# packaging/flatpak/runtime/ is rendered to a container.yaml and assembled by
# scripts/depot/runtime/assemble-from-rpms.py inside the tools container, from
# Fedora 44 and the Luma repository made by collect-luma-rpms.sh. Results land
# in $DEPOT_ROOT/out/runtime/<COMPONENT>/ and are published by publish-runtime.sh.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"

install -d -m 0755 "$DEPOT_ROOT/locks"
exec 9>"$DEPOT_ROOT/locks/runtime-rpms-$DEPOT_ARCH.lock"
flock -s -w 120 9 || depot_die "runtime repository collection is active"

components=("$@")
[ "${#components[@]}" -gt 0 ] || components=(Platform Sdk Platform.GL.default)
build_id=${DEPOT_BUILD_ID:-$(date -u +%Y%m%d.%H%M)}

[ -f "$depot_rpms/repodata/repomd.xml" ] ||
  depot_die "no Luma package repository at $depot_rpms; run collect-luma-rpms.sh"
install -d -m 0755 "$DEPOT_ROOT/work/runtime" "$depot_out/runtime" "$depot_logs"

for component in "${components[@]}"; do
  overlay="packaging/flatpak/runtime/org.projectluma.$component.yaml"
  [ -f "$depot_repo_root/$overlay" ] || depot_die "unknown component $component"
  spec="$DEPOT_ROOT/work/runtime/$component.container.yaml"
  result="$depot_out/runtime/$component"
  log="$depot_logs/runtime-$component-$build_id.log"
  name=$(printf 'luma-%s' "$component" | tr 'A-Z.' 'a-z-')
  extra=()
  if [ -n "${DEPOT_BUILD_MIN_FREE_BYTES:-}" ]; then
    [ "$DEPOT_BUILD_MIN_FREE_BYTES" = 10737418240 ] || depot_die "private builder reserve must be exactly 10 GiB"
    extra+=(--private-min-free-bytes "$DEPOT_BUILD_MIN_FREE_BYTES")
  fi
  if [ "$component" = Platform.Office ]; then
    rpmlist=$(ls "$depot_out/runtime/Platform/"*.oci.rpmlist.json 2>/dev/null | head -n 1)
    [ -n "$rpmlist" ] || depot_die "Platform.Office needs the Platform build's rpmlist first"
    extra+=(--runtime-rpmlist "$rpmlist")
  fi

  depot_log "rendering $overlay"
  depot_in_tools python3 -B scripts/depot/runtime/generate-container-yaml.py "$overlay" -o "$spec"
  depot_log "assembling $component (log: $log)"
  if ! depot_in_tools python3 -B scripts/depot/runtime/assemble-from-rpms.py \
      --containerspec "$spec" \
      --nvr "$name-$DEPOT_RUNTIME_BRANCH-$build_id" \
      --fedora-release "$DEPOT_FEDORA_RELEASE" \
      --local-repo "$depot_rpms" \
      --workdir "$DEPOT_ROOT/work/runtime/$component.work" \
      --resultdir "$result" \
      "${extra[@]}" >"$log" 2>&1; then
    tail -n 40 "$log" >&2
    depot_die "assembly of $component failed; see $log"
  fi
  # The install root is only needed while assembling; the result holds the
  # commit, the OCI image and the RPM manifest.
  depot_in_tools rm -rf "$DEPOT_ROOT/work/runtime/$component.work"
  printf '%s\n' "$build_id" >"$result/BUILD_ID"
  depot_log "$component assembled: $(du -sh "$result" | cut -f1)"
done
