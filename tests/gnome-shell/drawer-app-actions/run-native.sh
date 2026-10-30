#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
set -euo pipefail
export LUMA_POLISH_SCENARIO=drawer-app-actions
exec bash "$(dirname "$0")/../status-polish/run-native.sh"
