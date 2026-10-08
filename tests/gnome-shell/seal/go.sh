#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Seal evidence harness, on the build server: one headless Shell run in the
# systemd test container luma-seal-it. go.sh TAG THEME [K=V ...]
TAG=$1; THEME=$2; shift 2
C=${SEAL_CONTAINER:-luma-seal-it}
props=(-p PAMName=seal-session -p User=nick -p "Environment=SEAL_TAG=$TAG" -p "Environment=SEAL_THEME=$THEME")
for kv in "$@"; do props+=(-p "Environment=$kv"); done
podman exec $C systemctl is-active --quiet seal-mechanism || podman exec $C systemd-run --unit=seal-mechanism python3 /seal/harness/mechanism.py
podman exec $C systemd-run --wait --collect --unit="seal-shell-$TAG" "${props[@]}" /bin/bash /seal/harness/session.sh
D=/root/luma-seal/out/$TAG
grep -n "JS ERROR\|TypeError\|ReferenceError\|SyntaxError\|Gjs-CRITICAL\|JS WARNING\|harness error" $D/shell.log | head -20
grep "\[seal\]" $D/shell.log | cut -c1-400
