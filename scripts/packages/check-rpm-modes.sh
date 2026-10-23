#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# check-rpm-modes.sh RPM...: fail when a package records a setgid/setuid
# directory or a directory others cannot enter. Build trees with setgid or
# default ACLs leak those modes into packages, and installed directories the
# session cannot read silently break things (UCM, session scripts).
set -euo pipefail
[ "$#" -gt 0 ] || { echo 'usage: check-rpm-modes.sh RPM...' >&2; exit 2; }
status=0
for rpm_file in "$@"; do
  while read -r path mode; do
    [ "${mode:0:2}" = 04 ] || continue                   # directories only
    perm=$((8#${mode:2}))
    if (( perm & 06000 )) || (( (perm & 05) != 05 )); then
      printf '%s: directory %s has mode %s\n' "$(basename "$rpm_file")" "$path" "${mode:2}" >&2
      status=1
    fi
  done < <(rpm -qp --dump "$rpm_file" | awk '{print $1, $5}')
done
exit "$status"
