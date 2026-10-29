#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# No actual user applications, documents, accounts or package registries.
set -euo pipefail
ari_source=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
ari_actions_scratch=$(mktemp -d /tmp/ari-actions-native.XXXXXX)
trap 'rm -rf -- "$ari_actions_scratch"' EXIT
export ARI_REAL_HOME="$HOME"
export HOME="$ari_actions_scratch/home"
export XDG_CONFIG_HOME="$ari_actions_scratch/config" XDG_DATA_HOME="$ari_actions_scratch/data"
export XDG_CACHE_HOME="$ari_actions_scratch/cache" XDG_RUNTIME_DIR="$ari_actions_scratch/runtime"
mkdir -m 700 "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_RUNTIME_DIR"
dbus-run-session -- "${ARI_TEST_PYTHON:-python3}" "$ari_source/tests/luma-ari/native_actions.py"
