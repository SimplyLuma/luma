#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Fairphone 6 boot image for the shared ARM image: the Alpha 1 boot image's
# kernel and DTB (devices/fairphone/fp6/device.toml [[releases]] alpha1), its
# ramdisk plus one more cpio archive, and a command line that names no
# deployment.
#
# The added archive holds scripts/mobile/fp6-native-init-functions.sh as
# /init_functions.sh, and the shared OSTree boot resolver
# (scripts/mobile/common/initramfs): /usr/lib/luma/ostree-boot.sh and
# /hooks-extra/50-luma-ostree-default.sh, which point /ostree/root.<slot> at
# ostree's default deployment on every boot. ostree=/ostree/root.a names the
# slot link; the bootloader appends androidboot.slot_suffix. One boot image
# serves every nightly, rpm-ostree upgrade and rollback; it changes only with
# the kernel or the initramfs.
#
# It also holds /hooks-cleanup/60-luma-fp6-modem-store.sh
# (scripts/mobile/fp6-initramfs): on the first start, which is `fastboot boot`,
# it copies the modem storage partitions into the private rmtfs store before
# any normal boot lets the bootloader erase modemst.
#
# usage: build-fp6-boot-image.sh --alpha-boot FILE --output FILE
#
# Needs AOSP's mkbootimg.py, unpack_bootimg.py and avbtool.py (MKBOOTIMG,
# UNPACK_BOOTIMG, AVBTOOL, default: on PATH), cpio, gzip and python3.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
device="$repo_root/devices/fairphone/fp6"
shared="$repo_root/scripts/mobile/common/initramfs"
modem_store="$repo_root/scripts/mobile/fp6-initramfs/hooks-cleanup/60-luma-fp6-modem-store.sh"
fail() { printf 'build-fp6-boot-image: %s\n' "$*" >&2; exit 1; }
# The generic phone userdata image's filesystems (scripts/mobile/build-phone-userdata.sh).
root_uuid=03797c05-cf87-4eda-adc4-6ce111baf2d3
boot_uuid=032D-57AD

alpha="" output=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --alpha-boot) alpha=$(realpath -e "${2:?}"); shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    *) sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2 ;;
  esac
done
[ -n "$alpha" ] && [ -n "$output" ] || fail "missing arguments"
for f in "$shared/luma-ostree-boot.sh" "$shared/hooks-extra/50-luma-ostree-default.sh"; do
  [ -f "$f" ] || fail "missing $f (the shared OSTree boot resolver)"
done
[ -f "$modem_store" ] || fail "missing $modem_store (the modem store seed)"
mkbootimg=${MKBOOTIMG:-mkbootimg.py} unpack=${UNPACK_BOOTIMG:-unpack_bootimg.py} avbtool=${AVBTOOL:-avbtool.py}

eval "$(python3 - "$device" <<'PY'
import shlex, sys, tomllib
d = sys.argv[1]
dev = tomllib.load(open(d + "/device.toml", "rb"))
boot = tomllib.load(open(d + "/boot.toml", "rb"))
alpha = next(r for r in dev["releases"] if r["name"] == "alpha1")
v = {
    "alpha_sha256": alpha["boot_image_sha256"],
    "ramdisk_sha256": dev["initramfs"]["ramdisk_sha256"],
    "init_functions": dev["initramfs"]["init_functions"],
    "init_functions_sha256": dev["initramfs"]["init_functions_sha256"],
    "cmdline_fixed": " ".join(boot["cmdline"]["args"]),
    "forbidden": " ".join(boot["cmdline"]["forbidden"]),
    "partition_size": boot["partition_size"],
    "rollback_index": boot["avb"]["rollback_index"],
}
for k, val in v.items():
    print(f"{k}={shlex.quote(str(val))}")
PY
)"

echo "$alpha_sha256  $alpha" | sha256sum -c --quiet - || fail "$alpha is not the Alpha 1 boot image"
script="$repo_root/$init_functions"
echo "$init_functions_sha256  $script" | sha256sum -c --quiet - || fail "$init_functions differs from device.toml"

work=$(mktemp -d "${TMPDIR:-/var/tmp}/fp6-boot.XXXXXX")
trap 'rm -rf "$work"' EXIT
umask 022
python3 "$unpack" --boot_img "$alpha" --out "$work/in" --format mkbootimg >"$work/args"
echo "$ramdisk_sha256  $work/in/ramdisk" | sha256sum -c --quiet - || fail "the Alpha 1 ramdisk differs from device.toml"

# The added archive: later files in the ramdisk replace earlier ones.
o="$work/overlay"
install -D -m 0644 "$script" "$o/init_functions.sh"
install -D -m 0644 "$shared/luma-ostree-boot.sh" "$o/usr/lib/luma/ostree-boot.sh"
install -D -m 0755 "$shared/hooks-extra/50-luma-ostree-default.sh" "$o/hooks-extra/50-luma-ostree-default.sh"
install -D -m 0755 "$modem_store" "$o/hooks-cleanup/60-luma-fp6-modem-store.sh"
# 00755: GNU chmod keeps a directory's setgid bit (inherited from TMPDIR) under 0755.
find "$o" -type d -exec chmod 00755 {} +
find "$o" -exec touch -h -d @0 {} +
(cd "$o" && find . -mindepth 1 | LC_ALL=C sort | cpio --quiet --reproducible -o -H newc -R 0:0) | gzip -9n >"$work/overlay.cpio.gz"
cat "$work/in/ramdisk" "$work/overlay.cpio.gz" >"$work/ramdisk"

cmdline="$cmdline_fixed pmos_root_uuid=$root_uuid pmos_boot_uuid=$boot_uuid ostree=/ostree/root.a"
for word in $forbidden androidboot.slot_suffix; do
  case " $cmdline " in *" $word"*) fail "forbidden argument $word in: $cmdline" ;; esac
done

# Same header as Alpha 1 (version, offsets, page size, OS version); only the
# ramdisk and the command line change.
python3 - "$work/args" "$work" "$cmdline" >"$work/mkargs" <<'PY'
import shlex, sys
args = shlex.split(open(sys.argv[1]).read())
work, cmdline = sys.argv[2], sys.argv[3]
out, i = [], 0
while i < len(args):
    k, v = args[i], args[i + 1]
    i += 2
    if k == "--cmdline":
        v = cmdline
    elif k == "--ramdisk":
        v = work + "/ramdisk"
    out += [k, v]
print("\0".join(out), end="")
PY
mapfile -d '' mkargs <"$work/mkargs"
python3 "$mkbootimg" "${mkargs[@]}" --output "$work/boot.img"
# An unsigned AVB hash footer, as boot.toml [avb] describes. The salt is the
# image's digest, so a rebuild is byte-identical.
salt=$(sha256sum "$work/boot.img" | cut -d' ' -f1)
python3 "$avbtool" add_hash_footer --image "$work/boot.img" --partition_name boot \
  --partition_size "$partition_size" --algorithm NONE --hash_algorithm sha256 \
  --rollback_index "$rollback_index" --salt "$salt"

# Check the result the way the bootloader and the initramfs will see it.
python3 "$unpack" --boot_img "$work/boot.img" --out "$work/check" --format mkbootimg >"$work/check.args"
grep -Fq -- "--cmdline '$cmdline'" "$work/check.args" || fail "command line not in the image"
cmp -s "$work/check/kernel" "$work/in/kernel" && cmp -s "$work/check/dtb" "$work/in/dtb" || fail "kernel or DTB changed"
# Every archive in the ramdisk, in order, later entries replacing earlier ones.
python3 - "$work/check/ramdisk" "$script" "$modem_store" <<'PY' || fail "the ramdisk lacks the OSTree boot pieces or the modem store seed"
import gzip, sys
data, files, i = gzip.open(sys.argv[1]).read(), {}, 0
while i < len(data):
    if data[i:i + 6] != b"070701":
        i += 1
        continue
    h = [int(data[i + 6 + 8 * n:i + 14 + 8 * n], 16) for n in range(13)]
    mode, size, namesize = h[1], h[6], h[11]
    name = data[i + 110:i + 110 + namesize - 1].decode()
    start = (i + 110 + namesize + 3) & ~3
    files[name.lstrip("./")] = (mode, data[start:start + size])
    i = (start + size + 3) & ~3
script = open(sys.argv[2], "rb").read()
assert files["init_functions.sh"][1] == script, "init_functions.sh"
assert "etc/luma-fp6-ostree" in files
for name in ("hooks-extra/50-luma-ostree-default.sh", "hooks-cleanup/60-luma-fp6-modem-store.sh",
             "usr/lib/ostree/ostree-prepare-root"):
    assert files[name][0] & 0o111, name
assert files["usr/lib/luma/ostree-boot.sh"][1], "ostree-boot.sh"
assert files["hooks-cleanup/60-luma-fp6-modem-store.sh"][1] == open(sys.argv[3], "rb").read(), "modem store seed"
PY
python3 "$avbtool" verify_image --image "$work/boot.img" >/dev/null
[ "$(stat -c %s "$work/boot.img")" = "$partition_size" ] || fail "the image is not the boot partition's size"

install -D -m 0644 "$work/boot.img" "$output"
printf 'CMDLINE=%s\nBOOT_SHA256=%s\nALPHA_BOOT_SHA256=%s\nRAMDISK_OVERLAY_SHA256=%s\n' "$cmdline" \
  "$(sha256sum "$output" | cut -d' ' -f1)" "$alpha_sha256" "$(sha256sum "$work/overlay.cpio.gz" | cut -d' ' -f1)"
