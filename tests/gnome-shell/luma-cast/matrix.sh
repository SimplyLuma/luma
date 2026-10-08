#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Full Cast UI matrix: dark/light x 1x/2x. Output under /mnt/luma-secondary/luma-cast-ui/shots/m-<theme>-<scale>-<group>
G1=tile,searching,devices,focus,long,empty
G2=sheet,casting,connecting,reconnecting,error
G3=picker,code-enter,code-show,code-confirm,curtain,banner
for theme in dark light; do
  for scale in 1 2; do
    for g in 1 2 3; do
      eval cases=\$G$g
      bash /root/luma-shell-oracle/cast-ui/go.sh m-$theme-$scale-$g $theme $cases $([ $scale = 2 ] && echo 2880x1800 || echo 2560x1440) new $scale 2>&1 | grep -v "disposed\|sweeping\|not in the stage\|\[cu\] shot" | tail -6
    done
  done
  bash /root/luma-shell-oracle/cast-ui/go.sh m-$theme-1-multi $theme displays 2560x1440,1920x1080 new 1 2>&1 | grep -v "disposed\|sweeping\|not in the stage\|\[cu\] shot" | tail -4
done
echo MATRIX-DONE
