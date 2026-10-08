#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Every evidence pass. $1: output root.
R=${1:-/oracle/out/ev}; T=${SHARE_HARNESS:-/oracle/t}
sed 's/^\[org\/gnome\/desktop\/interface\]$/[org\/gnome\/desktop\/interface]\nscaling-factor=uint32 2/' $T/keyfile.light > $T/keyfile.light.s2
run() { name=$1; shift; env "$@" ORACLE_OUT=$R/$name bash $T/run.sh | sed "s/^/$name /"; }
run full-light-100 MODE=full ORACLE_KEYFILE=$T/keyfile.light
run look-dark-100 MODE=look SUFFIX=-dark ORACLE_KEYFILE=$T/keyfile.dark
run look-light-200 MODE=look SUFFIX=-200 ORACLE_KEYFILE=$T/keyfile.light.s2 ORACLE_W=2880 ORACLE_H=1800
python3 $T/measure.py $R
