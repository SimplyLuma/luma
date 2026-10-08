#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build only the three FP6 speaker-path modules against Luma's exact running
# 7.1.2 configuration and symbol table. The default policy refuses to compile
# on the physical phone; the physical lab may opt in explicitly for this
# bounded, single-job module-only build after stopping the graphical session.
# This script never installs or loads a module and never accesses a partition.

set -euo pipefail
umask 022

inputs=${1:?usage: build-fp6-audio-modules.sh INPUT_DIR WORK_DIR}
work=${2:?usage: build-fp6-audio-modules.sh INPUT_DIR WORK_DIR}
jobs=${LUMA_KERNEL_BUILD_JOBS:-1}
allow_phone=${LUMA_ALLOW_FP6_BOUNDED_AUDIO_BUILD:-false}

kernel_release=7.1.2
running_config_sha=9bf4fd8e395147f5eb696d2887e7c40d5a5e124a4d27584babeec830ed5d3fb2
module_symvers_sha=0136080d85bcb215aad56324ed61259c1581400d0159489c664b75b476de04e0

case "$jobs" in
  ''|*[!0-9]*) printf 'error: invalid job count: %s\n' "$jobs" >&2; exit 1 ;;
esac
[ "$jobs" -eq 1 ] || {
  printf 'error: the bounded audio build requires exactly one job\n' >&2
  exit 1
}
case "$allow_phone" in true|false) ;; *)
  printf 'error: LUMA_ALLOW_FP6_BOUNDED_AUDIO_BUILD must be true or false\n' >&2
  exit 1
esac
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || {
  printf 'error: this build requires Linux/aarch64\n' >&2
  exit 1
}
[ ! -e "$work" ] || {
  printf 'error: work directory already exists: %s\n' "$work" >&2
  exit 1
}

model=unknown
if [ -r /sys/firmware/devicetree/base/model ]; then
  model=$(tr -d '\000' </sys/firmware/devicetree/base/model)
fi
if [ "$model" = 'The Fairphone (Gen. 6)' ] && [ "$allow_phone" != true ]; then
  printf 'error: refusing a phone-side build without the explicit bounded opt-in\n' >&2
  exit 1
fi

for tool in awk clang git gzip install ld.lld llvm-objcopy make modinfo \
  mktemp readelf sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: missing build tool: %s\n' "$tool" >&2
    exit 1
  }
done

check_file() {
  local name=$1 expected=$2 actual
  [ -f "$inputs/$name" ] || {
    printf 'error: missing input: %s\n' "$name" >&2
    exit 1
  }
  actual=$(sha256sum "$inputs/$name" | awk '{print $1}')
  [ "$actual" = "$expected" ] || {
    printf 'error: checksum differs for %s: %s\n' "$name" "$actual" >&2
    exit 1
  }
}

check_file linux-v7.1.2-milos.tar.gz \
  6043a062d595e1b4913512c3414387d9a016c4690d8832720651df07276dc78b
check_file Module.symvers "$module_symvers_sha"
check_file 0026-ASoC-qcom-sc8280xp-support-Senary-MI2S.patch \
  6a28abddc3e34a05cb65de70f186facd84ab2c0823cbb7a208aa440e26180df8
check_file 0027-ASoC-codecs-aw88261-backport-mainline-format-negotiation-power-up-fix.patch \
  d08280d0b5e090caee8dc73631be3527c39625298a96c5fd6958d2abe24ee01d
check_file 0028-ASoC-qcom-q6apm-lpass-dais-start-graph-at-prepare.patch \
  d90c97cf73261656bbd77c81cdf95d3b08edb09bbba5fe1e1d9dfa75bb1af5c0

mkdir -p "$work"
work=$(CDPATH= cd -- "$work" && pwd)
tar -xzf "$inputs/linux-v7.1.2-milos.tar.gz" -C "$work"
source_tree=$work/linux

for patch in \
  0026-ASoC-qcom-sc8280xp-support-Senary-MI2S.patch \
  0027-ASoC-codecs-aw88261-backport-mainline-format-negotiation-power-up-fix.patch \
  0028-ASoC-qcom-q6apm-lpass-dais-start-graph-at-prepare.patch; do
  git -C "$source_tree" apply "$inputs/$patch"
done

(
  cd "$source_tree"
  gzip -dc /proc/config.gz >.config
  [ "$(sha256sum .config | awk '{print $1}')" = "$running_config_sha" ]
  make LLVM=1 LOCALVERSION= olddefconfig
  [ "$(make LLVM=1 LOCALVERSION= -s kernelrelease)" = "$kernel_release" ]
  grep -qx 'CONFIG_SND_SOC_SC8280XP=m' .config
  grep -qx 'CONFIG_SND_SOC_AW88261=m' .config
  grep -qx 'CONFIG_SND_SOC_QDSP6_APM_LPASS_DAI=m' .config
  make -j1 LLVM=1 LOCALVERSION= modules_prepare
  install -m 0644 "$inputs/Module.symvers" Module.symvers
  make -j1 LLVM=1 LOCALVERSION= M=sound/soc/codecs snd-soc-aw88261.ko
  make -j1 LLVM=1 LOCALVERSION= M=sound/soc/qcom/qdsp6 q6apm-lpass-dais.ko
  make -j1 LLVM=1 LOCALVERSION= M=sound/soc/qcom snd-soc-sc8280xp.ko
)

bundle=$work/bundle
mkdir -p "$bundle"
install -m 0644 "$source_tree/sound/soc/codecs/snd-soc-aw88261.ko" "$bundle/"
install -m 0644 "$source_tree/sound/soc/qcom/qdsp6/q6apm-lpass-dais.ko" "$bundle/"
install -m 0644 "$source_tree/sound/soc/qcom/snd-soc-sc8280xp.ko" "$bundle/"
install -m 0644 "$source_tree/.config" "$bundle/config"

for module in snd-soc-aw88261 q6apm-lpass-dais snd-soc-sc8280xp; do
  [ "$(modinfo -F vermagic "$bundle/$module.ko" | awk '{print $1}')" = \
    "$kernel_release" ]
  if readelf -SW "$bundle/$module.ko" | grep -q '[.]BTF'; then
    llvm-objcopy --remove-section=.BTF "$bundle/$module.ko"
  fi
done

{
  printf 'LUMA_FP6_AUDIO_MODULE_BUILD_VERSION=1\n'
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'RUNNING_CONFIG_SHA256=%s\n' "$running_config_sha"
  printf 'MODULE_SYMVERS_SHA256=%s\n' "$module_symvers_sha"
  printf 'BUILD_DEVICE_MODEL=%s\n' "$model"
  printf 'BUILD_JOBS=1\n'
  printf 'MODULES_INSTALLED=false\n'
  printf 'MODULES_LOADED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
  for artifact in snd-soc-aw88261.ko q6apm-lpass-dais.ko \
    snd-soc-sc8280xp.ko config; do
    key=$(printf '%s' "$artifact" | tr '[:lower:].-' '[:upper:]__')
    printf '%s_SHA256=%s\n' "$key" \
      "$(sha256sum "$bundle/$artifact" | awk '{print $1}')"
  done
} >"$bundle/manifest.env"
chmod 0644 "$bundle/manifest.env"

printf 'FP6 bounded audio module bundle: %s\n' "$bundle"
