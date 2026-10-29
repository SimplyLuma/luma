#!/bin/bash
# Real app control tests stay on the server, never the Dell manual preview.
set -euo pipefail
ari_source=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
ari_run=$(mktemp -d /tmp/ari-accessibility.XXXXXX)
trap 'rm -rf "$ari_run"' EXIT
mkdir -m700 "$ari_run/runtime"
export PYTHONPATH="${ARI_ACCESSIBILITY_DEPS:+$ARI_ACCESSIBILITY_DEPS:}$ari_source/src/luma-ari:$ari_source/src/luma-platform/appkit:$ari_source/src/prairie-core"
export ATSPI_DBUS_IMPLEMENTATION=dbus-daemon GTK_A11Y=atspi GDK_BACKEND=x11 GSK_RENDERER=cairo GSETTINGS_BACKEND=memory PYTHONDONTWRITEBYTECODE=1
export LUMA_WEATHER_STYLE_PATH="$ari_source/src/prairie-core/style/weather.css" LUMA_APPKIT_ICON_PATH="$ari_source/assets/icon-theme/Prairie/symbolic/actions"
unset LUMA_WEATHER_FIXTURE LUMA_ARI_FIXTURE LUMA_ARI_PREVIEW
ari_output="$DEV_ARTIFACT_DIR"
for ari_mode in 0 1 2; do
 export ARI_ACCESSIBILITY_MCP=$((ari_mode > 0)) ARI_ACCESSIBILITY_AGENT=$((ari_mode == 2))
 export XDG_CONFIG_HOME="$ari_run/$ari_mode/config" XDG_DATA_HOME="$ari_run/$ari_mode/data" XDG_CACHE_HOME="$ari_run/$ari_mode/cache" XDG_STATE_HOME="$ari_run/$ari_mode/state" XDG_RUNTIME_DIR="$ari_run/runtime"
 export DEV_ARTIFACT_DIR="$ari_output/mode-$ari_mode"
 mkdir -p "$DEV_ARTIFACT_DIR"
 dbus-run-session -- xvfb-run -a -s '-screen 0 1920x1200x24' "${ARI_TEST_PYTHON:-python3}" "$ari_source/tests/luma-ari/native_accessibility.py" > "$DEV_ARTIFACT_DIR/native.log" 2>&1
done
printf '%s\n' 'Real Weather search, activation, saved city and visible verification through direct API, MCP and scripted-Responses agent/approvals PASS'
