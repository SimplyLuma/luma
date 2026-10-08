#!/bin/sh
# lumaui-conform, step 2 (called by run.sh): run the real app headless, once
# per state, from a scratch copy of the LumaUI preview staged from <worktree>.
#   gtk_capture.sh <scenario.json> <out-dir> <theme> <worktree>
# Where it runs is LUMAUI_CONFORM_HOST (run.sh has already handed "server"
# runs to the build server, where this script runs inside the container):
#   local     here, in this machine's GTK stack (the server's container);
#   thinkpad  on the ThinkPad over ssh (LUMAUI_HOST), one capture at a time,
#             only while its load is below its CPU count. The scratch copy
#             lives in ~/.cache/lumaui-conform/<run> there and is removed on
#             any exit, Ctrl-C included.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
scenario=$1 out=$2 theme=$3 worktree=$4
route=${LUMAUI_CONFORM_HOST:-local}
host=${LUMAUI_HOST:-nick@192.168.1.143}

# The software renderer is the same on every machine; the ThinkPad's GPU is not.
# The spec variant's phone flag also pins the native device capability; width
# alone cannot distinguish a handset from a narrow tiled desktop window.
node -e '
const fs = require("fs"), [scn, out, theme] = process.argv.slice(1), s = JSON.parse(fs.readFileSync(scn)), m = JSON.parse(fs.readFileSync(out + "/spec-meta.json"));
const want = new Set(m.states.map(x => x.name)), win = m.states[0].window;
const appId = s.gtk.app_id || (/^[a-z_]+$/.test(s.gtk.module || "") && s.gtk.module !== "gallery" ? "org.projectluma." + s.gtk.module[0].toUpperCase() + s.gtk.module.slice(1) : "");
fs.writeFileSync(out + "/gtk-plan.json", JSON.stringify({ module: s.gtk.module || "", binary: s.gtk.binary || "", args: s.gtk.args || [], app_id: appId, pythonpath: s.gtk.pythonpath || [], theme, phone: m.phone === true, size: `${Math.round(win.w)}x${Math.round(win.h)}`, env: s.gtk.env || {},
  font: s.gtk.font || "Figtree 11", renderer: process.env.LUMAUI_CONFORM_RENDERER || "cairo", monitor: "1920x1200",
  states: s.states.filter(x => want.has(x.name)).map(x => { const w = (m.states.find(y => y.name === x.name) || {}).window || win;
    return { name: x.name, size: `${Math.round(w.w)}x${Math.round(w.h)}`, actions: x.gtk || [] }; }) }, null, 1));' "$scenario" "$out" "$theme"

# Stage the run directory: preview (kit + harness), fixtures, the scenario's pythonpath trees.
stage=$(mktemp -d "${TMPDIR:-/tmp}/lumaui-conform.XXXXXX")
remote_run=
cleanup() {
  status=$?
  rm -rf "$stage"
  if [ -n "$remote_run" ]; then
    # Stop whatever this run started on the ThinkPad (remote_capture.py stops its own mutter, bus
    # and app on SIGTERM), then drop the scratch copy. [.] keeps pkill off its own command line.
    pat="[.]${remote_run#.}/"
    ssh -o BatchMode=yes "$host" "pkill -TERM -f '$pat' 2>/dev/null && sleep 2; pkill -KILL -f '$pat' 2>/dev/null; rm -rf ~/$remote_run; true" || true
  fi
  exit $status
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP
# The kit comes from LUMAUI_CONFORM_KIT (a foundation tree sent with --kit) when the app's repo has none.
sh "$here/stage_preview.sh" "${LUMAUI_CONFORM_KIT:-$worktree}" "$stage/preview"
cp "$here/harness/sitecustomize.py" "$here/harness/lumaui_conform_harness.py" "$stage/preview/python/"
cp "$here/harness/remote_capture.py" "$out/gtk-plan.json" "$stage/"
node -e 'const s = JSON.parse(require("fs").readFileSync(process.argv[1])).gtk;
  for (const f of s.fixtures || []) console.log("fixtures\t" + f);
  for (const p of s.pythonpath || []) console.log("src\t" + p);' "$scenario" |
while IFS="$(printf '\t')" read -r kind path; do
  case "$path" in /*|*..*) echo "lumaui-conform: scenario paths are relative to the worktree: $path" >&2; exit 2 ;; esac
  if [ "$kind" = fixtures ]; then dest="$stage/fixtures"; else dest="$stage/src/$(dirname "$path")"; fi
  mkdir -p "$dest"
  cp -R "$worktree/$path" "$dest/"
done
find "$stage" -name __pycache__ -type d -prune -exec rm -rf {} +

case "$route" in
  local)
    LUMAUI_CONFORM_SLOTS=0 LUMAUI_CONFORM_LOAD_GUARD=0 python3 "$stage/remote_capture.py" "$stage" "$stage/gtk-plan.json"
    cp -R "$stage/out/." "$out/"
    ;;
  thinkpad)
    if node -e 'process.exit("build" in JSON.parse(require("fs").readFileSync(process.argv[1])).gtk ? 0 : 1)' "$scenario"; then
      echo "lumaui-conform: C/C++ apps (gtk.build) are built and captured on the server only" >&2; exit 2
    fi
    remote_run=.cache/lumaui-conform/$(date +%Y%m%d-%H%M%S)-$$
    ssh -o BatchMode=yes "$host" "mkdir -p ~/$remote_run"
    rsync -a "$stage/" "$host:$remote_run/"
    ssh -o BatchMode=yes "$host" "LUMAUI_CONFORM_SLOTS=1 LUMAUI_CONFORM_LOAD_GUARD=1 python3 ~/$remote_run/remote_capture.py \$HOME/$remote_run \$HOME/$remote_run/gtk-plan.json"
    rsync -a "$host:$remote_run/out/" "$out/"
    ;;
  *) echo "lumaui-conform: LUMAUI_CONFORM_HOST is server, thinkpad or local, not $route" >&2; exit 2 ;;
esac
