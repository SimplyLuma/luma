#!/bin/bash
# Shell 0136 evidence: preview close/resize frames, menus light/dark 1x/2x, Open at Login from the menu,
# and the shelf placement regression for dock menus. Pass B_OVERLAY_DEFAULT (overlay or none) and B_EVIDENCE.
H=/root/luma-shell-oracle/beam/harness
source <(sed -n "/^one()/,/^}/p" $H/matrix.sh)
E=${B_EVIDENCE:?}; mkdir -p $E
export B_CONTAINER=${B_CONTAINER:-luma-beam-sound}
one d94-preview-dark-1x-motion dark bottom 1 B_EVAL=/oracle/beam/harness/dock94.js B_MOTION=1 B_ONLY=preview B_TIMEOUT=220 &
one d94-preview-light-2x-motion light bottom 2 B_EVAL=/oracle/beam/harness/dock94.js B_MOTION=1 B_ONLY=preview B_TIMEOUT=240 &
wait
one d94-preview-dark-1x-reduced dark bottom 1 B_EVAL=/oracle/beam/harness/dock94.js B_ANIM=false B_ONLY=preview B_TIMEOUT=220 &
one d94-menu-light-1x light bottom 1 B_EVAL=/oracle/beam/harness/dock94.js B_ONLY=menu B_TOGGLE=1 B_TIMEOUT=220 &
wait
one d94-menu-dark-1x dark bottom 1 B_EVAL=/oracle/beam/harness/dock94.js B_ONLY=menu B_TOGGLE=1 B_TIMEOUT=220 &
one d94-menu-light-2x light bottom 2 B_EVAL=/oracle/beam/harness/dock94.js B_ONLY=menu B_TIMEOUT=240 &
wait
one d94-menu-dark-2x dark bottom 2 B_EVAL=/oracle/beam/harness/dock94.js B_ONLY=menu B_TIMEOUT=240 &
one d94-place-right-2x light right 2 B_SNI=8 B_ONLY=tooltip,dock-menu,preview,well B_TIMEOUT=260 &
wait
one d94-place-bottom-1x dark bottom 1 B_SNI=8 B_ONLY=tooltip,dock-menu,preview,well B_TIMEOUT=260 &
one d94-paints-dark-1x dark bottom 1 B_EVAL=/oracle/beam/harness/dock94.js B_MOTION=1 B_ONLY=preview B_PAINTS=1 B_TIMEOUT=200 &
wait
one d94-paints-light-2x light bottom 2 B_EVAL=/oracle/beam/harness/dock94.js B_MOTION=1 B_ONLY=preview B_PAINTS=1 B_TIMEOUT=220 &
one d94-preview-left-1x-motion light left 1 B_EVAL=/oracle/beam/harness/dock94.js B_MOTION=1 B_ONLY=preview B_TIMEOUT=220 &
wait
touch $E/DONE
