#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
env_file=$repo_root/config/mobile/fp6-microphone.env
builder=$repo_root/scripts/mobile/build-fp6-microphone-candidate.sh
installer=$repo_root/scripts/mobile/install-fp6-microphone-modules.sh
repacker=$repo_root/scripts/mobile/prepare-fp6-microphone-boot-candidate.sh
live_repacker=$repo_root/scripts/mobile/prepare-fp6-microphone-live-capture-candidate.sh
inspector=$repo_root/scripts/mobile/inspect-fp6-microphone-candidate.sh
relinker=$repo_root/scripts/mobile/relink-fp6-microphone-kernel-btf-fallback.sh
stimulus=$repo_root/scripts/mobile/check-fp6-microphone-stimulus.py
ledger=$repo_root/docs/research/fp6-linux-hardware-readiness.md
amic3_builder=$repo_root/scripts/mobile/build-fp6-amic3-routing-candidate.sh
amic3_repacker=$repo_root/scripts/mobile/prepare-fp6-amic3-boot-candidate.sh
amic3_stager=$repo_root/scripts/mobile/stage-fp6-amic3-module.sh
amic3_dapm_builder=$repo_root/scripts/mobile/build-fp6-amic3-dapm-route-candidate.sh
amic3_dapm_repacker=$repo_root/scripts/mobile/prepare-fp6-amic3-dapm-boot-candidate.sh
amic3_route_tester=$repo_root/scripts/mobile/test-fp6-amic3-route.sh
patch_dir=$repo_root/patches/linux-milos

bash -n "$installer" "$repacker" "$live_repacker" "$inspector" "$relinker" "$amic3_builder" "$amic3_repacker" "$amic3_stager" "$amic3_dapm_builder" "$amic3_dapm_repacker" "$amic3_route_tester"
sh -n "$builder"
PYTHONPYCACHEPREFIX=${TMPDIR:-/tmp}/luma-fp6-microphone-pycache \
  python3 -m py_compile "$stimulus"

grep -Fxq 'FP6_MICROPHONE_BASE_BOOT_SHA256=44e8e2a04d960ea33cd7bee3e52251d20ce0b70432a2368408e08978042117a7' "$env_file"
grep -Fxq 'FP6_MICROPHONE_PROVEN_KERNEL_SHA256=62556c8a87a0b8c345fdfd11770bffad7b112526e17cdb353600bec8648583ef' "$env_file"
grep -Fxq 'FP6_MICROPHONE_V1_REBUILT_KERNEL_SHA256=a955ed54f22de57f8d6060fa017a832413034037d08fe0715c40b6d18332e4c2' "$env_file"
grep -Fxq 'FP6_MICROPHONE_V2_BOOT_SHA256=756d4f96546e5db3f394e2f3764986140de1dc0caef0681e3d8bee76cd08e17a' "$env_file"
grep -Fxq 'FP6_MICROPHONE_V3_RELINKED_KERNEL_SHA256=760eb683f88ced7f869bc169ba123c52f0df6163c6cc2812b6676d812a3efb68' "$env_file"
grep -Fxq 'FP6_MICROPHONE_V3_CONFIG_SHA256=a78af97c72e7d454348aa87dbe4bdabc81ab8328e9ae807610842059ef061d09' "$env_file"
grep -Fxq 'FP6_MICROPHONE_V3_BOOT_SHA256=ee200e5a20161306a534f8f6a3b1e4d0ccd8abc80cb1ae706dddf027a96df7e9' "$env_file"
grep -Fxq 'FP6_MICROPHONE_AMIC3_V1_WCD9378_SDW_KO_SHA256=10b7aaea18a2df37c1ac6ad195db47e9f89f7c7249080e7ef30a8e1221d761d9' "$env_file"
grep -Fxq 'FP6_MICROPHONE_AMIC3_V1_DTB_SHA256=28bdea6b91939f9ae55c33e04cea3b39058d4d49494f594195dbade4396b5e78' "$env_file"
grep -Fxq 'FP6_MICROPHONE_V4_BOOT_SHA256=a4f0ec5d9d181f08269be12ce9122ba1ac33e5e98648d5e904bb5e0c9b82249b' "$env_file"
grep -Fxq 'FP6_MICROPHONE_AMIC3_V2_PATCH_SHA256=53fcddb1c91a7fcb4fdf33b9c0db7da8892bbf4597809ca3be6cd8e6d14bddd9' "$env_file"
grep -Fxq 'FP6_MICROPHONE_AMIC3_V2_DTB_SHA256=cd942c35f96d13c8ea22bcb9bab611abb8cf2a34fec9c95d2e17a56baa617c02' "$env_file"
grep -Fxq 'FP6_MICROPHONE_V5_BOOT_SHA256=77819b90457e55809708aac1bc01d4d5d063f1bc274c1a4b0f9de716bfe94e01' "$env_file"
grep -Fxq 'FP6_MICROPHONE_DTB_SHA256=42427369df56f961ca1aa60f02edb6b8f63cb0a60b7aed4e4f820cb5fda6951b' "$env_file"
grep -Fxq 'FP6_MICROPHONE_REGMAP_SDW_KO_SHA256=5a73fe7ca1d5c02644f42cf011a5425035624fa02091531555fb3171cbcbbab9' "$env_file"
grep -Fxq 'FP6_MICROPHONE_WCD_COMMON_KO_SHA256=fc236f5cedeea8050faa3baad56f12d1b60cde89be521d08dbe272eab462baf2' "$env_file"
grep -Fxq 'FP6_MICROPHONE_WCD9378_KO_SHA256=4ad0f230d5a1fc1893e28d904562dc633f7e925976cd8057585d6fbfe2866bb6' "$env_file"
grep -Fxq 'FP6_MICROPHONE_WCD9378_SDW_KO_SHA256=25afa7124d8ee1e8160d72a9478b1ab4336ed90a157e5266722f94357c479c38' "$env_file"

expected_patch_hashes='9af84a04e48a5595a44bde84b71c21e410397381dc24c07563aa8b74ee2b62a5  0029-soundwire-qcom-add-SCP-address-paging-support.patch
8f16511593487aaf94e1e212e280a78528103c7e74d41d414d0909c6b066e982  0030-ASoC-codecs-wcd9378-add-SoundWire-skeleton-driver-WI.patch
d14462efc9afc03158b3f5e8cbeac13ccf9c83f5b196538b06052860af4a32f8  0031-arm64-dts-qcom-milos-fairphone-fp6-enable-SoundWire-.patch
c7ef475aace517b2feadbb29e727d103fa4527c12dc4a8a5f6f1eaf907a90e95  0032-ASoC-codecs-wcd9378-grow-skeleton-into-TX-capture-co.patch
4fd48fab3768e32b864f02f03e2518d6acac021513d6898882d0b17e991e5171  0033-arm64-dts-qcom-milos-fairphone-fp6-add-WCD9378-codec.patch
b25f96065e4e9b0eb7b32d3ac71b45df32a45bfcb142e0d685da95bb6f3e8d6d  0034-ASoC-codecs-wcd9378-keep-the-TX-SoundWire-bus-out-of.patch
585ccdf3ddbea08ffe9b7d860751961bbb9215dacb47cfa0f851a19b90a9251e  0035-ASoC-codecs-wcd9378-correct-the-ADC-analog-gain-TLV-.patch
615c2ea8646c82acb6b4c6f635b92f52832145e23c0af1e264189115d6917625  0036-ASoC-codecs-wcd9378-sync-with-the-mainline-v1-submis.patch
6cee0f009ff183e6b406e3d499de66b1c0d0bd570ebf6a0eb3ee53b37c556424  0037-ASoC-wcd9378-map-FP6-ADC2-to-SWRM-TX2-channel-1.patch
53fcddb1c91a7fcb4fdf33b9c0db7da8892bbf4597809ca3be6cd8e6d14bddd9  0038-arm64-dts-qcom-fp6-route-ADC2-to-TX-SWR-INPUT4.patch'
while read -r expected patch; do
  test "$(sha256sum "$patch_dir/$patch" | cut -d ' ' -f 1)" = "$expected"
done <<<"$expected_patch_hashes"

grep -Fq 'KBUILD_BUILD_TIMESTAMP=' "$builder"
grep -Fq 'scripts/config --enable MODULE_ALLOW_BTF_MISMATCH' "$builder"
grep -Fq "CONFIG_MODULE_ALLOW_BTF_MISMATCH=y" "$builder"
grep -Fq 'MODULE_BTF_MISMATCH_FALLBACK=true' "$builder"
grep -Fq 'scripts/config --enable MODULE_ALLOW_BTF_MISMATCH' "$relinker"
grep -Fq 'MODULE_BINARY_KABI_CHANGED=false' "$relinker"
grep -Fq 'RAM_BOOT_ONLY=true' "$relinker"
grep -Fq 'PHONE_ACCESSED=false' "$relinker"
grep -Fq 'PARTITION_WRITTEN=false' "$relinker"
grep -Fq 'FREQUENCY_HZ = 997' "$stimulus"
grep -Fq 'nonzero_after_startup > RATE' "$stimulus"
grep -Fq 'target > max(shoulders * 3.0, 1.0)' "$stimulus"
grep -Fq 'analyze_parser.add_argument("--search-tone", action="store_true")' "$stimulus"
grep -Fq 'LUMA_FP6_AMIC3_ROUTING_BUILD_VERSION=1' "$amic3_builder"
grep -Fq 'M=sound/soc/codecs modules' "$amic3_builder"
grep -Fq 'qcom/milos-fairphone-fp6.dtb' "$amic3_builder"
grep -Fq 'RAM_BOOT_ONLY=true' "$amic3_builder"
grep -Fq 'PHONE_ACCESSED=false' "$amic3_builder"
grep -Fq 'PARTITION_WRITTEN=false' "$amic3_builder"
grep -Fq 'cmp "$base_boot" "$roundtrip"' "$amic3_repacker"
grep -Fq 'KERNEL_UNCHANGED=true' "$amic3_repacker"
grep -Fq 'LUMA_ALLOW_UNPINNED_FP6_MICROPHONE_V4' "$amic3_repacker"
grep -Fq 'RAM_BOOT_ONLY=true' "$amic3_repacker"
grep -Fq 'PARTITION_WRITTEN=false' "$amic3_repacker"
if grep -Eiq '^[[:space:]]*(adb|fastboot|scp|ssh)([[:space:]]|$)' "$amic3_repacker"; then
  printf 'FAIL: offline AMIC3 repacker must not contact a device\n' >&2
  exit 1
fi
grep -Fq "The Fairphone (Gen. 6)" "$amic3_stager"
grep -Fq 'original-snd-soc-wcd9378-sdw.ko' "$amic3_stager"
grep -Fq 'MODULES_LOADED=false' "$amic3_stager"
grep -Fq 'SERVICES_RESTARTED=false' "$amic3_stager"
grep -Fq 'REBOOTED=false' "$amic3_stager"
grep -Fq 'PARTITIONS_WRITTEN=false' "$amic3_stager"
if grep -Eiq '^[[:space:]]*(insmod|modprobe|rmmod|systemctl|reboot|fastboot)([[:space:]]|$)' "$amic3_stager"; then
  printf 'FAIL: AMIC3 stager must not load, restart, reboot, or access fastboot\n' >&2
  exit 1
fi
grep -Fq 'LUMA_FP6_AMIC3_DAPM_BUILD_VERSION=1' "$amic3_dapm_builder"
grep -Fq 'qcom/milos-fairphone-fp6.dtb' "$amic3_dapm_builder"
grep -Fq 'MODULE_UNCHANGED=true' "$amic3_dapm_builder"
grep -Fq 'RAM_BOOT_ONLY=true' "$amic3_dapm_builder"
grep -Fq 'PHONE_ACCESSED=false' "$amic3_dapm_builder"
grep -Fq 'PARTITION_WRITTEN=false' "$amic3_dapm_builder"
grep -Fq 'cmp "$base_boot" "$roundtrip"' "$amic3_dapm_repacker"
grep -Fq 'FP6_MICROPHONE_V4_BOOT_SHA256' "$amic3_dapm_repacker"
grep -Fq 'LUMA_ALLOW_UNPINNED_FP6_MICROPHONE_V5' "$amic3_dapm_repacker"
grep -Fq 'KERNEL_UNCHANGED=true' "$amic3_dapm_repacker"
grep -Fq 'RAMDISK_UNCHANGED=true' "$amic3_dapm_repacker"
grep -Fq 'RAM_BOOT_ONLY=true' "$amic3_dapm_repacker"
grep -Fq 'PARTITION_WRITTEN=false' "$amic3_dapm_repacker"
if grep -Eiq '^[[:space:]]*(adb|fastboot|scp|ssh)([[:space:]]|$)' "$amic3_dapm_repacker"; then
  printf 'FAIL: offline AMIC3 DAPM repacker must not contact a device\n' >&2
  exit 1
fi
grep -Fq "The Fairphone (Gen. 6)" "$amic3_route_tester"
grep -Fq 'isolated-dec0' "$amic3_route_tester"
grep -Fq 'restore_primary_only' "$amic3_route_tester"
grep -Fq "cset 'TX SMIC MUX0' SWR_MIC0" "$amic3_route_tester"
grep -Fq 'arecord -q -D hw:0,1' "$amic3_route_tester"
if grep -Eiq '^[[:space:]]*(insmod|modprobe|rmmod|systemctl|reboot|fastboot)([[:space:]]|$)' "$amic3_route_tester"; then
  printf 'FAIL: bounded AMIC3 route tester must not load, restart, reboot, or access fastboot\n' >&2
  exit 1
fi
grep -Fq 'M=drivers/base/regmap modules' "$builder"
grep -Fq 'M=sound/soc/codecs' "$builder"
grep -Fq 'KBUILD_EXTRA_SYMBOLS=/work/linux/drivers/base/regmap/Module.symvers' "$builder"
grep -Fq -- '--remove-section=.BTF.base' "$builder"
grep -Fq 'RAM_BOOT_ONLY=true' "$builder"
grep -Fq 'PHONE_ACCESSED=false' "$builder"
grep -Fq 'PARTITION_WRITTEN=false' "$builder"

grep -Fq "The Fairphone (Gen. 6)" "$installer"
grep -Fq 'module retains incompatible split BTF' "$installer"
grep -Fq 'modules_loaded=false' "$installer"
grep -Fq 'partitions_written=false' "$installer"
if grep -Eiq '^[[:space:]]*(insmod|modprobe|rmmod|systemctl|reboot|fastboot)([[:space:]]|$)' "$installer"; then
  printf 'FAIL: module installer must not load, restart, reboot, or access fastboot\n' >&2
  exit 1
fi

grep -Fq 'cmp "$base_boot" "$roundtrip"' "$repacker"
grep -Fq 'FP6_MICROPHONE_PROVEN_KERNEL_SHA256' "$repacker"
grep -Fq 'FP6_MICROPHONE_V2_BOOT_SHA256' "$repacker"
grep -Fq 'KERNEL_UNCHANGED=true' "$repacker"
grep -Fq 'cmp "$base_dir/ramdisk" "$candidate_dir/ramdisk"' "$repacker"
grep -Fq 'RAM_BOOT_ONLY=true' "$repacker"
grep -Fq 'PARTITION_WRITTEN=false' "$repacker"
if grep -Eiq '^[[:space:]]*(adb|fastboot|scp|ssh)([[:space:]]|$)' "$repacker"; then
  printf 'FAIL: offline microphone repacker must not contact a device\n' >&2
  exit 1
fi

grep -Fq 'cmp "$base_boot" "$roundtrip"' "$live_repacker"
grep -Fq 'FP6_MICROPHONE_V3_RELINKED_KERNEL_SHA256' "$live_repacker"
grep -Fq 'LUMA_ALLOW_UNPINNED_FP6_MICROPHONE_V3' "$live_repacker"
grep -Fq 'CANDIDATE_HASH_PINNED=' "$live_repacker"
grep -Fq 'MODULE_BTF_MISMATCH_FALLBACK=true' "$live_repacker"
grep -Fq 'cmp "$base_dir/ramdisk" "$candidate_dir/ramdisk"' "$live_repacker"
grep -Fq 'RAM_BOOT_ONLY=true' "$live_repacker"
grep -Fq 'PARTITION_WRITTEN=false' "$live_repacker"
if grep -Eiq '^[[:space:]]*(adb|fastboot|scp|ssh)([[:space:]]|$)' "$live_repacker"; then
  printf 'FAIL: offline live-capture repacker must not contact a device\n' >&2
  exit 1
fi

grep -Fq "The Fairphone (Gen. 6)" "$inspector"
grep -Fq "MultiMedia2 Mixer TX_CODEC_DMA_TX_3" "$inspector"
grep -Fq "TX_AIF1_CAP Mixer DEC0" "$inspector"
grep -Fq "TX_AIF1_CAP Mixer DEC1" "$inspector"
grep -Fq 'find -L /sys/bus/soundwire/devices' "$inspector"
if grep -Eiq '^[[:space:]]*(amixer[[:space:]].*cset|insmod|modprobe|rmmod|systemctl[[:space:]]+(start|stop|restart)|reboot|fastboot)([[:space:]]|$)' "$inspector"; then
  printf 'FAIL: microphone inspector must remain read-only\n' >&2
  exit 1
fi

grep -Fq 'simultaneous packed-stereo raw capture' "$ledger"
grep -Fq 'accepts the FP6 dual-microphone transport' "$ledger"

printf 'PASS: FP6 microphone candidate is exact-hash, RAM-boot-only, and acceptance-honest\n'
