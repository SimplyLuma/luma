#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [ "$#" -ne 2 ]; then
  printf 'usage: %s BUILD_REPORT KEY\n' "$0" >&2
  exit 2
fi

build_report=$1
key=$2
[ -f "$build_report" ] || {
  printf 'error: build report is missing: %s\n' "$build_report" >&2
  exit 1
}
[[ "$key" =~ ^[A-Za-z0-9_]+$ ]] || {
  printf 'error: invalid build-report key: %s\n' "$key" >&2
  exit 1
}

# Older accepted reports can contain the same provenance record twice because
# the Stage wrapper repeated a value already emitted by the canonical report
# writer. Treat identical repetitions as one assertion, but never choose
# between conflicting values.
awk -F= -v key="$key" '
  $1 == key {
    sub(/^[^=]*=/, "")
    if ($0 != "" && !seen[$0]++) {
      values[++count] = $0
    }
  }
  END {
    if (count == 0) {
      printf "error: build report has no non-empty %s record\n", key > "/dev/stderr"
      exit 1
    }
    if (count != 1) {
      printf "error: build report has conflicting %s records\n", key > "/dev/stderr"
      exit 1
    }
    print values[1]
  }
' "$build_report"
