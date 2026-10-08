#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Run the audited one-TA identity gate with Qualcomm's signed smplap64 sample
# application instead of focal64. No application command is sent. This is an
# image-format and transport control, not a biometric operation.

set -Eeuo pipefail
umask 077

base=/tmp/luma-fingerprint-identity-v9-base.sh
inner=/tmp/luma-qsee-signed-control-inner.sh
expected_base_sha=1a1f7c881a2e1190b5dcc5bac187e35f13861a7bd59346c32d1e5eee5519635b
expected_inner_sha=6a1e69b52b1172041deb1d9af7e90eb26cc7f9c1e7080444f0f2925ebd9c5b2e

fail() {
  printf 'event=qsee_signed_control_wrapper_failed reason=%s\n' "$1" >&2
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
  -e 's#/tmp/luma-fingerprint-identity-v9#/tmp/luma-qsee-signed-control#g' \
  -e 's#bc12893e0403245e2ea4a1cb6a4801c1efdba515d6f726be18f2e55f1752ab33#d4930b0c74a61402b3e6360ef81f9ce0c1eaf72501085eeb9be4ce046c7b30d9#g' \
  -e 's#/reserved-memory/qseecom-ta-pool#/reserved-memory/qseecom_ta_region#g' \
  -e 's#/reserved-memory/qseecom-apps-pool#/reserved-memory/qseecom_region#g' \
  -e 's#104792353c21f86efd7eb9a5773acf033431bc7293b18062e817e898b2973df9#38dbf4154218d45f8d5e487f518b222400acbbd4ad71b849405af58413f46f70#g' \
  -e 's/focal64/smplap64/g' \
  -e 's/identity_v9/qsee_signed_control/g' \
  "$base" >"$inner"
chmod 0700 "$inner"
[[ $(sha256sum "$inner" | cut -d' ' -f1) == "$expected_inner_sha" ]] || fail inner_hash

"$inner"
