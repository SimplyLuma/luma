#!/bin/bash
# lumaui-conform, C/C++ route, build step: runs in localhost/lumaui-conform-cbuild on the
# build server (conform-serve.sh), before the capture containers.
#   c_build.sh <scenario.json>
# Builds LumaUI-1 (src/luma-platform of the kit tree) into /opt/conform/lumaui, then the
# app with meson against it into /opt/conform/app. /cache is the app's persistent cache
# (meson build dirs and ccache), so a repeat capture rebuilds only what changed.
# The scenario's gtk.build is one of ({} builds LumaUI-1 only, for Python fixtures over its typelibs):
#   {"source": "<dir in the worktree>", "meson_options": ["-Dx=y", ...], "lumaui": false}
#   {"patched_upstream": "<package>", "patches": "patches/<package>", "meson_options": [...]}
#     the upstream tarball and Fedora's patches from the package's Luma SRPM (/srpms), then
#     the worktree's patch series (0000, the spec patch, skipped). An optional
#     "patch_series": ["0001-name.patch", ...] selects the active files in exact order.
set -euo pipefail
list_worktree_patches() {  # <scenario.json> <patch directory>; NUL-delimited paths
  python3 - "$1" "$2" <<'PY'
import json
import pathlib
import sys

scenario, patch_dir = map(pathlib.Path, sys.argv[1:])
build = json.loads(scenario.read_text())["gtk"]["build"]
if not patch_dir.is_dir():
    sys.exit(f"c-build: patch directory does not exist: {patch_dir}")

if "patch_series" in build:
    series = build["patch_series"]
    if not isinstance(series, list):
        sys.exit("c-build: gtk.build.patch_series must be a list")
    if len(series) != len(set(map(str, series))):
        sys.exit("c-build: gtk.build.patch_series contains duplicate entries")
    paths = []
    for name in series:
        if (not isinstance(name, str) or not name or name != pathlib.Path(name).name
                or "/" in name or "\\" in name or "\n" in name or "\0" in name
                or not name.endswith(".patch") or name.startswith("0000-")):
            sys.exit(f"c-build: invalid gtk.build.patch_series entry: {name!r}")
        path = patch_dir / name
        if not path.is_file():
            sys.exit(f"c-build: listed patch does not exist: {path}")
        paths.append(path)
else:
    # Legacy scenarios use the directory's lexical order, except the spec patch.
    paths = [p for p in sorted(patch_dir.glob("*.patch"))
             if not p.name.startswith("0000-")]

for path in paths:
    sys.stdout.buffer.write(str(path).encode() + b"\0")
PY
}
if [ "${1:-}" = --list-worktree-patches ]; then
  list_worktree_patches "$2" "$3"
  exit
fi
scn=$1
wt=/w/src/wt
kit=${LUMAUI_CONFORM_KIT:-$wt}
export CCACHE_DIR=${CCACHE_DIR:-/cache/ccache} CCACHE_MAXSIZE=${CCACHE_MAXSIZE:-5G} CC="ccache gcc" CXX="ccache g++"
export PKG_CONFIG_PATH=/opt/conform/lumaui/lib64/pkgconfig:/opt/conform/lumaui/share/pkgconfig
export LD_LIBRARY_PATH=/opt/conform/lumaui/lib64 GI_TYPELIB_PATH=/opt/conform/lumaui/lib64/girepository-1.0
# Installed, the kit's headers sit under /usr/include; some apps include them as <luma-1/...>.
export CFLAGS="-I/opt/conform/lumaui/include" CXXFLAGS="-I/opt/conform/lumaui/include"
field() { python3 -c 'import json, sys; b = json.load(open(sys.argv[1]))["gtk"].get("build", {}); v = b.get(sys.argv[2], "")
print("\n".join(v) if isinstance(v, list) else v)' "$scn" "$1"; }
t0=$(date +%s)

setup() {  # setup <builddir> <srcdir> <prefix> [meson options...]
  local b=$1 s=$2 p=$3 log=/tmp/c-build.log; shift 3
  if [ -f "$b/build.ninja" ] && [ "$(cat "$b/.conform-src" 2>/dev/null)" = "$s $* $CFLAGS" ]; then
    meson setup --reconfigure "$b" "$s" > $log 2>&1 || { tail -40 $log >&2; exit 2; }
  else
    rm -rf "$b"
    meson setup "$b" "$s" --prefix="$p" --libdir=lib64 --buildtype=debugoptimized "$@" > $log 2>&1 || { tail -40 $log >&2; exit 2; }
    echo "$s $* $CFLAGS" > "$b/.conform-src"
  fi
  ninja -C "$b" -j "${CONFORM_JOBS:-4}" > $log 2>&1 || { grep -B2 -A12 -m3 -E "error|FAILED" $log >&2 || tail -40 $log >&2; exit 2; }
  meson install -C "$b" --quiet > $log 2>&1 || { tail -40 $log >&2; exit 2; }
}

# 1. LumaUI-1 from the kit tree (the per-kit build dir keeps it incremental); "lumaui": false skips it.
if [ "$(field lumaui)" != False ]; then
  setup "/cache/lumaui-$(basename "$kit")" "$kit/src/luma-platform" /opt/conform/lumaui
  echo "c-build: LumaUI-1 ready ($(( $(date +%s) - t0 )) s)"
fi

# 2. The app's source tree.
mapfile -t opts < <(field meson_options)
[ ${#opts[@]} -eq 1 ] && [ -z "${opts[0]}" ] && opts=()
pkg=$(field patched_upstream)
if [ -n "$pkg" ]; then
  srpm=$(ls /srpms/"$pkg"-[0-9]*.src.rpm 2>/dev/null | sort -V | tail -1)
  [ -n "$srpm" ] || { echo "c-build: no SRPM for $pkg on the server (/srv/lumaui-conform/srpms)" >&2; exit 2; }
  prep=$(mktemp -d /tmp/prep.XXXXXX)
  (cd "$prep" && rpm2cpio "$srpm" | cpio -id --quiet)
  # Pristine upstream + Fedora's own patches (Patch numbers below 1000, in order), then the
  # worktree's series in its order: exactly what %autosetup does with the series swapped in.
  tar -xf "$(ls "$prep/$pkg"-[0-9]*.tar.* | head -1)" -C "$prep"
  tree=$(dirname "$(find "$prep" -mindepth 2 -maxdepth 2 -name meson.build | head -1)")
  fedora=$(python3 - "$prep/$pkg.spec" <<'PY'
import re, sys
rows = []
for line in open(sys.argv[1]):
    m = re.match(r"Patch(\d*):\s*(\S+)", line)
    if m and int(m.group(1) or 0) < 1000:
        rows.append((int(m.group(1) or 0), m.group(2).split("/")[-1]))
print("\n".join(name for _, name in sorted(rows)))
PY
)
  for f in $fedora; do (cd "$tree" && patch -p1 -f -s --fuzz=0 --no-backup-if-mismatch < "$prep/$f") || { echo "c-build: Fedora patch $f does not apply" >&2; exit 2; }; done
  patches=$wt/$(field patches)
  patch_list=$(mktemp)
  list_worktree_patches "$scn" "$patches" > "$patch_list" || { rm -f "$patch_list"; exit 2; }
  mapfile -d '' -t patch_paths < "$patch_list"
  rm -f "$patch_list"
  n=0
  for p in "${patch_paths[@]}"; do
    (cd "$tree" && patch -p1 -f -s --fuzz=0 --no-backup-if-mismatch < "$p") || { echo "c-build: $(basename "$p") does not apply" >&2; exit 2; }
    n=$((n + 1))
  done
  # Same contents keep their mtimes, so ninja rebuilds only what the series changed.
  mkdir -p /cache/upstream-src
  rsync -rlc --delete "$tree/" /cache/upstream-src/
  rm -rf "$prep"
  src=/cache/upstream-src
  echo "c-build: $pkg $(basename "$srpm" .src.rpm | sed "s/^$pkg-//") upstream, $(echo $fedora | wc -w) Fedora patches, $n from the worktree"
elif [ -n "$(field source)" ]; then
  src=$wt/$(field source)
  [ -f "$src/meson.build" ] || { echo "c-build: no meson.build in $src (gtk.build.source)" >&2; exit 2; }
else
  echo "c-build: LumaUI-1 only (no gtk.build.source): the app runs from gtk.module with the kit's typelibs"
  exit 0
fi

# 3. The app, against LumaUI-1.
setup /cache/app "$src" /opt/conform/app "${opts[@]}"
if [ -d /opt/conform/app/share/glib-2.0/schemas ]; then glib-compile-schemas /opt/conform/app/share/glib-2.0/schemas; fi
ccache -s 2>/dev/null | grep -i -E "^(hits|cacheable)" | tr -s ' ' | tr '\n' ' ' || true
echo "c-build: app built ($(( $(date +%s) - t0 )) s)"
