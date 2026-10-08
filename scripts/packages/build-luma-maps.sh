#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the luma-maps RPM; see build-luma-python-app.sh.
exec "$(dirname -- "$0")/build-luma-python-app.sh" luma-maps "$@"
