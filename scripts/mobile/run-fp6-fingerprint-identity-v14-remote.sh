#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Constrain the audited one-TA identity-v9 runner to the physically accepted
# matched-CMA v14 kernel and full boot partition. The generated inner runner
# loads either Qualcomm's signed non-biometric control TA or focal64, sends no
# application command, and restores every ephemeral resource on exit.

set -Eeuo pipefail
umask 077

app=${1:?usage: run-fp6-fingerprint-identity-v14-remote.sh smplap64|focal64}
base=/tmp/luma-fingerprint-identity-v9-base.sh
inner=/tmp/luma-fingerprint-identity-v14-inner.sh
expected_base_sha=1a1f7c881a2e1190b5dcc5bac187e35f13861a7bd59346c32d1e5eee5519635b

case $app in
  focal64)
    bundle_sha=104792353c21f86efd7eb9a5773acf033431bc7293b18062e817e898b2973df9
    expected_inner_sha=97ecaf65fa8f282f506f94a8a4bb2af46df01072e1bddcc9eceba5865badcdb1
    ;;
  smplap64)
    bundle_sha=38dbf4154218d45f8d5e487f518b222400acbbd4ad71b849405af58413f46f70
    expected_inner_sha=698e62cac3697ef9b3f621a5a7525c4209137499c320eee659e871ccf493dd17
    ;;
  *)
    printf 'event=identity_v14_wrapper_failed reason=app_not_allowed\n' >&2
    exit 2
    ;;
esac

fail() {
  printf 'event=identity_v14_wrapper_failed reason=%s\n' "$1" >&2
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
  -e 's#/tmp/luma-fingerprint-identity-v9#/tmp/luma-fingerprint-identity-v14#g' \
  -e 's#expected_kernel=7.1.2#expected_kernel=7.1.2-luma-fp-cma1#g' \
  -e 's#expected_boot_size=27512832#expected_boot_size=100663296#g' \
  -e 's#bc12893e0403245e2ea4a1cb6a4801c1efdba515d6f726be18f2e55f1752ab33#c6acad3a30d944d59648258eef383c33a3b51ac1148a98ef24256ee4dee61dd8#g' \
  -e 's#a403b3cb247d3f2ce358f8debcc3b93d3c1949813488da8e12ac81540cee519c#5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe#g' \
  -e 's#126074584721781d038312eb2d0129cd9d6044ee1b54523999a69824d54b7f5e#1f74052412d9fc34eb581cb7aac0df80d949ca2c6da9bd6e9e70dc2fa68415df#g' \
  -e 's#030959db4f3c21ddeef76863594e7c5d184bfe484119fd9e483b662bd19cfcd5#047100c255b64b14ec1565cfc089f9bd0c5eb3a28e67b259f206bc930af39f5b#g' \
  -e 's#e9ca41da358fa6bf0be128ed5ae7c386ea0496d2aa953c04274a3ac41c549987#be860585db2c68ca80b3ee9e5e4929413c0d7207f9524ba489f974c46e26d051#g' \
  -e 's#/lib/modules/7.1.2/extra/luma-fingerprint/focaltech_fp.ko#/usr/lib/modules/7.1.2-luma-fp-cma1/kernel/drivers/input/finger/focal_finger/focaltech_fp.ko.zst#g' \
  -e 's#/lib/modules/7.1.2/extra/luma-fingerprint/qseecomtee.ko#/usr/lib/modules/7.1.2-luma-fp-cma1/kernel/drivers/tee/qseecom/qseecomtee.ko.zst#g' \
  -e "s#104792353c21f86efd7eb9a5773acf033431bc7293b18062e817e898b2973df9#$bundle_sha#g" \
  -e "s/focal64/$app/g" \
  -e 's#/reserved-memory/qseecom-ta-pool#/reserved-memory/qseecom_ta_region#g' \
  -e 's#/reserved-memory/qseecom-apps-pool#/reserved-memory/qseecom_region#g' \
  -e "s/identity_v9/identity_v14/g" \
  -e "s@grep -qx '# CONFIG_DMA_CMA is not set' <<<\"\$runtime_config\" || fail dma_cma_enabled@grep -qx 'CONFIG_DMA_CMA=y' <<<\"\$runtime_config\" || fail dma_cma_enabled@" \
  -e "/grep -qx 'CONFIG_DMA_CMA=y'/a\\grep -qx 'CONFIG_CMA_SIZE_MBYTES=32' <<<\"\$runtime_config\" || fail cma_size" \
  "$base" >"$inner"
chmod 0700 "$inner"
bash -n "$inner" || fail inner_syntax

actual_inner_sha=$(sha256sum "$inner" | cut -d' ' -f1)
if [[ ${LUMA_IDENTITY_V14_PRINT_INNER_HASH:-false} == true ]]; then
  printf '%s %s\n' "$app" "$actual_inner_sha"
  exit 0
fi
[[ $actual_inner_sha == "$expected_inner_sha" ]] || fail inner_hash

"$inner"
