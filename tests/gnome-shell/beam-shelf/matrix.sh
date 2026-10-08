#!/bin/bash
# Beam / shelf surface evidence matrix. Writes /root/luma-shell-oracle/beam/evidence/<run>/{log,measure.json,*.png}
H=/root/luma-shell-oracle/beam/harness; E=/root/luma-shell-oracle/beam/evidence; mkdir -p $E
one() { # tag theme edge scale env...
  local tag=$1; shift
  timeout 400 bash $H/go.sh "$tag" "$@" > $E/$tag.log 2>&1
  mkdir -p $E/$tag; cp /root/luma-shell-oracle/beam/out-$tag/*.png /root/luma-shell-oracle/beam/out-$tag/measure.json $E/$tag/ 2>/dev/null
  grep "\[beam\]\|\[dock94\]\|\[motion\]\|\[slider\]\|\[share\]" /root/luma-shell-oracle/beam/out-$tag/shell.log > $E/$tag/beam.log
  grep -n "JS ERROR\|TypeError\|ReferenceError\|SyntaxError" /root/luma-shell-oracle/beam/out-$tag/shell.log | grep -v "dbus\|portal\|Bluetooth\|NetworkManager\|polkit\|geoclue\|upower\|bolt\|malcontent\|ibus\|gdm" > $E/$tag/js-errors.log
  rm -rf /root/luma-shell-oracle/beam/out-$tag
}
E=${B_EVIDENCE:-$E}; mkdir -p $E
case "$1" in
placement)
  for edge in bottom top left right; do for sc in 1 2; do
    one place-$edge-${sc}x light $edge $sc B_SNI=8 B_LIVE=calendar B_TIMEOUT=260 &
    [ $sc = 2 ] && wait
  done; done; wait
  one place-bottom-1x-span light bottom 1 B_SNI=8 B_LIVE=calendar B_SPAN=true B_TIMEOUT=260 &
  one place-right-2x-span light right 2 B_SNI=8 B_LIVE=calendar B_SPAN=true B_TIMEOUT=260 &
  wait
  [ -n "${B_SKIP_BEFORE:-}" ] || one before-bottom-1x light bottom 1 B_SNI=8 B_OVERLAY=/oracle/live-island/overlay-90 B_ONLY=quick-options,clock,tooltip,dock-menu,preview,well,beam B_TIMEOUT=240 &
  [ -n "${B_SKIP_BEFORE:-}" ] || one before-bottom-2x light bottom 2 B_SNI=8 B_OVERLAY=/oracle/live-island/overlay-90 B_ONLY=quick-options,clock,tooltip,dock-menu,preview,well,beam B_TIMEOUT=240 &
  wait ;;
beam)
  for th in light dark frost glass; do
    one beam-$th-bottom-1x $th bottom 1 B_ONLY=beam,second-monitor,fullscreen,lock B_MONITORS=2560x1440,1920x1080 "B_LEVELS=volume:0.48,muted:0,volume:0.83,volume:1.25:1.5,brightness:0.72,caps:none" &
    one beam-$th-bottom-2x $th bottom 2 B_ONLY=beam "B_LEVELS=volume:0.48,brightness:0.72" &
    wait
  done
  one motion-animated dark bottom 1 B_ONLY=motion B_MOTION=1 &
  one motion-reduced dark bottom 1 B_ONLY=motion B_ANIM=false &
  wait ;;
esac
touch $E/DONE-$1
