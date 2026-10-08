#!/bin/bash
# Build a synthetic home, let a real LocalSearch crawl it, then run
# file-index-gjs.js. Needs gjs, localsearch, tinysparql, dbus-daemon.
# localsearch-harness.sh UI_DIR [FILLER_FILES]
set -euo pipefail
ui=$1
filler=${2:-100000}
here=$(cd "$(dirname "$0")" && pwd)
# LocalSearch never indexes under /tmp, so the synthetic home lives elsewhere.
base=${LUMA_SEARCH_TEST_BASE:-/var/lib/luma-search-test}
mkdir -p "$base"
export HOME=$(mktemp -d "$base/home.XXXXXX")
export XDG_RUNTIME_DIR=$HOME/.run XDG_CACHE_HOME=$HOME/.cache XDG_DATA_HOME=$HOME/.local/share
export XDG_CONFIG_HOME=$HOME/.config XDG_STATE_HOME=$HOME/.local/state
mkdir -m 700 -p "$XDG_RUNTIME_DIR" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME/glib-2.0/settings" "$XDG_STATE_HOME"
cd "$HOME"
mkdir -p Desktop Documents Downloads Music Pictures Videos depot depot-1840284-backup-x2 depot-old \
  Projects/depot/build/depot Projects/site/node_modules/depot .hidden/depot \
  Documents/Taxes Documents/Work/2025/Q3/archive/drafts "Music/Beyoncé"
for f in "Documents/report.pdf" "Documents/report (copy).pdf" "Documents/report (1).pdf" "Documents/report.pdf.bak" \
  "Documents/budget.ods" "Documents/Work/2025/Q3/archive/drafts/budget.ods" \
  "Documents/Taxes/invoice-march.pdf" "Documents/Taxes/invoice-april.pdf" "Pictures/Café Menu.png" \
  "Projects/site/node_modules/depot/index.js" ".hidden/depot/notes.txt"; do
  printf 'x' >"$f"
done
python3 - "$filler" <<'PY'
import os, sys
n = int(sys.argv[1])
words = ["notes", "report", "quarterly", "photo", "draft", "invoice", "summary", "plan", "depot", "budget"]
for i in range(n):
    d = f"Archive/set{i % 50}/batch{i % 997}"
    os.makedirs(d, exist_ok=True)
    w = words[i % len(words)]
    name = f"{w}-{i}.txt" if i % 3 else f"{w} {i} copy.md"
    open(os.path.join(d, name), "w").close()
PY
now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat >"$XDG_DATA_HOME/recently-used.xbel" <<XBEL
<?xml version="1.0" encoding="UTF-8"?>
<xbel version="1.0" xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks" xmlns:mime="http://www.freedesktop.org/standards/shared-mime-info">
  <bookmark href="file://$HOME/Documents/Taxes/invoice-march.pdf" added="$now" modified="$now" visited="$now">
    <info><metadata owner="http://freedesktop.org"><mime:mime-type type="application/pdf"/>
      <bookmark:applications><bookmark:application name="Papers" exec="papers %u" modified="$now" count="5"/></bookmark:applications>
    </metadata></info>
  </bookmark>
</xbel>
XBEL
cat >"$XDG_CONFIG_HOME/glib-2.0/settings/keyfile" <<KEY
[org/freedesktop/tracker/miner/files]
index-recursive-directories=['\$HOME']
index-single-directories=@as []
index-removable-devices=false
index-optical-discs=false
enable-monitors=false
ignored-directories=['po', 'CVS', 'core-dumps', 'lost+found', 'node_modules', 'bower_components', '__pycache__', 'site-packages', 'venv', 'rpmbuild', 'BUILDROOT']
ignored-directories-with-content=['.trackerignore', '.git', '.hg', '.nomedia', '.noindex', 'CACHEDIR.TAG', 'pyvenv.cfg']
KEY
export GSETTINGS_BACKEND=keyfile
dbus-run-session -- bash -c '
  set -euo pipefail
  started=$(date +%s)
  /usr/libexec/localsearch-3 >"$HOME/localsearch.log" 2>&1 &
  sleep 1
  # D-Bus activation starts the indexer; wait until it is idle with a count
  # that has stopped changing.
  last=-1; stable=0
  for i in $(seq 1 450); do
    status=$(localsearch status 2>/dev/null || true)
    count=$(printf "%s" "$status" | sed -n "s/.*Currently indexed: \([0-9]*\) files.*/\1/p")
    if printf "%s" "$status" | grep -q "idle" && [ "${count:-0}" -gt 0 ] && [ "$count" = "$last" ]; then
      stable=$((stable + 1)); [ $stable -ge 3 ] && break
    else
      stable=0
    fi
    last=${count:-0}
    sleep 2
  done
  printf "%s\n" "$status" | head -3
  [ "${count:-0}" -gt 0 ] || { echo "LocalSearch indexed nothing"; tail -20 "$HOME/localsearch.log"; exit 1; }
  echo "indexing took $(( $(date +%s) - started )) s"
  gjs -m "'"$here"'/file-index-gjs.js" "'"$ui"'" "$HOME"
'
