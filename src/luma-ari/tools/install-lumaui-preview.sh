#!/bin/sh
# Run through host only after the full Ari conform gate passes.
# This installs only Ari's contained preview and its separate user launcher.
set -eu
ari_worktree=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
ari_preview_root="$HOME/.local/share/luma-dev/lumaui-ari"
ari_launcher="$HOME/.local/share/applications/org.projectluma.Ari.LumaUIPreview.desktop"
# Sync kit files without publishing the helper's Prairie-specific launcher.
LUMAUI_HOST=local LUMAUI_WORKTREE="$ari_worktree" \
  LUMAUI_DEST=.local/share/luma-dev/lumaui-ari LUMAUI_LAUNCHER= \
  sh "$HOME/Documents/LumaDesign/tools/sync-lumaui-preview.sh"
rsync -a --exclude __pycache__ "$ari_worktree/src/luma-ari/ari" \
  "$ari_worktree/src/luma-ari/ari_ui" "$ari_preview_root/python/"
rsync -a "$ari_worktree/src/luma-ari/data/" "$ari_preview_root/python/data/"
ari_run_tmp=$(mktemp "$ari_preview_root/bin/.ari-run.XXXXXX")
ari_launcher_tmp=$(mktemp "$ari_preview_root/.ari-launcher.XXXXXX")
trap 'rm -f "$ari_run_tmp" "$ari_launcher_tmp"' EXIT
cat > "$ari_run_tmp" <<'RUN'
#!/bin/sh
set -eu
ari_preview_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# Closing an existing preview must still work after installed package versions
# change. Ordinary launches retain the BASE-NVR compatibility guard.
if [ "$#" -ne 1 ] || [ "$1" != "--close-preview" ]; then
while read -r ari_package ari_nvr; do
  ari_have=$(rpm -q --qf '%{VERSION}-%{RELEASE}' "$ari_package" 2>/dev/null) || ari_have=
  if [ "$ari_have" != "$ari_nvr" ]; then
    echo "Ari preview: $ari_package differs from the preview's BASE-NVR; re-sync first." >&2
    exit 3
  fi
done < "$ari_preview_root/BASE-NVR"
fi
export LUMA_ARI_PREVIEW=1
unset LUMA_ARI_FIXTURE
export PYTHONPATH="$ari_preview_root/python"
export LUMA_APPKIT_ICON_PATH="$ari_preview_root/python/icons"
exec python3 -m ari_ui.lumaui_app "$@"
RUN
chmod 755 "$ari_run_tmp"
mv "$ari_run_tmp" "$ari_preview_root/bin/run"
cat > "$ari_launcher_tmp" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Ari (LumaUI preview)
Comment=Ari rebuilt on LumaUI, under its own application identity
Exec="$ari_preview_root/bin/run"
Icon=org.projectluma.Ari
Terminal=false
Categories=Utility;
StartupNotify=true
StartupWMClass=org.projectluma.Ari.LumaUIPreview
DESKTOP
chmod 644 "$ari_launcher_tmp"
mv "$ari_launcher_tmp" "$ari_launcher"
printf '%s\n' "Ari preview installed; launcher: $ari_preview_root/bin/run"
