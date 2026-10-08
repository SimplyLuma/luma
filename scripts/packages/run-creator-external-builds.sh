#!/usr/bin/env bash
# Dedicated external-app containers share only source and artifact mounts.
set -uo pipefail
work=${1:?writable host work directory}
platform=${2:?platform RPM}
devel=${3:?platform devel RPM}
cp "$platform" "$devel" "$work/deps/"
platform_in=/work/deps/$(basename "$platform")
devel_in=/work/deps/$(basename "$devel")
pids=()
for app in grid stage write session canvas; do
  container=creator-$app-20261004
  [[ $app == grid ]] && container=creator-office-20261004
  (
    podman exec -u0 "$container" bash /work/build-creator-external.sh "$app" /source "/work/$app" "$platform_in" "$devel_in" > "$work/$app/build.log" 2>&1
    code=$?
    printf '%s\n' "$code" > "$work/$app/exit-status"
    exit "$code"
  ) &
  pids+=("$!")
done
(
  podman exec -u0 creator-viola-20261004 bash -c '
    set -euo pipefail
    dnf -y install "$1" "$2"
    dnf -y builddep /work/viola/SPECS/viola-browser-stable.spec
    rpmbuild -ba --define "_topdir /work/viola" --define "viola_commit 61246420e8a0ca8c3b461cc8111893a76cdd4003" /work/viola/SPECS/viola-browser-stable.spec
    test "$(find /work/viola/RPMS -name "viola-browser-stable-*.rpm" | wc -l)" -eq 1
    find /work/viola/RPMS -name "*.rpm" -print0 | sort -z | xargs -0 sha256sum > /work/viola/SHA256SUMS
  ' bash "$platform_in" "$devel_in" > "$work/viola/build.log" 2>&1
  code=$?
  printf '%s\n' "$code" > "$work/viola/exit-status"
  exit "$code"
) &
pids+=("$!")
status=0
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done
exit "$status"
