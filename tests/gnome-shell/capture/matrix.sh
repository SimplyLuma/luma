#!/bin/bash
# Capture evidence matrix on the build server.
# matrix.sh RUN [C_CONTAINER=...]: writes /root/luma-shell-oracle/capture/evidence/RUN/<case>/{shell.log,results.json,*.png,*.webm}
RUN=${1:?run name}; H=/root/luma-shell-oracle/capture/harness; E=/root/luma-shell-oracle/capture/evidence/$RUN
mkdir -p $E
one() { # tag theme edge scale env...
  local tag=$1; shift
  timeout 600 bash $H/go.sh "$RUN-$tag" "$@" > $E/$tag.go.log 2>&1
  mkdir -p $E/$tag
  local O=/root/luma-shell-oracle/capture/out-$RUN-$tag
  cp $O/*.png $O/*.webm $O/results.json $O/opened.log $O/atspi.log $E/$tag/ 2>/dev/null
  grep -v "^GLib-GIO-Message: .*resource overlay" $O/shell.log > $E/$tag/shell.log
  rm -rf $O
}
for edge in bottom top; do for theme in light dark; do
  one bar-$edge-$theme-1x $theme $edge 1 C_ONLY=bar &
  one bar-$edge-$theme-2x $theme $edge 2 C_ONLY=bar
  wait
done; done
one bar-bottom-frost-1x frost bottom 1 C_ONLY=bar &
one bar-bottom-glass-1x glass bottom 1 C_ONLY=bar
wait
one flows light bottom 1 C_ONLY=paintprobe,shortcuts,selection,window,timer,thumbnail C_TIMEOUT=420 &
one record light bottom 1 C_ONLY=record C_TIMEOUT=300
wait
one saveto light bottom 1 C_ONLY=saveto C_TIMEOUT=300
# Log out and in: a new Shell on the same home reads the Options back.
one persist light bottom 1 C_ONLY=persist C_KEEP_HOME=1 C_HOME_TAG=$RUN-saveto C_TIMEOUT=200
one search-keyboard light bottom 1 C_ONLY=search,keyboard C_A11Y=true C_TIMEOUT=300 &
one multimonitor dark bottom 1 C_ONLY=multimonitor C_MONITORS=2560x1440,1920x1080 C_TIMEOUT=200
wait
touch $E/DONE
