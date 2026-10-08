#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the luma-monitor RPM; see build-luma-python-app.sh.
exec "$(dirname -- "$0")/build-luma-python-app.sh" luma-monitor "$@"
