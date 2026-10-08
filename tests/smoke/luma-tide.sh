#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
fail() { printf 'Tide smoke test: FAIL: %s\n' "$1" >&2; exit 1; }

manifest="$repo_root/src/luma-tide/luma-app.toml"
desktop="$repo_root/src/luma-tide/data/org.projectluma.Tide.desktop"
metainfo="$repo_root/src/luma-tide/data/org.projectluma.Tide.metainfo.xml"
spec="$repo_root/packaging/rpm/luma-tide.spec"

for required in "$manifest" "$desktop" "$metainfo" "$spec"; do
  [ -f "$required" ] || fail "missing source contract: $required"
done

grep -Fq 'id = "org.projectluma.Tide"' "$manifest" || fail 'manifest ID is not stable'
grep -Fq 'presentation_modes = ["windowed", "fullscreen-mobile"]' "$manifest" || \
  fail 'one manifest does not declare both presentation modes'
grep -Fq 'class TideWindow(AppWindow)' \
  "$repo_root/src/luma-tide/luma_tide/application.py" || fail 'Tide bypasses the shared AppWindow'
grep -Fq 'self.context.presentation' \
  "$repo_root/src/luma-tide/luma_tide/application.py" || fail 'Tide ignores the inherited presentation context'
grep -Fq 'self.context = AppContext.from_environment()' \
  "$repo_root/src/luma-platform/appkit/luma_appkit/widgets.py" || fail 'shared AppWindow lacks the system presentation/input context'
grep -Fq 'LumaSemantics.SemanticObject.new' \
  "$repo_root/src/luma-tide/luma_tide/application.py" || fail 'Tide lacks semantic objects'
grep -Fq 'Requires:       gstreamer1-plugins-good' "$spec" || \
  fail 'Tide lacks the standard platform audio sink dependency'
python3 - "$repo_root/src/luma-tide/luma_tide/playback.py" <<'PYTEST' || fail 'Tide bypasses platform audio selection'
import ast, sys
from pathlib import Path
calls = [node for node in ast.walk(ast.parse(Path(sys.argv[1]).read_text()))
         if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
         and node.func.attr == "make" and node.args and isinstance(node.args[0], ast.Constant)]
names = {node.args[0].value for node in calls}
assert "playbin3" in names and "pipewiresink" not in names, names
PYTEST
grep -Fq 'org.mpris.MediaPlayer2.Player' "$repo_root/src/luma-tide/luma_tide/mpris.py" || \
  fail 'Tide does not export MPRIS'

if command -v rg >/dev/null 2>&1; then
  forbidden_runtime=$(rg -ni '(waydroid|android\.media|android\.provider|webview|electron)' \
    "$repo_root/src/luma-tide" "$spec" || true)
else
  forbidden_runtime=$(grep -REni '(waydroid|android\.media|android\.provider|webview|electron)' \
    "$repo_root/src/luma-tide" "$spec" || true)
fi
if [ -n "$forbidden_runtime" ]; then
  printf '%s\n' "$forbidden_runtime"
  fail 'Tide depends on an Android or browser runtime'
fi

PYTHONPATH="$repo_root/src/luma-tide" python3 -m unittest discover \
  -s "$repo_root/tests/luma-tide" >/dev/null || fail 'Tide unit tests failed'

if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$desktop" || fail 'desktop entry is invalid'
fi
if command -v appstreamcli >/dev/null 2>&1; then
  appstreamcli validate --no-net "$metainfo" || fail 'AppStream metadata is invalid'
fi

if command -v rpm >/dev/null 2>&1 && rpm -q luma-tide >/dev/null 2>&1; then
  rpm -ql luma-tide | grep -Fxq '/usr/bin/org.projectluma.Tide' || \
    fail 'installed RPM lacks the Tide executable'
  rpm -ql luma-tide | grep -Fxq '/usr/share/applications/org.projectluma.Tide.desktop' || \
    fail 'installed RPM lacks its launcher'
fi

printf 'Tide smoke test: PASS\n'
