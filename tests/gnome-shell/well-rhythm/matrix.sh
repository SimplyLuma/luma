#!/bin/bash
# Evidence for Shell patch 0151: the Well row and the shelf's hover rhythm in
# every appearance mode, at 100% and 125%, at rest, on hover and pressed, and
# focus after pointer and keyboard use.
# B_EVIDENCE is the output directory; B_OVERLAY_DEFAULT picks the overlay or
# the installed RPM (none).
H=${H:-/root/luma-shell-oracle/beam/harness}
# The Beam harness, with run102.sh's fake browser player (B_CHROME) and
# Nick's shelf layout (B_NICK).
export B_RUN=run102.sh
one() { # tag theme edge scale env...
  local tag=$1; shift
  timeout 480 bash $H/go102.sh "$tag" "$@" > $E/$tag.log 2>&1
  mkdir -p $E/$tag
  cp /root/luma-shell-oracle/beam/out-$tag/*.png $E/$tag/ 2>/dev/null
  grep "\[well\]\|\[focus\]\|\[islands\]\|Luma Well" /root/luma-shell-oracle/beam/out-$tag/shell.log > $E/$tag/probe.log
  grep -n "JS ERROR\|TypeError\|ReferenceError\|SyntaxError" /root/luma-shell-oracle/beam/out-$tag/shell.log > $E/$tag/js-errors.log
  rm -rf /root/luma-shell-oracle/beam/out-$tag
}
E=${B_EVIDENCE:?}; mkdir -p $E
export B_CONTAINER=${B_CONTAINER:-luma-beam-sound}
W=/oracle/beam/well151/well.js
F=/oracle/beam/well151/focus.js
I=/oracle/beam/well151/islands.js
for theme in light dark frost glass; do
  one well-$theme-100 $theme bottom 1 B_EVAL=$W B_SNI=2 B_NICK=1 B_TIMEOUT=220
  one well-$theme-125 $theme bottom 1 B_EVAL=$W B_SNI=2 B_NICK=1 W_SCALE=1.25 B_MONITORS=1920x1200 B_TIMEOUT=260
  # Focus after pointer and keyboard use, and what paints on a hovered dock icon.
  one focus-$theme-100 $theme bottom 1 B_EVAL=$F B_SNI=2 B_NICK=1 B_CHROME=1 B_TIMEOUT=240
  one focus-$theme-125 $theme bottom 1 B_EVAL=$F B_SNI=2 B_NICK=1 B_CHROME=1 W_SCALE=1.25 B_MONITORS=1920x1200 B_TIMEOUT=280
  # Every live island type: the beacon, a Live Extension that is one action
  # (calendar) and one with buttons (a call), and the now-playing island.
  for live in calendar call-app; do
    one islands-$theme-$live-100 $theme bottom 1 B_EVAL=$I B_LIVE=$live B_CHROME=1 B_TIMEOUT=240
    one islands-$theme-$live-125 $theme bottom 1 B_EVAL=$I B_LIVE=$live B_CHROME=1 W_SCALE=1.25 B_MONITORS=1920x1200 B_TIMEOUT=280
  done
done
touch $E/DONE
