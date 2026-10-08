#!/usr/bin/bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
work_root=${CHARLIE_RPM_WORK_ROOT:-"$project_root/.build/rpmbuild"}

mkdir -p "$work_root/BUILD" "$work_root/BUILDROOT" "$work_root/RPMS" \
  "$work_root/SOURCES" "$work_root/SPECS" "$work_root/SRPMS"

tar --sort=name --mtime='UTC 2026-09-13' --owner=0 --group=0 --numeric-owner \
  --exclude=.git --exclude=.build --exclude='__pycache__' --exclude='*.pyc' --transform='s,^,charlie-luma/,' \
  -czf "$work_root/SOURCES/charlie-luma.tar.gz" -C "$project_root" .
cp "$project_root/LICENSE.md" "$work_root/SOURCES/LICENSE.md"
# Test with the authoritative shell schema on a private backend. Standalone
# producer checkouts can supply the same declared input explicitly.
schema=${LUMA_CREATOR_SCHEMA_FILE:-"$project_root/../../luma-shell-state/org.project_luma.shell-state.gschema.xml"}
[ -f "$schema" ] || { printf 'missing declared test source: %s\n' "$schema" >&2; exit 1; }
cp "$schema" "$work_root/SOURCES/org.project_luma.shell-state.gschema.xml"
cp "$project_root/packaging/luma-charlie.spec" "$work_root/SPECS/luma-charlie.spec"

rpmbuild --define "_topdir $work_root" -ba "$work_root/SPECS/luma-charlie.spec"
