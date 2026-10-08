#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

fail() {
  printf 'Native core independence smoke test: FAIL: %s\n' "$1" >&2
  exit 1
}

core_specs=(
  "$repo_root/packaging/rpm/prairie-core-apps.spec"
  "$repo_root/packaging/rpm/ModemManager-fp6.spec"
  "$repo_root/packaging/rpm/libqmi-fp6.spec"
  "$repo_root/packaging/rpm/lpac-fp6.spec"
  "$repo_root/packaging/rpm/luma-imsd.spec"
)

if rg -ni '^[[:space:]]*(Requires|Recommends|Suggests):.*(luma-android-runtime|waydroid)' \
  "${core_specs[@]}"; then
  fail 'a native core RPM depends on the Android application runtime'
fi

if rg -ni '(luma_android|luma-android|waydroid|android\.telephony|org\.codeaurora\.ims)' \
  "$repo_root/src/prairie-core/prairie_apps/phone_backend.py" \
  "$repo_root/src/prairie-core/prairie_apps/messages_backend.py"; then
  fail 'Prairie Phone or Messages invokes Android userspace'
fi

core_units=(
  "$repo_root/scripts/mobile/luma-fp6-imsd.service"
  "$repo_root/config/mobile/fp6-physical/overlay/etc/systemd/system/luma-fp6-cellular-link.service"
  "$repo_root/src/prairie-core/data/prairie-messages-daemon.service"
)

if rg -ni '^(Requires|Wants|After|Before)=.*(android|waydroid)' "${core_units[@]}"; then
  fail 'a native core service has an Android ordering or requirement edge'
fi

release_manifests=(
  "$repo_root/config/mobile/packages.txt"
  "$repo_root/config/mobile/ui-packages.txt"
  "$repo_root/config/mobile/phone-capability-packages.txt"
  "$repo_root/config/shared/platform-packages.txt"
  "$repo_root/config/shared/application-packages.txt"
)

if rg -ni '^[^#]*(qcrilnrd|org\.codeaurora\.ims|teleservice\.apk|telecom\.apk|stock-android)' \
  "${release_manifests[@]}"; then
  fail 'a release package manifest contains stock Android telephony'
fi

if rg -ni '(run|prepare)-fp6-stock-android|qcrilnrd|org\.codeaurora\.ims' \
  "$repo_root/scripts/mobile/compose-fp6-rootfs.sh" \
  "$repo_root/scripts/mobile/prepare-fp6-p5-candidate.sh"; then
  fail 'a canonical FP6 composer invokes the stock Android oracle'
fi

grep -Fq 'ExecStart=/usr/sbin/imsd' \
  "$repo_root/scripts/mobile/luma-fp6-imsd.service" ||
  fail 'the FP6 call service is not the native imsd service'
grep -Fq 'Wants=ModemManager.service' \
  "$repo_root/scripts/mobile/luma-fp6-imsd.service" ||
  fail 'the native IMS service does not declare its Linux modem owner'

[ -f "$repo_root/src/imsd/LUMA-PROVENANCE.md" ] ||
  fail 'canonical native IMS source is absent'
grep -Fq 'Imported commit: `9267c02c9f1eb501baef41b6c1e5fe4391606f72`' \
  "$repo_root/src/imsd/LUMA-PROVENANCE.md" ||
  fail 'canonical native IMS provenance is not pinned'
grep -Fq 'build/packages/luma-imsd/aarch64/RPMS/$LUMA_IMSD_AARCH64_NEVRA.rpm' \
  "$repo_root/scripts/mobile/compose-fp6-rootfs.sh" ||
  fail 'the canonical FP6 composer does not consume the native IMS RPM'
if rg -n 'build/mobile/fp6-ims/imsd-source' \
  "$repo_root/scripts" "$repo_root/packaging" "$repo_root/config"; then
  fail 'a release input still consumes the ignored IMS lab working tree'
fi

[ "$(rg -c 'if \(RegEventEnabled\(\)\) SubscribeRegEvent\(\);' \
  "$repo_root/src/imsd/implementations/main.cpp")" -eq 3 ] ||
  fail 'an IMS refresh/reconnect path can bypass the reg-event carrier gate'

printf 'Native core independence smoke test: PASS\n'
