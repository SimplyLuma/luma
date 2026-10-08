#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
fail() { printf 'Darkroom smoke test: FAIL: %s\n' "$1" >&2; exit 1; }
source_root="$repo_root/src/luma-darkroom"

for required in "$source_root/luma-app.toml" "$source_root/data/org.projectluma.Darkroom.desktop" "$repo_root/packaging/rpm/luma-darkroom.spec"; do
  [ -f "$required" ] || fail "missing source contract: $required"
done
grep -Fq 'id = "org.projectluma.Darkroom"' "$source_root/luma-app.toml" || fail 'application ID is not stable'
grep -Fq 'presentation_modes = ["windowed", "fullscreen-mobile"]' "$source_root/luma-app.toml" || fail 'one manifest does not cover desktop and mobile'
grep -Fq 'LumaUI.Context.new_from_environment()' "$source_root/luma_darkroom/window.py" || fail 'Darkroom bypasses capability context'
grep -Fq 'LumaUI.Histogram.new()' "$source_root/luma_darkroom/window.py" || fail 'Darkroom does not use shared histogram'
grep -Fq 'LumaUI.CurveEditor.new()' "$source_root/luma_darkroom/window.py" || fail 'Darkroom does not use shared curves'
grep -Fq 'ImageCms.profileToProfile' "$source_root/luma_darkroom/engine.py" || fail 'embedded profiles are not converted for display'
grep -Fq 'max_dimension=None' "$source_root/luma_darkroom/engine.py" || fail 'export does not reopen full-resolution source'
grep -Fq 'os.replace' "$source_root/luma_darkroom/model.py" || fail 'documents are not atomically finalized'
grep -Fxq 'luma-darkroom' "$repo_root/config/shared/application-packages.txt" || fail 'Darkroom is absent from shared applications'
grep -Fq 'luma-darkroom-0.1.0-1.luma.1.fc44.noarch' "$repo_root/config/desktop/packages.txt" || fail 'desktop Darkroom package is not pinned'
grep -Fq '$LUMA_DARKROOM_NEVRA.rpm' "$repo_root/scripts/vm/compose-desktop-image.sh" || fail 'desktop does not compose Darkroom'
grep -Fq '$LUMA_DARKROOM_NEVRA.rpm' "$repo_root/scripts/mobile/compose-fp6-rootfs.sh" || fail 'mobile does not compose the same Darkroom package'
PYTHONPATH="$source_root" python3 -m unittest discover -s "$source_root/tests" -p 'test_*.py' >/dev/null || fail 'unit tests failed'
printf 'Darkroom smoke test: PASS\n'

