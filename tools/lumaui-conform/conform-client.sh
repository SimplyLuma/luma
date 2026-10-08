#!/bin/bash
# lumaui-conform client: send a worktree to the build server's conform service
# and unpack the reports. Used by run.sh (the Mac) and, installed as
# ~/Documents/.luma-dev/bin/lumaui-conform, on the ThinkPad (host or capsule).
#
#   lumaui-conform <app> [--all | --theme dark|light] [--phone] [--spec-only]
#                  [--worktree DIR] [--kit DIR] [--out DIR] [--pixel PNGDIR] [--overlay DIR]
#
# --worktree  the tree to measure (default: the git checkout around $PWD).
# --kit       a LumaUI foundation tree, for repos without the kit
#             (default ~/Documents/wt/lumaui-kit-ref when the worktree has none).
# --out       where <app>/<stamp>-<variant>/ land (default ~/Documents/LumaDesign/conform).
# --pixel     pixel mode: <state>.png (or <variant>/<state>.png) from the app itself, compared
#             with the spec PNGs (Viola); no GTK capture, no element matching.
# --overlay   the Shell route (a scenario with a "shell" section, run on the ThinkPad): the
#             G_RESOURCE_OVERLAYS dir (default ~/.local/share/luma-shell-live/active).
# Sends git-tracked plus untracked, non-ignored files (for a ProjectLuma tree,
# only the platform and kit, Prairie, tools/lumaui-conform, tests/fixtures and the
# scenario's paths). Exit: 0 PASS, 1 FAIL, 2+ could not run.
# Environment: LUMAUI_CONFORM_SERVER (user@build-host.example), LUMAUI_CONFORM_PORT (6767).
set -euo pipefail
self=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")
server=${LUMAUI_CONFORM_SERVER:?Set LUMAUI_CONFORM_SERVER to your build host}
port=${LUMAUI_CONFORM_PORT:-6767}
key=$HOME/.ssh/lumaui_conform_ed25519
luma_dev=${LUMA_DEV:-$HOME/Documents/.luma-dev}

args=() worktree= kit= out= pixel= overlay=
while [ $# -gt 0 ]; do
  case "$1" in
    --pixel) pixel=$(cd "$2" && pwd -P); args+=(--pixel); shift ;;
    --overlay) overlay=$(cd "$2" && pwd -P); shift ;;
    --worktree) worktree=$2; shift ;;
    --kit) kit=$2; shift ;;
    --out) out=$2; shift ;;
    --theme) args+=("$1" "$2"); shift ;;
    *) args+=("$1") ;;
  esac
  shift
done
[ ${#args[@]} -ge 1 ] || { sed -n '6,17p' "$self" >&2; exit 2; }
app=${args[0]}
worktree=$(cd "${worktree:-.}" && { git rev-parse --show-toplevel 2>/dev/null || pwd -P; })
out=${out:-$HOME/Documents/LumaDesign/conform}

# In the Codex capsule the key is out of reach: run this same script on the host.
if [ ! -r "$key" ] && [ -f "$luma_dev/bin/host" ] && [ -z "${LUMAUI_CONFORM_ON_HOST:-}" ]; then
  rest=(); for a in "${args[@]}"; do [ "$a" = --pixel ] || rest+=("$a"); done
  exec bash "$luma_dev/bin/host" env LUMAUI_CONFORM_ON_HOST=1 bash "$self" "${rest[@]}" --worktree "$worktree" \
    ${kit:+--kit "$kit"} ${pixel:+--pixel "$pixel"} ${overlay:+--overlay "$overlay"} --out "$out"
fi
if [ -z "$kit" ] && [ ! -d "$worktree/src/luma-platform/appkit" ] && [ -d "$HOME/Documents/wt/lumaui-kit-ref" ]; then
  kit=$HOME/Documents/wt/lumaui-kit-ref
fi
[ -n "$kit" ] && kit=$(cd "$kit" && git rev-parse --show-toplevel)

# The Shell route: a scenario with a "shell" section is captured here (the ThinkPad's own Shell,
# headless, private bus and runtime dir; harness/shell_capture.py), then compared in pixel mode.
scn=
for t in "$worktree" ${kit:+"$kit"}; do [ -f "$t/tools/lumaui-conform/scenarios/$app.json" ] && { scn=$t/tools/lumaui-conform/scenarios/$app.json; break; }; done
if [ -z "$pixel" ] && [ -n "$scn" ] && python3 -c 'import json, sys; sys.exit(0 if "shell" in json.load(open(sys.argv[1])) else 1)' "$scn"; then
  shot=$(dirname "$(dirname "$scn")")/harness/shell_capture.py
  [ -f "$shot" ] || { echo "lumaui-conform: $shot is missing (merge work/lumaui-foundation)" >&2; exit 2; }
  pixel=$(mktemp -d "${TMPDIR:-/tmp}/lumaui-conform-shell.XXXXXX")
  trap 'rm -rf "$pixel"' EXIT
  vs=()
  case " ${args[*]} " in *" --all "*) vs=("dark 0" "light 0" "dark 1" "light 1") ;;
    *) t=dark; for ((i = 0; i < ${#args[@]}; i++)); do [ "${args[$i]}" = --theme ] && t=${args[$((i + 1))]}; done
       ph=0; case " ${args[*]} " in *" --phone "*) ph=1 ;; esac; vs=("$t $ph") ;; esac
  for v in "${vs[@]}"; do
    set -- $v
    python3 "$shot" "$scn" "$1" "$2" "$pixel/$1$([ "$2" = 1 ] && echo -phone)" "$overlay" || exit $?
  done
  args+=(--pixel)
fi

ssh_opts=(-p "$port" -o BatchMode=yes -o ServerAliveInterval=15)
[ -r "$key" ] && ssh_opts+=(-i "$key" -o IdentitiesOnly=yes)

# The file list: NUL-separated paths under wt/ and kit/, existing files only.
list() {  # list <tree> <prefix>
  local tree=$1 prefix=$2 scope=()
  if [ -d "$tree/src/luma-platform/appkit" ]; then
    scope=(src/luma-platform config/shared scripts/developer src/prairie-core
      assets/icon-theme/Prairie/symbolic/actions tools/lumaui-conform tests/fixtures)
    # Scenario paths belong to the app worktree, even when the scenario itself
    # comes from --kit. In particular, pythonpath "." must not expand the kit
    # archive to the entire foundation repository.
    if [ "$tree" = "$worktree" ] && [ -n "$scn" ]; then
      while IFS= read -r p; do [ -n "$p" ] && scope+=("$p"); done < <(python3 -c '
import json, sys
g = json.load(open(sys.argv[1])).get("gtk", {})
b = g.get("build", {})
print("\n".join(list(g.get("fixtures", [])) + list(g.get("pythonpath", [])) + [p for p in (b.get("source"), b.get("patches")) if p]))' "$scn")
    fi
  fi
  { if git -C "$tree" rev-parse --git-dir >/dev/null 2>&1; then
      git -C "$tree" ls-files -z --cached --others --exclude-standard -- "${scope[@]}"
    else  # not a git checkout: every file, minus VCS, caches and build output
      (cd "$tree" && find . \( -name .git -o -name __pycache__ -o -name node_modules -o -name _build -o -name builddir \) -prune \
        -o \( -type f -o -type l \) -print0)
    fi; } |
    while IFS= read -r -d '' f; do
      f=${f#./}
      [ -e "$tree/$f" ] || [ -L "$tree/$f" ] && printf '%s/%s\0' "$prefix" "$f"
    done
}
stage=$(mktemp -d "${TMPDIR:-/tmp}/lumaui-conform-client.XXXXXX")
trap 'rm -rf "$stage"; case "$pixel" in */lumaui-conform-shell.*) rm -rf "$pixel" ;; esac' EXIT
ln -s "$worktree" "$stage/wt"
[ -n "$kit" ] && ln -s "$kit" "$stage/kit"
[ -n "$pixel" ] && ln -s "$pixel" "$stage/pixel"
{ list "$worktree" wt; [ -n "$kit" ] && list "$kit" kit
  [ -n "$pixel" ] && (cd "$pixel" && find . -name '*.png' -print0) | while IFS= read -r -d '' f; do printf 'pixel/%s\0' "${f#./}"; done
  true; } > "$stage/files"
mkdir -p "$stage/result" "$out"
start=$(date +%s)
set +e
tar_opts=()
tar --version 2>/dev/null | grep -q bsdtar && tar_opts=(--no-xattrs --no-mac-metadata)
(cd "$stage" && tar "${tar_opts[@]}" --null -T files -cf -) |
  ssh "${ssh_opts[@]}" "$server" /srv/lumaui-conform/conform-serve.sh "${args[@]}" |
  tar -x -C "$stage/result" -f -
codes=("${PIPESTATUS[@]}")
set -e
status=${codes[1]}
[ "${codes[0]}" = 0 ] || { echo "lumaui-conform: could not read the worktree" >&2; exit 2; }
cp -R "$stage/result/." "$out/"
for d in "$stage"/result/"$app"/*/; do
  [ -d "$d" ] || continue
  name=$(basename "$d")
  verdict=$(grep -m1 -o -E 'PASS|FAIL' "$d/REPORT.md" 2>/dev/null || echo "no report")
  echo "$verdict  $out/$app/$name/"
done
echo "lumaui-conform $app: $([ "$status" = 0 ] && echo PASS || { [ "$status" = 1 ] && echo FAIL || echo "could not run ($status)"; }) in $(( $(date +%s) - start )) s" >&2
exit "$status"
