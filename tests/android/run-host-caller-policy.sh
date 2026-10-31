#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
javac --release 8 -d "$work" \
  "$root/patches/android-lineage-sdk/java/HostCallerPolicy.java" \
  "$root/tests/android/HostCallerPolicyTest.java"
java -cp "$work" org.lineageos.platform.internal.HostCallerPolicyTest
