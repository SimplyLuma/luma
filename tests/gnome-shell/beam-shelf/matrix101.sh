#!/bin/bash
# Window motion evidence. B_OVERLAY_DEFAULT (overlay or none), B_EVIDENCE.
H=/root/luma-shell-oracle/beam/harness
source <(sed -n "/^one()/,/^}/p" $H/matrix.sh)
E=${B_EVIDENCE:?}; mkdir -p $E
export B_CONTAINER=${B_CONTAINER:-luma-beam-sound}
M=/oracle/beam/harness/motion.js
one mo-dark-bottom-1x-strips dark bottom 1 B_EVAL=$M B_STRIP=1 B_TIMEOUT=260
one mo-light-left-1x-strips light left 1 B_EVAL=$M B_STRIP=1 B_TIMEOUT=260
one mo-dark-bottom-5120 dark bottom 1 B_EVAL=$M B_MONITORS=5120x1440 B_TIMEOUT=220
one mo-light-bottom-2x light bottom 2 B_EVAL=$M B_MONITORS=2880x1800 B_TIMEOUT=220
one mo-dark-bottom-reduced dark bottom 1 B_EVAL=$M B_ANIM=false B_TIMEOUT=220
for d in $E/mo-*; do [ -d $d ] || continue; grep "\[motion\]" /dev/null; done
touch $E/DONE
