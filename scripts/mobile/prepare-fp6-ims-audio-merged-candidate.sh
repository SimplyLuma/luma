#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose a full-size FP6 boot image which keeps the accepted IMS kernel,
# initramfs, boot header and partition tail while replacing only its DTB with
# the union of:
#
#   * the physically accepted microphone-v5 audio graph, and
#   * the currently accepted fingerprint/QSEE additions.
#
# This is an offline operation. It never contacts a phone or writes a
# partition. The current and audio DTBs are retained as semantic oracles and
# the merged tree is checked against both before an image is emitted.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
current_boot=${1:?usage: prepare-fp6-ims-audio-merged-candidate.sh CURRENT_IMS_FULL_BOOT AUDIO_V5_BOOT OUTPUT_DIR}
audio_boot=${2:?missing accepted audio-v5 boot image}
output_dir=${3:?missing output directory}

expected_current_boot_sha=${LUMA_FP6_IMS_AUDIO_CURRENT_BOOT_SHA256:-481f0a3e0533395cab125697e89ecc0e2dfd8b6261f45300a0775040ace3e51a}
expected_audio_boot_sha=${LUMA_FP6_IMS_AUDIO_V5_BOOT_SHA256:-77819b90457e55809708aac1bc01d4d5d063f1bc274c1a4b0f9de716bfe94e01}
expected_kernel_sha=${LUMA_FP6_IMS_AUDIO_KERNEL_SHA256:-04eae4ebf26ad68b4a543dd50cae3cb6c9400729bb15e73290b678a1dfeb4ce1}
expected_ramdisk_sha=${LUMA_FP6_IMS_AUDIO_RAMDISK_SHA256:-096bbb1cadd09efd89b6f50d45c4a8ece83023663d7084872203394166b99e7a}
expected_current_dtb_sha=${LUMA_FP6_IMS_AUDIO_CURRENT_DTB_SHA256:-3eea76e3e41ef0b6fafc3e06c137c59f5b82640bea596f1511cdcf690c7c8f3d}
expected_audio_dtb_sha=${LUMA_FP6_IMS_AUDIO_V5_DTB_SHA256:-cd942c35f96d13c8ea22bcb9bab611abb8cf2a34fec9c95d2e17a56baa617c02}
expected_full_size=100663296

fingerprint_overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-fingerprint-base-0382-lab.dtso
fingerprint_enable_overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-fingerprint-lab-enable.dtso
qsee_pool_overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-pool.dtso
qsee_heaps_overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-dedicated-heaps.dtso
qsee_log_overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-log.dtso
appsbl_overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-appsbl-contract.dtso

candidate=$output_dir/boot-fp6-luma-ims-audio-v1-full.img
slim_candidate=$output_dir/boot-fp6-luma-ims-audio-v1.img
merged_dtb=$output_dir/milos-fairphone-fp6-ims-audio-v1.dtb

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp cpp dd dtc fdtoverlay fdtget fdtput find grep install mkbootimg \
  mktemp python3 sha256sum stat tail unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing composition tool: $tool"
done
for artifact in "$current_boot" "$audio_boot" "$fingerprint_overlay" \
  "$fingerprint_enable_overlay" "$qsee_pool_overlay" "$qsee_heaps_overlay" \
  "$qsee_log_overlay" "$appsbl_overlay"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ "$(hash "$current_boot")" = "$expected_current_boot_sha" ] || die 'current IMS boot hash differs'
[ "$(stat -c %s "$current_boot")" = "$expected_full_size" ] || die 'current IMS boot size differs'
[ "$(hash "$audio_boot")" = "$expected_audio_boot_sha" ] || die 'accepted audio-v5 boot hash differs'

run_mkbootimg_args0() {
  local args_file=$1 output=$2
  python3 - "$args_file" "$output" <<'PY'
import os
import subprocess
import sys

args_file, output = sys.argv[1:]
with open(args_file, "rb") as stream:
    args = stream.read().split(b"\0")
if args and args[-1] == b"":
    args.pop()
subprocess.run(
    ["mkbootimg", "--output", output, *(os.fsdecode(arg) for arg in args)],
    check=True,
)
PY
}

compile_overlay() {
  local source=$1 output=$2
  cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp \
    -I "$repo_root" -I "$dts_include" "$source" |
    dtc -@ -I dts -O dtb -o "$output"
}

apply_overlay() {
  local base=$1 overlay=$2 output=$3
  fdtoverlay -i "$base" -o "$output" "$overlay"
}

mkdir -p "$output_dir"
work=$(mktemp -d "$output_dir/repack.XXXXXX")
cleanup() { [ ! -e "$work" ] || find "$work" -depth -delete; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/current" "$work/audio" "$work/candidate" "$work/roundtrip"

dts_irq_header=$(find /usr/src /var/tmp -path '*/include/dt-bindings/interrupt-controller/irq.h' \
  -type f -print -quit 2>/dev/null || true)
[ -n "$dts_irq_header" ] || die 'kernel DTS interrupt binding header is absent'
grep -Eq '^#define[[:space:]]+IRQ_TYPE_EDGE_RISING[[:space:]]+1([[:space:]]|$)' \
  "$dts_irq_header" || die 'IRQ_TYPE_EDGE_RISING binding differs'
dts_include=${dts_irq_header%/dt-bindings/interrupt-controller/irq.h}

args_file=$work/mkbootimg.args0
unpack_bootimg --boot_img "$current_boot" --out "$work/current" \
  --format=mkbootimg -0 >"$args_file"
unpack_bootimg --boot_img "$audio_boot" --out "$work/audio" >/dev/null

[ "$(hash "$work/current/kernel")" = "$expected_kernel_sha" ] || die 'IMS kernel hash differs'
[ "$(hash "$work/current/ramdisk")" = "$expected_ramdisk_sha" ] || die 'IMS ramdisk hash differs'
[ "$(hash "$work/current/dtb")" = "$expected_current_dtb_sha" ] || die 'current IMS DTB hash differs'
[ "$(hash "$work/audio/dtb")" = "$expected_audio_dtb_sha" ] || die 'audio-v5 DTB hash differs'
[ "$(fdtget -t x "$work/audio/dtb" /soc@0/pinctrl@f100000 phandle)" = 35 ] ||
  die 'audio-v5 TLMM phandle differs from the fingerprint overlay gate'

compile_overlay "$fingerprint_overlay" "$work/fingerprint.dtbo"
compile_overlay "$fingerprint_enable_overlay" "$work/fingerprint-enable.dtbo"
compile_overlay "$qsee_pool_overlay" "$work/qsee-pool.dtbo"
compile_overlay "$qsee_heaps_overlay" "$work/qsee-heaps.dtbo"
compile_overlay "$qsee_log_overlay" "$work/qsee-log.dtbo"
compile_overlay "$appsbl_overlay" "$work/appsbl.dtbo"

apply_overlay "$work/audio/dtb" "$work/fingerprint.dtbo" "$work/merge-1.dtb"
apply_overlay "$work/merge-1.dtb" "$work/fingerprint-enable.dtbo" "$work/merge-2.dtb"
apply_overlay "$work/merge-2.dtb" "$work/qsee-pool.dtbo" "$work/merge-3.dtb"
fdtput -d "$work/merge-3.dtb" /firmware/scm memory-region
apply_overlay "$work/merge-3.dtb" "$work/qsee-heaps.dtbo" "$work/merge-4.dtb"
apply_overlay "$work/merge-4.dtb" "$work/qsee-log.dtbo" "$work/merge-5.dtb"
apply_overlay "$work/merge-5.dtb" "$work/appsbl.dtbo" "$work/merge-6.dtb"

# Overlays cannot rename nodes. Reproduce the accepted stock-visible QSEE node
# names, then restore the accepted matched-CMA reusable pool semantics.
dtc -I dtb -O dts -o "$work/pre-rename.dts" "$work/merge-6.dtb" 2>"$work/pre-rename.warn"
python3 - "$work/pre-rename.dts" "$work/renamed.dts" <<'PY'
from pathlib import Path
import sys

source, output = map(Path, sys.argv[1:])
text = source.read_text()
for old, new in {
    "qseecom-apps-pool": "qseecom_region",
    "qseecom-ta-pool": "qseecom_ta_region",
}.items():
    count = text.count(old)
    if count != 3:
        raise SystemExit(f"unexpected {old!r} occurrence count: {count}")
    text = text.replace(old, new)
output.write_text(text)
PY
dtc -@ -I dts -O dtb -o "$merged_dtb" "$work/renamed.dts" 2>"$work/renamed.warn"
for pool in /reserved-memory/qseecom_region /reserved-memory/qseecom_ta_region; do
  fdtput -d "$merged_dtb" "$pool" no-map
  fdtput "$merged_dtb" "$pool" reusable
done

# Structural union check. Every audio-v5 node/property must remain byte-for-
# byte equivalent in normalized DTS, while every non-reference IMS-only value
# must match the running tree. Explicit relationship checks below cover the
# phandle-bearing fingerprint and QSEE properties.
dtc -I dtb -O dts -o "$work/current.dts" "$work/current/dtb" 2>"$work/current.warn"
dtc -I dtb -O dts -o "$work/audio.dts" "$work/audio/dtb" 2>"$work/audio.warn"
dtc -I dtb -O dts -o "$work/merged.dts" "$merged_dtb" 2>"$work/merged.warn"
python3 - "$work/current.dts" "$work/audio.dts" "$work/merged.dts" <<'PY'
from pathlib import Path
import sys

def parse(path):
    stack = []
    nodes = {}
    for raw in Path(path).read_text(errors="strict").splitlines():
        line = raw.strip()
        if not line or line.startswith("/dts-v1/"):
            continue
        if line.endswith("{"):
            name = line[:-1].strip()
            if name == "/":
                stack = []
                nodes.setdefault("/", {})
            else:
                stack.append(name)
                nodes.setdefault("/" + "/".join(stack), {})
        elif line.startswith("};"):
            if stack:
                stack.pop()
        elif "=" in line or line.endswith(";"):
            path_name = "/" + "/".join(stack) if stack else "/"
            key = line.split("=", 1)[0].strip().rstrip(";")
            nodes.setdefault(path_name, {})[key] = line
    return nodes

current, audio, merged = map(parse, sys.argv[1:])
expected_nodes = set(current) | set(audio)
if set(merged) != expected_nodes:
    raise SystemExit(
        f"merged node union differs: missing={sorted(expected_nodes-set(merged))} "
        f"extra={sorted(set(merged)-expected_nodes)}"
    )

reference_keys = {
    "phandle", "pinctrl-0", "pinctrl-1", "pinctrl-2", "pinctrl-3", "pinctrl-4",
    "memory-region", "qseecom_mem", "qseecom_ta_mem",
}
for node in sorted(expected_nodes):
    expected_keys = set(current.get(node, {})) | set(audio.get(node, {}))
    actual_keys = set(merged[node])
    if actual_keys != expected_keys:
        raise SystemExit(
            f"property union differs at {node}: missing={sorted(expected_keys-actual_keys)} "
            f"extra={sorted(actual_keys-expected_keys)}"
        )
    for key, value in audio.get(node, {}).items():
        if merged[node][key] != value:
            raise SystemExit(f"audio-v5 property changed at {node}:{key}")
    for key, value in current.get(node, {}).items():
        if key in audio.get(node, {}) or key in reference_keys:
            continue
        if merged[node][key] != value:
            raise SystemExit(f"IMS-only scalar changed at {node}:{key}")
PY

# Fingerprint gate and reference checks.
[ "$(fdtget "$merged_dtb" /focalfp-ft9362 status)" = okay ] || die 'fingerprint node not enabled'
[ "$(fdtget "$merged_dtb" /focalfp-ft9362 compatible)" = focaltech,fp ] || die 'fingerprint compatible differs'
[ "$(fdtget -t x "$merged_dtb" /focalfp-ft9362 interrupt-parent)" = 35 ] || die 'fingerprint interrupt parent differs'
[ "$(fdtget -t x "$merged_dtb" /focalfp-ft9362 irq-gpios)" = '35 4b 0' ] || die 'fingerprint IRQ GPIO differs'
[ "$(fdtget -t x "$merged_dtb" /focalfp-ft9362 reset-gpios)" = '35 4a 0' ] || die 'fingerprint reset GPIO differs'
[ "$(fdtget -t x "$merged_dtb" /focalfp-ft9362 vdd-gpios)" = '35 1d 0' ] || die 'fingerprint power GPIO differs'

# QSEE pool and ownership checks.
apps=/reserved-memory/qseecom_region
ta=/reserved-memory/qseecom_ta_region
qsee=/soc@0/qseecom@c1700000
[ "$(fdtget -t x "$merged_dtb" "$apps" size)" = '0 1400000' ] || die 'QSEE apps pool size differs'
[ "$(fdtget -t x "$merged_dtb" "$ta" size)" = '0 1000000' ] || die 'QSEE TA pool size differs'
for pool in "$apps" "$ta"; do
  [ "$(fdtget -t x "$merged_dtb" "$pool" alignment)" = '0 400000' ] || die "QSEE alignment differs: $pool"
  [ "$(fdtget -t x "$merged_dtb" "$pool" alloc-ranges)" = '0 80000000 0 80000000' ] || die "QSEE range differs: $pool"
  fdtget "$merged_dtb" "$pool" reusable >/dev/null || die "QSEE pool is not reusable: $pool"
  ! fdtget "$merged_dtb" "$pool" no-map >/dev/null 2>&1 || die "QSEE pool retained no-map: $pool"
done
apps_phandle=$(fdtget -t x "$merged_dtb" "$apps" phandle)
ta_phandle=$(fdtget -t x "$merged_dtb" "$ta" phandle)
[ "$(fdtget -t x "$merged_dtb" "$qsee" memory-region)" = "$apps_phandle" ] || die 'QSEE apps memory-region differs'
[ "$(fdtget -t x "$merged_dtb" "$qsee" qseecom_mem)" = "$apps_phandle" ] || die 'QSEE apps reference differs'
[ "$(fdtget -t x "$merged_dtb" "$qsee" qseecom_ta_mem)" = "$ta_phandle" ] || die 'QSEE TA reference differs'
[ "$(fdtget "$merged_dtb" /aliases qseecom_mem)" = "$apps" ] || die 'QSEE apps alias differs'
[ "$(fdtget "$merged_dtb" /aliases qseecom_ta_mem)" = "$ta" ] || die 'QSEE TA alias differs'
[ "$(fdtget "$merged_dtb" /aliases qcom_qseecom)" = "$qsee" ] || die 'QSEE node alias differs'
fdtget "$merged_dtb" /firmware/scm/qseecom-tee-heaps luma,dedicated-heaps >/dev/null || die 'dedicated heaps marker absent'
fdtget "$merged_dtb" /firmware/scm luma,qsee-log-diagnostic >/dev/null || die 'QSEE diagnostic marker absent'
! fdtget "$merged_dtb" /firmware/scm memory-region >/dev/null 2>&1 || die 'global SCM pool reintroduced'

# Physically accepted microphone-v5 audio graph checks.
[ "$(fdtget "$merged_dtb" /soc@0/soundwire@3210000 status)" = okay ] || die 'RX SoundWire disabled'
[ "$(fdtget "$merged_dtb" /soc@0/soundwire@33b0000 status)" = okay ] || die 'TX SoundWire disabled'
[ "$(fdtget "$merged_dtb" /audio-codec compatible)" = qcom,wcd9378-codec ] || die 'WCD9378 codec absent'
[ "$(fdtget "$merged_dtb" /sound/wcd-capture-dai-link link-name)" = 'WCD Capture' ] || die 'WCD capture DAI link differs'
[ "$(fdtget "$merged_dtb" /sound/i2s-dai-link link-name)" = 'Senary MI2S Playback' ] || die 'speaker playback DAI link differs'
fdtget "$merged_dtb" /sound audio-routing | grep -Fq 'TX SWR_INPUT4' || die 'accepted AMIC3 DAPM route absent'

install -m 0644 "$merged_dtb" "$work/current/dtb"
run_mkbootimg_args0 "$args_file" "$slim_candidate"
unpack_bootimg --boot_img "$slim_candidate" --out "$work/candidate" >/dev/null
cmp "$work/current/kernel" "$work/candidate/kernel"
cmp "$work/current/ramdisk" "$work/candidate/ramdisk"
cmp "$merged_dtb" "$work/candidate/dtb"

# Preserve the accepted 96 MiB partition image and AVB footer byte-for-byte
# outside the new slim payload.
dd if="$current_boot" of="$candidate" bs=4M status=none
dd if="$slim_candidate" of="$candidate" bs=4M conv=notrunc status=none
[ "$(stat -c %s "$candidate")" = "$expected_full_size" ] || die 'full candidate size differs'
cmp -n "$(stat -c %s "$slim_candidate")" "$slim_candidate" "$candidate"
current_footer_sha=$(tail -c 64 "$current_boot" | sha256sum | awk '{print $1}')
candidate_footer_sha=$(tail -c 64 "$candidate" | sha256sum | awk '{print $1}')
[ "$candidate_footer_sha" = "$current_footer_sha" ] || die 'AVB footer changed'

{
  printf 'LUMA_FP6_IMS_AUDIO_MERGED_VERSION=1\n'
  printf 'CURRENT_FULL_BOOT_SHA256=%s\n' "$expected_current_boot_sha"
  printf 'AUDIO_V5_BOOT_SHA256=%s\n' "$expected_audio_boot_sha"
  printf 'KERNEL_SHA256=%s\n' "$expected_kernel_sha"
  printf 'RAMDISK_SHA256=%s\n' "$expected_ramdisk_sha"
  printf 'CURRENT_DTB_SHA256=%s\n' "$expected_current_dtb_sha"
  printf 'AUDIO_V5_DTB_SHA256=%s\n' "$expected_audio_dtb_sha"
  printf 'MERGED_DTB_SHA256=%s\n' "$(hash "$merged_dtb")"
  printf 'SLIM_CANDIDATE_SHA256=%s\n' "$(hash "$slim_candidate")"
  printf 'SLIM_CANDIDATE_SIZE=%s\n' "$(stat -c %s "$slim_candidate")"
  printf 'CANDIDATE_SHA256=%s\n' "$(hash "$candidate")"
  printf 'CANDIDATE_SIZE=%s\n' "$(stat -c %s "$candidate")"
  printf 'CURRENT_KERNEL_UNCHANGED=true\n'
  printf 'CURRENT_RAMDISK_UNCHANGED=true\n'
  printf 'CURRENT_BOOT_HEADER_UNCHANGED=true\n'
  printf 'CURRENT_PARTITION_TAIL_PRESERVED=true\n'
  printf 'CURRENT_AVB_FOOTER_PRESERVED=true\n'
  printf 'AUDIO_V5_GRAPH_EXACT=true\n'
  printf 'CURRENT_FINGERPRINT_QSEE_CONTRACT_RETAINED=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$candidate" "$slim_candidate" "$merged_dtb" "$output_dir/manifest.env"

printf 'FP6 IMS/audio merged full boot candidate: %s\n' "$candidate"
