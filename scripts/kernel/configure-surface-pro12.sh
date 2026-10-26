#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Configure a prepared upstream Surface tree. No host packages are installed.
set -euo pipefail
repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
source_dir=$(realpath "${1:?usage: configure-surface-pro12.sh SOURCE OUTPUT FEDORA_CONFIG}")
output_dir=$(realpath -m "${2:?}")
base_config=$(realpath "${3:?}")
lock="$repo_root/config/kernel/surface-pro12-7.3.json"
python3 - "$lock" "$base_config" <<'PY'
import hashlib,json,sys
lock=json.load(open(sys.argv[1]))
with open(sys.argv[2],'rb') as stream:
    actual=hashlib.file_digest(stream,'sha256').hexdigest()
if actual != lock['base_config']['sha256']:
    raise SystemExit('Fedora kernel configuration differs from the source lock')
PY
mkdir -p "$output_dir"
export ARCH=arm64
if [ -z "${CROSS_COMPILE+x}" ]; then
  CROSS_COMPILE=
  [ "$(uname -m)" = aarch64 ] || CROSS_COMPILE=aarch64-linux-gnu-
fi
export CROSS_COMPILE
export KBUILD_BUILD_USER=luma KBUILD_BUILD_HOST=builder
export KBUILD_BUILD_TIMESTAMP='2026-10-22 00:00:00 UTC'
"$source_dir/scripts/kconfig/merge_config.sh" -m -O "$output_dir" \
  "$base_config" "$repo_root/config/kernel/surface-pro12.config"
make -C "$source_dir" O="$output_dir" olddefconfig
# merge_config.sh warns instead of failing when dependencies drop a symbol.
python3 - "$output_dir/.config" "$lock" <<'PY'
import json,pathlib,sys
text=pathlib.Path(sys.argv[1]).read_text()
required={'CONFIG_ARM64_4K_PAGES':'y','CONFIG_ARCH_QCOM':'y',
          'CONFIG_SECURITY_SELINUX':'y','CONFIG_BPF_LSM':'y',
          'CONFIG_DEBUG_INFO_BTF':'y','CONFIG_MODULE_SIG':'y','CONFIG_MODULE_SIG_ALL':'y',
          'CONFIG_EFI_STUB':'y','CONFIG_SURFACE_AGGREGATOR':'m',
          'CONFIG_RTC_DRV_SURFACE':'m','CONFIG_PHY_QCOM_MIPI_CSI2':'m',
          'CONFIG_VIDEO_QCOM_CAMSS':'m','CONFIG_VIDEO_OV13858':'m',
          'CONFIG_VIDEO_OV02C10':'m','CONFIG_ATH12K':'m'}
missing=[f'{k}={v}' for k,v in required.items() if f'{k}={v}\n' not in text]
if missing: raise SystemExit('Required configuration lost: '+', '.join(missing))
PY
release=$(make -s -C "$source_dir" O="$output_dir" kernelrelease)
expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["release"])' "$lock")
[ "$release" = "$expected" ] || { printf 'kernel release differs: %s != %s\n' "$release" "$expected" >&2; exit 1; }
printf 'Configured %s\n' "$release"
