#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
exec "$(dirname -- "$0")/build-luma-display-config.sh" "$@"
