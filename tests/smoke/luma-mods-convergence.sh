#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

desktop_packages="$repo_root/config/desktop/packages.txt"
mod_packages="$repo_root/config/shared/mod-packages.txt"
mobile_compose="$repo_root/scripts/mobile/compose-fp6-rootfs.sh"
desktop_compose="$repo_root/scripts/vm/compose-desktop-image.sh"
system_bus="$repo_root/src/luma-mods/data/org.projectluma.ModTransactions1.xml"
system_unit="$repo_root/src/luma-mods/data/luma-mod-transactions.service"

grep -Fqx 'luma-mods' "$mod_packages"
grep -Fqx "$LUMA_MODS_NEVRA" "$desktop_packages"
grep -Fq 'config/shared/mod-packages.txt' "$mobile_compose"
grep -Fq 'build/packages/luma-mods/RPMS/noarch/$LUMA_MODS_NEVRA.rpm' "$mobile_compose"
grep -Fq 'build/packages/luma-mods/RPMS/noarch/$LUMA_MODS_NEVRA.rpm' "$desktop_compose"
grep -Fq '<method name="StageCatalog">' "$system_bus"
! grep -Fq 'request_json' "$system_bus"
grep -Fq 'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6' "$system_unit"

PYTHONPYCACHEPREFIX=/tmp/luma-mods-smoke-pycache \
  python3 "$repo_root/tests/unit/test_luma_mods.py" -q

review_dir=$(mktemp -d)
trap 'rm -rf "$review_dir"' EXIT
PYTHONPYCACHEPREFIX=/tmp/luma-mods-smoke-pycache \
  PYTHONPATH="$repo_root/src/luma-mods" \
  python3 -m luma_mods.author image-plan \
  org.projectluma.pilot.hardware.oneplus-cph2653 \
  --catalog "$repo_root/examples/mods/pilots" \
  --host "$repo_root/examples/mods/pilots/host-oneplus-cph2653-image.json" \
  --output "$review_dir/oneplus.lock.json" >/dev/null
python3 - "$review_dir/oneplus.lock.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["schema"] == "org.luma.mod-image-composition-lock/v0.1"
assert value["base"]["install_mode"] == "image-compose"
assert value["base"]["architecture"] == "aarch64"
assert value["authorization"]["install_authorized"] is False
assert value["mods"][0]["id"] == "org.projectluma.pilot.hardware.oneplus-cph2653"
PY

printf 'Luma Mods desktop/mobile convergence smoke test: PASS\n'
