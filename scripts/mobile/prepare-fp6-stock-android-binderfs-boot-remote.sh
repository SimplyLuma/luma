#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build a RAM-only BinderFS boot image from the exact running FP6 boot_b.
# The source partition is read only.  The candidate changes only the kernel
# payload; ramdisk, DTB, command line, header fields, and every other boot
# component must round-trip byte-for-byte.

set -Eeuo pipefail
umask 077

boot_link=/dev/disk/by-partlabel/boot_b
boot_partition=
expected_partition_sha=2eb1c71fe073c8624ed6f1a4f1267744cd7c8281b52c4f64eca65b9f9050a1e6
kernel=${LUMA_CONTAINER_KERNEL:-/var/tmp/luma-fp6-stock-android-binderfs-compat1/bundle/Image.gz}
expected_kernel_sha=${LUMA_CONTAINER_KERNEL_SHA256:-d1476dbea625aeb22d5d2a27df3a928cd9a2a13bd72a80bfda8507d376df0153}
output_root=${LUMA_CONTAINER_BOOT_OUTPUT_ROOT:-/var/tmp/luma-fp6-stock-android-binderfs-boot1}
candidate_name=${LUMA_CONTAINER_BOOT_CANDIDATE_NAME:-boot-fp6-luma-binderfs-ram1.img}
change_scope=${LUMA_CONTAINER_BOOT_CHANGE_SCOPE:-kernel_only_binderfs}
running_config_gate=${LUMA_CONTAINER_RUNNING_CONFIG_GATE:-binderfs_absent}
candidate=$output_root/$candidate_name
manifest=$output_root/manifest.env
work=
complete=false

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }
cleanup() {
  [[ -z ${work:-} || ! -d $work ]] || find "$work" -depth -delete
  [[ $complete == true || ! -d $output_root ]] || find "$output_root" -depth -delete
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] ||
  die 'device identity mismatch'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not running slot b'
[[ $(uname -r) == 7.1.2-luma-fp-ims1 ]] || die 'accepted ims1 kernel is not running'
case $running_config_gate in
  binderfs_absent)
    zcat /proc/config.gz | grep -Fx '# CONFIG_ANDROID_BINDERFS is not set' >/dev/null ||
      die 'running kernel is not the proven non-BinderFS kernel'
    ;;
  binderfs_without_memfd_shim)
    zcat /proc/config.gz | grep -Fx 'CONFIG_ANDROID_BINDERFS=y' >/dev/null ||
      die 'running kernel does not have the accepted BinderFS support'
    ! zcat /proc/config.gz | grep -Fx 'CONFIG_MEMFD_ASHMEM_SHIM=y' >/dev/null ||
      die 'running kernel already has the memfd ashmem shim'
    ;;
  *) die 'unsupported running config gate' ;;
esac
[[ -L $boot_link ]] || die 'boot_b partition link differs'
boot_partition=$(readlink -f "$boot_link")
[[ -b $boot_partition && ! -L $boot_partition ]] || die 'boot_b identity differs'
[[ $(blockdev --getsize64 "$boot_partition") == 100663296 ]] || die 'boot_b size differs'
[[ $(hash "$boot_partition") == "$expected_partition_sha" ]] || die 'boot_b hash differs'
[[ -f $kernel && ! -L $kernel && $(hash "$kernel") == "$expected_kernel_sha" ]] ||
  die 'BinderFS kernel hash differs'
[[ ! -e $output_root ]] || die 'output root already exists'
for command in cmp dd find mkbootimg python3 sha256sum stat unpack_bootimg; do
  command -v "$command" >/dev/null || die "missing command: $command"
done

install -d -m 0700 "$output_root"
work=$(mktemp -d /var/tmp/luma-binderfs-boot-work.XXXXXXXX)
install -d -m 0700 "$work/base" "$work/roundtrip-check" "$work/candidate-check"
dd if="$boot_partition" of="$work/boot-b-partition.img" bs=4M status=none
unpack_bootimg --boot_img "$work/boot-b-partition.img" --out "$work/base" \
  --format=mkbootimg -0 >"$work/args0"

python3 - "$work/args0" "$work/roundtrip.img" <<'PY'
import os
import subprocess
import sys

args = open(sys.argv[1], "rb").read().split(b"\0")
if args and args[-1] == b"":
    args.pop()
subprocess.run(
    ["mkbootimg", "--output", sys.argv[2], *(os.fsdecode(arg) for arg in args)],
    check=True,
)
PY

roundtrip_size=$(stat -c %s "$work/roundtrip.img")
[[ $roundtrip_size -gt 0 && $roundtrip_size -le 100663296 ]] || die 'round-trip size differs'
cmp -n "$roundtrip_size" "$work/boot-b-partition.img" "$work/roundtrip.img" ||
  die 'boot_b does not round-trip byte-exactly'
unpack_bootimg --boot_img "$work/roundtrip.img" --out "$work/roundtrip-check" >/dev/null

python3 - "$work/args0" "$candidate" "$kernel" <<'PY'
import os
import subprocess
import sys

args = open(sys.argv[1], "rb").read().split(b"\0")
if args and args[-1] == b"":
    args.pop()
try:
    index = args.index(b"--kernel")
except ValueError as exc:
    raise SystemExit("missing --kernel in exact mkbootimg arguments") from exc
if index + 1 >= len(args):
    raise SystemExit("missing kernel value in exact mkbootimg arguments")
args[index + 1] = os.fsencode(sys.argv[3])
subprocess.run(
    ["mkbootimg", "--output", sys.argv[2], *(os.fsdecode(arg) for arg in args)],
    check=True,
)
PY

candidate_size=$(stat -c %s "$candidate")
[[ $candidate_size -gt 0 && $candidate_size -le 100663296 ]] || die 'candidate size is invalid'
unpack_bootimg --boot_img "$candidate" --out "$work/candidate-check" >/dev/null
[[ $(hash "$work/candidate-check/kernel") == "$expected_kernel_sha" ]] ||
  die 'candidate kernel differs after unpack'

while IFS= read -r -d '' base_file; do
  relative=${base_file#"$work/roundtrip-check/"}
  [[ $relative == kernel ]] && continue
  [[ -f $work/candidate-check/$relative ]] || die "candidate component missing: $relative"
  cmp "$base_file" "$work/candidate-check/$relative" ||
    die "candidate component changed: $relative"
done < <(find "$work/roundtrip-check" -type f -print0)

base_non_kernel=$(find "$work/roundtrip-check" -type f ! -name kernel -printf '%P\n' | sort)
candidate_non_kernel=$(find "$work/candidate-check" -type f ! -name kernel -printf '%P\n' | sort)
[[ $base_non_kernel == "$candidate_non_kernel" ]] || die 'candidate component inventory differs'

candidate_sha=$(hash "$candidate")
install -m 0600 /dev/stdin "$manifest" <<EOF
VERSION=1
DEVICE=Fairphone_Gen_6
SLOT=b
SOURCE_BOOT_PARTITION_SHA256=$expected_partition_sha
CANDIDATE_KERNEL_SHA256=$expected_kernel_sha
CANDIDATE_BOOT_SHA256=$candidate_sha
SOURCE_BOOT_IMAGE_SIZE=$roundtrip_size
CANDIDATE_BOOT_SIZE=$candidate_size
CHANGE_SCOPE=$change_scope
EXECUTION_MODE=fastboot_boot_ram_only
EOF
sync -f "$candidate"
sync -f "$manifest"
complete=true

printf 'BINDERFS_RAM_BOOT_CANDIDATE_READY=true\n'
printf 'candidate=%s\n' "$candidate"
printf 'candidate_sha256=%s\n' "$candidate_sha"
printf 'candidate_size=%s\n' "$candidate_size"
printf 'source_boot_b_sha256=%s\n' "$expected_partition_sha"
printf 'non_kernel_components_unchanged=true\n'
printf 'partition_writes=0\n'
