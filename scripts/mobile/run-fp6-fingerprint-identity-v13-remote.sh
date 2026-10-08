#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Constrain the already audited identity-v9 runner to the DT-only v13 boot and
# its exact stock reserved-memory paths. The generated inner script changes no
# load behavior; it only updates the boot identity, stage identity, event label,
# and two device-tree paths before executing the same one-TA/no-biometric gate.

set -Eeuo pipefail
umask 077

base=/tmp/luma-fingerprint-identity-v9-base.sh
inner=/tmp/luma-fingerprint-identity-v13-inner.sh
expected_base_sha=1a1f7c881a2e1190b5dcc5bac187e35f13861a7bd59346c32d1e5eee5519635b
expected_inner_sha=907660396637ab359a62a8dc7fc5eaac09c0137f6542c7a75eb5456023d31612

fail() {
  printf 'event=identity_v13_wrapper_failed reason=%s\n' "$1" >&2
  exit 1
}

cleanup() {
  local status=$?
  rm -f -- "$inner"
  exit "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ -f $base && ! -L $base ]] || fail base_identity
[[ $(sha256sum "$base" | cut -d' ' -f1) == "$expected_base_sha" ]] || fail base_hash
[[ ! -e $inner ]] || fail inner_preexists

sed \
  -e 's#/tmp/luma-fingerprint-identity-v9#/tmp/luma-fingerprint-identity-v13#g' \
  -e 's#bc12893e0403245e2ea4a1cb6a4801c1efdba515d6f726be18f2e55f1752ab33#d4930b0c74a61402b3e6360ef81f9ce0c1eaf72501085eeb9be4ce046c7b30d9#g' \
  -e 's#/reserved-memory/qseecom-ta-pool#/reserved-memory/qseecom_ta_region#g' \
  -e 's#/reserved-memory/qseecom-apps-pool#/reserved-memory/qseecom_region#g' \
  -e 's/identity_v9/identity_v13/g' \
  "$base" >"$inner"
chmod 0700 "$inner"
[[ $(sha256sum "$inner" | cut -d' ' -f1) == "$expected_inner_sha" ]] || fail inner_hash

"$inner"
