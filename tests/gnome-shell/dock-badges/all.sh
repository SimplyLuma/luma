#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Evidence runs inside the oracle container. $1: output root.
R=${1:-/oracle/out/ev}
K=/oracle/keyfile
sed 's/^\[org\/gnome\/desktop\/interface\]$/[org\/gnome\/desktop\/interface]\nscaling-factor=uint32 2/' $K > /oracle/t/keyfile.s2
run() { name=$1; shift; env "$@" ORACLE_OUT=$R/$name ORACLE_EVAL=/oracle/t/evidence.js bash /oracle/t/run.sh | sed "s/^/$name /"; }
run behaviour MODE=behaviour
run matrix-100 MODE=matrix
run matrix-200 MODE=matrix ORACLE_KEYFILE=/oracle/t/keyfile.s2 ORACLE_W=2880 ORACLE_H=1800
run edges MODE=edges
run interact MODE=interact
