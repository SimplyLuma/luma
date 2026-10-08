#!/bin/sh
# lumaui-conform: stage a scratch copy of the LumaUI preview from a worktree
# (the kit, its sheets and icons, the gallery, prairie_apps/prairie_ui, the
# Prairie sheets and bin/run), the same layout sync-lumaui-preview.sh puts on
# the ThinkPad. Nothing is installed; gtk_capture.sh copies it where it runs.
#   stage_preview.sh <worktree> <dest>
set -eu
WORKTREE=$1 stage=$2
PLATFORM_NVR=${LUMAUI_PLATFORM_NVR:-0.1.0-1.luma.87.lumaui20260928.1.fc44}
APPS_NVR=${LUMAUI_APPS_NVR:-0.1.0-1.luma.81.lumaui20260928.1.fc44}

mkdir -p "$stage/python/icons" "$stage/share/prairie-core" "$stage/bin"
appkit="$WORKTREE/src/luma-platform/appkit"
cp -R "$appkit/luma_appkit" "$stage/python/"
cp "$appkit"/*.css "$WORKTREE/src/luma-platform/ui/luma-ui.css" "$stage/python/"
cp "$appkit"/icons/*.svg "$stage/python/icons/"
cp "$WORKTREE"/assets/icon-theme/Prairie/symbolic/actions/lumaui-*-symbolic.svg "$stage/python/icons/" 2>/dev/null || true
cp "$WORKTREE/src/luma-platform/tools/lumaui-gallery/"*.py "$stage/python/" 2>/dev/null || true
for d in prairie_apps prairie_ui; do
  [ -d "$WORKTREE/src/prairie-core/$d" ] && cp -R "$WORKTREE/src/prairie-core/$d" "$stage/python/"
done
cp "$WORKTREE"/src/prairie-core/style/*.css "$stage/share/prairie-core/" 2>/dev/null || true
find "$stage/python" -name __pycache__ -type d -prune -exec rm -rf {} +
printf 'luma-developer-platform %s\nprairie-core-apps %s\n' "$PLATFORM_NVR" "$APPS_NVR" > "$stage/BASE-NVR"

cat > "$stage/bin/run" <<'RUN'
#!/bin/sh
# LumaUI preview: runs the LumaUI Gallery, or one Prairie app, against the kit
# in this directory. Refuses when the installed platform differs from BASE-NVR.
#   bin/run gallery [...]   |   bin/run contacts   (any prairie_apps module)
#   bin/run --module luma_tide[.app] [...]   (any module on LUMAUI_EXTRA_PYTHONPATH:
#   its main() when it has one, else run as `python3 -m`)
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app=${1:-gallery} module=
[ "$#" -gt 0 ] && shift
if [ "$app" = --module ]; then module=${1:-}; [ "$#" -gt 0 ] && shift; app=$module; fi
case "$app" in *[!a-z_]*|'') [ -n "$module" ] && case "$module" in *[!A-Za-z0-9_.]*|.*|'') ;; *) app=ok ;; esac ;; esac
case "$app" in *[!a-z_]*|'') echo "lumaui preview: not an app or module name: ${module:-$app}" >&2; exit 2 ;; esac
while read -r package nvr; do
  have=$(rpm -q --qf '%{VERSION}-%{RELEASE}' "$package" 2>/dev/null) || have=
  if [ "$have" != "$nvr" ]; then
    echo "lumaui preview: built on $package $nvr, but ${have:-nothing} is installed." >&2
    exit 3
  fi
done < "$here/BASE-NVR"
export PYTHONPATH="$here/python${LUMAUI_EXTRA_PYTHONPATH:+:$LUMAUI_EXTRA_PYTHONPATH}"
export LUMA_APPKIT_ICON_PATH="$here/python/icons"
for sheet in "$here"/share/prairie-core/*.css; do
  [ -e "$sheet" ] || continue
  name=$(basename "$sheet" .css | tr a-z A-Z)
  export "LUMA_${name}_STYLE_PATH=$sheet"
done
if [ -n "$module" ]; then
  exec python3 -c "import importlib, runpy, sys; sys.argv[0] = '$module'; m = importlib.import_module('$module')
if callable(getattr(m, 'main', None)): raise SystemExit(m.main())
runpy.run_module('$module', run_name='__main__', alter_sys=True)" "$@"
fi
if [ "$app" = gallery ]; then
  exec python3 "$here/python/lumaui_gallery.py" "$@"
fi
# Its own application ID, so it never activates (or is activated as) the installed app;
# a conform capture (private bus, headless) keeps the real one: LUMAUI_KEEP_APP_ID=1.
suffix=.LumaUIPreview
[ -n "${LUMAUI_KEEP_APP_ID:-}" ] && suffix=
exec python3 -c "import sys, prairie_apps.$app as m; m.APP_ID = getattr(m, 'APP_ID', 'org.projectluma.$app') + '$suffix'; sys.argv[0] = 'prairie-$app'; raise SystemExit(m.main())" "$@"
RUN
chmod +x "$stage/bin/run"
