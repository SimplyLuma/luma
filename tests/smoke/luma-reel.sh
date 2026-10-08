#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
fail() { printf 'Reel smoke test: FAIL: %s\n' "$1" >&2; exit 1; }

source_root="$repo_root/src/luma-reel"
manifest="$source_root/luma-app.toml"
desktop="$source_root/data/org.projectluma.Reel.desktop"
metainfo="$source_root/data/org.projectluma.Reel.metainfo.xml"
spec="$repo_root/packaging/rpm/luma-reel.spec"

for required in "$manifest" "$desktop" "$metainfo" "$spec"; do
  [ -f "$required" ] || fail "missing source contract: $required"
done

grep -Fq 'id = "org.projectluma.Reel"' "$manifest" || fail 'application ID is not stable'
grep -Fq 'presentation_modes = ["windowed", "fullscreen-mobile"]' "$manifest" || \
  fail 'one manifest does not declare both presentation modes'
grep -Fq 'LumaUI.Context.new_from_environment()' \
  "$source_root/luma_reel/window.py" || fail 'Reel bypasses presentation/input context'
grep -Fq 'LumaSemantics.SemanticObject.new' \
  "$source_root/luma_reel/window.py" || fail 'Reel lacks semantic objects'
grep -Fq 'GES.Pipeline' "$source_root/luma_reel/ges_engine.py" || \
  fail 'preview/export do not share the native GES timeline engine'
grep -Fq 'use_proxies=False' "$source_root/luma_reel/export.py" || \
  fail 'export does not explicitly require source originals'
grep -Fq 'os.replace' "$source_root/luma_reel/export.py" || \
  fail 'export does not atomically finalize its temporary sibling'

grep -Fxq 'luma-reel' "$repo_root/config/shared/application-packages.txt" || \
  fail 'Reel is absent from the shared application role'
# Both composition paths must consume the exact declared package.
. "$repo_root/config/desktop/inputs.env"
grep -Fxq "$LUMA_REEL_NEVRA" \
  "$repo_root/config/desktop/packages.txt" || fail 'desktop Reel package is not exactly pinned'
grep -Fq '$LUMA_REEL_NEVRA.rpm' "$repo_root/scripts/vm/compose-desktop-image.sh" || \
  fail 'desktop composition does not consume the pinned Reel RPM'
grep -Fq '$LUMA_REEL_NEVRA.rpm' "$repo_root/scripts/mobile/compose-fp6-rootfs.sh" || \
  fail 'mobile composition does not consume the same pinned Reel RPM'

if command -v rg >/dev/null 2>&1; then
  forbidden_runtime=$(rg -ni '(waydroid|android\.media|android\.provider|webview|webkit|electron)' \
    "$source_root/luma_reel" "$source_root/bin" "$spec" || true)
else
  forbidden_runtime=$(grep -REni '(waydroid|android\.media|android\.provider|webview|webkit|electron)' \
    "$source_root/luma_reel" "$source_root/bin" "$spec" || true)
fi
if [ -n "$forbidden_runtime" ]; then
  printf '%s\n' "$forbidden_runtime"
  fail 'Reel depends on an Android or browser runtime'
fi

PYTHONPATH="$source_root" python3 -m unittest discover \
  -s "$source_root/tests" -p 'test_*.py' >/dev/null || fail 'Reel unit tests failed'

if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$desktop" || fail 'desktop entry is invalid'
fi
if command -v appstreamcli >/dev/null 2>&1; then
  appstreamcli validate --no-net "$metainfo" || fail 'AppStream metadata is invalid'
fi

if command -v rpm >/dev/null 2>&1 && rpm -q luma-reel >/dev/null 2>&1; then
  rpm -ql luma-reel | grep -Fxq '/usr/bin/luma-reel' || \
    fail 'installed RPM lacks the Reel executable'
  rpm -ql luma-reel | grep -Fxq '/usr/share/applications/org.projectluma.Reel.desktop' || \
    fail 'installed RPM lacks its launcher'
fi

printf 'Reel smoke test: PASS\n'
