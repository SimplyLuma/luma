#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# matrix.sh CONTAINER: Light, Frost and Glass with the backdrop cache on, and
# Frost and Glass with it off (the previous behaviour), then the comparison.
C=${1:?container with the built RPMs and /oracle mounted}
for run in light:1 frost:1 glass:1 frost:0 glass:0; do
  theme=${run%:*}; cache=${run#*:}
  podman exec -e M_TAG=$theme-cache$cache -e M_THEME=$theme -e M_CACHE=$cache \
    ${M_EXTRA_ENV:+-e "$M_EXTRA_ENV"} "$C" bash /oracle/blur-motion/harness/run.sh
done
python3 "$(dirname "$0")/compare.py" "${M_OUT_ROOT:-/root/luma-shell-oracle/blur-motion}" "$@"
