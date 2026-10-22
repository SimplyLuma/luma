#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Public source checks shared by contributor checkouts and source-drop admission.
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"
git rev-parse --is-inside-work-tree >/dev/null
export PYTHONDONTWRITEBYTECODE=1
# Keep this contract about the converged public product. Private lab handoffs
# and separate Creative/Office sources are not inputs to a public build.
for path in README.md BUILDING.md CONTRIBUTING.md SECURITY.md LICENSE.md \
  config/desktop/inputs.env config/mobile/phosh-source.env \
  src/luma-search/luma-search-service \
  docs/developer/kit/lumaui-deviations.md; do
  test -s "$path" || { printf 'Required public source file missing: %s\n' "$path" >&2; exit 1; }
done
git diff --check
python3 -B -m unittest tests.unit.test_luma_notes_store tests.unit.test_luma_appkit \
  tests.unit.test_luma_appkit_conformance tests.unit.test_luma_appkit_families.KitGuard \
  tests.unit.test_desktop_targets tests.unit.test_desktop_package_matrix
python3 -B tools/check-devices.py
python3 -B -m unittest tests.unit.test_device_tree
