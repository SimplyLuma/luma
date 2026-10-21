#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
set -euo pipefail
export LUMA_POLISH_SCENARIO=applications-drawer
exec bash "$(dirname "$0")/../status-polish/run-native.sh"
