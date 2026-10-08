#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only eUICC probe for the physical FP6. It emits capability booleans and
# counts only; EID, ICCID, AID, provider, profile name, icon and addresses are
# neither printed nor persisted.

set -euo pipefail
umask 077

slot=${LUMA_FP6_ESIM_SLOT:-2}
case "$slot" in 1|2) ;; *) printf 'error: slot must be 1 or 2\n' >&2; exit 2 ;; esac

for command in lpac python3 timeout; do
  command -v "$command" >/dev/null || {
    printf 'error: missing command: %s\n' "$command" >&2
    exit 1
  }
done

scratch=$(mktemp -d /run/luma-esim-probe.XXXXXX)
cleanup() {
  rm -f "$scratch/chip.json" "$scratch/profiles.json"
  rmdir "$scratch" 2>/dev/null || true
}
trap cleanup EXIT INT TERM HUP

run_lpac() {
  LPAC_APDU=qmi_qrtr \
  LPAC_APDU_QMI_UIM_SLOT="$slot" \
  LPAC_HTTP=curl \
  LPAC_APDU_DEBUG=false \
  LPAC_HTTP_DEBUG=false \
    timeout --signal=TERM --kill-after=5 45 lpac "$@"
}

run_lpac chip info >"$scratch/chip.json"
run_lpac profile list >"$scratch/profiles.json"

python3 - "$scratch/chip.json" "$scratch/profiles.json" <<'PY'
import json
import sys

def payload(path):
    with open(path, "r", encoding="utf-8") as stream:
        value = json.load(stream)
    result = value.get("payload", {})
    if result.get("code") != 0:
        raise SystemExit("error: lpac returned a non-success payload")
    return result.get("data")

chip = payload(sys.argv[1])
profiles = payload(sys.argv[2])
if not isinstance(chip, dict) or not isinstance(profiles, list):
    raise SystemExit("error: unexpected lpac response shape")

info = chip.get("EUICCInfo2") or {}
print("EUICC_PRESENT=true")
print(f"PROFILE_COUNT={len(profiles)}")
print(f"ENABLED_PROFILE_COUNT={sum(p.get('profileState') == 'enabled' for p in profiles if isinstance(p, dict))}")
print(f"DISABLED_PROFILE_COUNT={sum(p.get('profileState') == 'disabled' for p in profiles if isinstance(p, dict))}")
print(f"ADDITIONAL_PROFILE_CAPABLE={str('additionalProfile' in (info.get('rspCapability') or [])).lower()}")
print(f"TEST_PROFILE_CAPABLE={str('testProfileSupport' in (info.get('rspCapability') or [])).lower()}")
print("IDENTIFIERS_REDACTED=true")
print("MUTATION_PERFORMED=false")
PY

