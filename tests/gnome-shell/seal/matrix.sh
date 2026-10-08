#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Seal evidence matrix on the build server, against the installed RPMs.
# matrix.sh RUN: writes /root/luma-seal/evidence/RUN/<tag>/{shell.log,results.json,*.png}
RUN=${1:?run name}; H=/root/luma-seal/harness; E=/root/luma-seal/evidence/$RUN
mkdir -p $E
one() { # tag theme env...
  local tag=$1; shift
  timeout 900 bash $H/go.sh "$RUN-$tag" "$@" > $E/$tag.go.log 2>&1
  mkdir -p $E/$tag
  local O=/root/luma-seal/out/$RUN-$tag
  cp $O/*.png $O/results.json $E/$tag/ 2>/dev/null
  cp $O/shell.log $E/$tag/shell.log
  rm -rf $O
}
ALL=usb,wrong,fingerprint,miss,pkexec,race,escape,keyboard,unknown,noreader
one light-1x light SEAL_ONLY=$ALL SEAL_TIMEOUT=840
one dark-1x dark SEAL_ONLY=$ALL SEAL_TIMEOUT=840
one light-2x light SEAL_ONLY=usb,fingerprint,miss,pkexec,unknown SEAL_SCALE=2 SEAL_MONITOR=2880x1800 SEAL_TIMEOUT=600
one dark-2x dark SEAL_ONLY=usb,fingerprint,miss,pkexec,unknown SEAL_SCALE=2 SEAL_MONITOR=2880x1800 SEAL_TIMEOUT=600
one reduced-light light SEAL_ONLY=reduced SEAL_ANIM=false SEAL_TIMEOUT=200
touch $E/DONE
