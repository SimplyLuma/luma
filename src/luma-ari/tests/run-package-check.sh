#!/bin/bash
# Run inside the approved toolbox after staging; never use the user's stores.
set -euo pipefail
ari_worktree=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
ari_stage_python=${1:?Supply the staged Python site-packages directory}
ari_success_file=${2:?Supply an isolated completion marker path}
test -f "$ari_stage_python/ari/service.py"
ari_check=$(mktemp -d /tmp/ari-package-check.XXXXXX)
trap 'rm -rf "$ari_check"' EXIT
mkdir -m 700 "$ari_check/runtime" "$ari_check/config" "$ari_check/data" "$ari_check/cache" "$ari_check/state" "$ari_check/package"
cp -R "$ari_worktree/src/luma-ari/ari" "$ari_worktree/src/luma-ari/ari_ui" "$ari_worktree/src/luma-ari/data" "$ari_worktree/src/luma-ari/eval" "$ari_check/package/"
mkdir -p "$ari_check/package/tests/fixtures" "$ari_check/package/tests/scenarios"
cp "$ari_worktree/tests/luma-ari/"*.py "$ari_check/package/tests/"
cp "$ari_worktree/tests/fixtures/ari-v70.json" "$ari_check/package/tests/fixtures/"
cp "$ari_worktree/tools/lumaui-conform/scenarios/ari.json" "$ari_check/package/tests/scenarios/"
# Set these here, inside the final runtime after toolbox has found its container.
export XDG_RUNTIME_DIR="$ari_check/runtime" XDG_CONFIG_HOME="$ari_check/config"
export XDG_DATA_HOME="$ari_check/data" XDG_CACHE_HOME="$ari_check/cache" XDG_STATE_HOME="$ari_check/state"
export GSETTINGS_BACKEND=memory
dbus-run-session -- bash -c 'set -euo pipefail
cd "$1"
PYTHONPATH=$PWD GSETTINGS_BACKEND=memory python3 -m unittest discover -s tests -p "test_*.py" -v
python3 -m py_compile ari/*.py ari/tools/*.py ari_ui/*.py eval/run.py
PYTHONPATH="$2" python3 -P -c "import ari.cloud, ari.service; print(ari.cloud.__file__); print(ari.service.__file__)"
glib-compile-schemas --strict --dry-run data
python3 -c "import json; json.load(open(\"eval/cases.json\"))"
desktop-file-validate data/org.projectluma.Ari.desktop
appstream-util validate-relax --nonet data/org.projectluma.Ari.metainfo.xml
' -- "$ari_check/package" "$ari_stage_python"
# Some toolbox versions mask the child's exit code. The caller must require
# this marker as well as the relay's completed return code before claiming PASS.
printf '%s\n' ARI_PACKAGE_CHECK_PASS > "$ari_success_file"
