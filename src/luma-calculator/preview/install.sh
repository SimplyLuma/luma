#!/bin/sh
# Install Calculator's separate, source-only LumaUI preview after the gate passes.
set -eu

src=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
dest="$HOME/.local/share/luma-dev/lumaui-calc"
launcher="$HOME/.local/share/applications/org.projectluma.Calc.LumaUIPreview.desktop"

LUMAUI_HOST=local LUMAUI_WORKTREE="$src" \
  LUMAUI_DEST=.local/share/luma-dev/lumaui-calc LUMAUI_LAUNCHER=calc \
  sh "$HOME/Documents/LumaDesign/tools/sync-lumaui-preview.sh"

rsync -a --delete --exclude __pycache__ "$src/src/luma-calculator/luma_calc/" "$dest/python/luma_calc/"
mkdir -p "$dest/python/style" "$dest/python/icons/hicolor/scalable/apps"
cp "$src/src/luma-calculator/style/calculator.css" "$dest/python/style/calculator.css"
cp "$src/src/luma-calculator/icons/hicolor/scalable/apps/luma-v3-calculator.svg" \
  "$dest/python/icons/hicolor/scalable/apps/luma-v3-calculator.svg"

cat > "$dest/bin/calc-preview" <<'RUN'
#!/bin/sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export PYTHONPATH="$here/python"
export LUMA_APPKIT_ICON_PATH="$here/python/icons"
export LUMA_CALC_STYLE_PATH="$here/python/style/calculator.css"
exec python3 -c 'import luma_calc.app as app; app.APP_ID = "org.projectluma.Calculator.LumaUIPreview"; raise SystemExit(app.main())'
RUN
chmod +x "$dest/bin/calc-preview"

cat > "$launcher" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Calculator (LumaUI preview)
Comment=Calculator built from LumaUI source
Exec=$dest/bin/calc-preview
Icon=$dest/python/icons/hicolor/scalable/apps/luma-v3-calculator.svg
Terminal=false
Categories=Utility;Calculator;
StartupNotify=true
StartupWMClass=org.projectluma.Calculator.LumaUIPreview
DESKTOP

printf 'Calculator preview installed from %s; launcher %s\n' "$(git -C "$src" rev-parse --short HEAD)" "$launcher"
