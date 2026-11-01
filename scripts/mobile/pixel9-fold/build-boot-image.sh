#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the Pixel 9 Pro Fold (comet) Luma boot image: linux-zumapro at the
# pinned commit plus patches/linux-zumapro/comet, zumapro_defconfig plus
# config/kernel/pixel9fold.fragment, the device tree and an initramfs built in,
# legacy-LZ4, Android boot header v4 with no ramdisk, and an AVB hash footer at
# the stock 64 MiB boot size signed with the AOSP test key.
#
# Usage: build-boot-image.sh <initramfs-dir> <out-dir>
#
# Environment:
#   ROOT_UUID, BOOT_UUID  filesystem UUIDs of the Luma image's root and boot
#   OSTREE_PATH           the deployment to boot, e.g. /ostree/boot.0/luma/<checksum>/0
#   MALI_FIRMWARE_DIR     a linux-firmware tree holding arm/mali/arch11.8/mali_csffw.bin
#   MKBOOTIMG, AVBTOOL    AOSP mkbootimg.py and avbtool.py
#   AVB_KEY               AOSP testkey_rsa4096.pem
#   CACHE                 download cache for the kernel archive (default ~/.cache/luma-kernel)
#   WORK                  scratch directory, removed afterwards (default: a mktemp dir)
#   JOBS                  parallel jobs (default: nproc)
#
# Needs clang/lld (LLVM=1), lz4, dtc (fdtget), python3, git and modinfo.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
INITRAMFS=$(cd "$1" && pwd)
OUT=$2
LOCK=$ROOT/config/kernel/pixel9fold-source.json
FRAGMENT=$ROOT/config/kernel/pixel9fold.fragment
REFERENCE=$ROOT/config/kernel/pixel9fold.config
MODULES=$(cat "$ROOT/config/kernel/pixel9fold.modules")
DTB=zumapro-comet-dual
ROLLBACK=1791158400   # stock comet cp3a.261005.005 boot.img
BOOT_BYTES=67108864
FW_ZONE=0x1800000:0x1800a00   # the bootloader's write
: "${ROOT_UUID:?}" "${BOOT_UUID:?}" "${OSTREE_PATH:?}" "${MALI_FIRMWARE_DIR:?}" "${MKBOOTIMG:?}" "${AVBTOOL:?}" "${AVB_KEY:?}"
CACHE=${CACHE:-$HOME/.cache/luma-kernel}
JOBS=${JOBS:-$(nproc)}

# Must match devices/google/comet/boot.toml (the device test checks it).
CMDLINE="console=tty0 rootwait ro init=/init pmos_rootfsopts=defaults pmos_root_uuid=$ROOT_UUID pmos_boot_uuid=$BOOT_UUID ostree=$OSTREE_PATH androidboot.slot_suffix=_a enforcing=0 clk_ignore_unused pd_ignore_unused exynosdrm.oneshot=0 systemd.setenv=WLR_RENDERER=pixman systemd.journald.forward_to_kmsg=1 systemd.mask=systemd-pstore.service printk.devkmsg=on ignore_loglevel reboot=warm panic=10 systemd.mask=greenboot-healthcheck.service systemd.mask=greenboot-set-rollback-trigger.service systemd.mask=luma-update-boot.service systemd.mask=systemd-suspend.service systemd.mask=suspend.target systemd.mask=sleep.target luma.fold_gpu=1 luma.diag_restart=0"

[ ! -e "$OUT" ] || { echo "refusing to replace $OUT"; exit 1; }
for f in "$MKBOOTIMG" "$AVBTOOL" "$AVB_KEY" "$INITRAMFS/init"; do [ -f "$f" ] || { echo "missing $f"; exit 1; }; done
W=${WORK:-$(mktemp -d)}
rm -rf "$W"; mkdir -p "$W" "$CACHE"
trap 'rm -rf "$W"' EXIT

# Firmware built into the kernel, checked against the lock
python3 -I - "$LOCK" "$MALI_FIRMWARE_DIR" <<'PY'
import hashlib, json, pathlib, sys
lock = json.loads(pathlib.Path(sys.argv[1]).read_text())
for name, row in lock["firmware"].items():
    got = hashlib.sha256((pathlib.Path(sys.argv[2]) / name).read_bytes()).hexdigest()
    if got != row["sha256"]: raise SystemExit(f"{name}: sha256 {got}, want {row['sha256']}")
PY

# Kernel source: the pinned archive as a git tree (the release gets the "+"
# of an untagged tree), then the patch series in lock order
python3 -I - "$LOCK" "$CACHE" "$ROOT" "$W/patches.list" <<'PY'
import hashlib, json, pathlib, sys, urllib.request
lock, cache, root = json.loads(pathlib.Path(sys.argv[1]).read_text()), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
src = lock["sources"]["linux"]
archive = cache / src["name"]
if not archive.exists():
    part = archive.with_suffix(".part")
    with urllib.request.urlopen(src["url"], timeout=300) as r, part.open("wb") as f:
        while chunk := r.read(1 << 20): f.write(chunk)
    part.rename(archive)
h = hashlib.sha256()
with archive.open("rb") as f:
    while chunk := f.read(1 << 20): h.update(chunk)
if h.hexdigest() != src["sha256"]: raise SystemExit(f"{archive}: sha256 mismatch")
with open(sys.argv[4], "w") as out:
    for row in lock["patches"]:
        p = root / row["path"]
        if hashlib.sha256(p.read_bytes()).hexdigest() != row["sha256"]: raise SystemExit(f"{p}: sha256 mismatch")
        out.write(f"{p}\n")
PY
mkdir -p "$W/src"
tar -xzf "$CACHE/$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["sources"]["linux"]["name"])' "$LOCK")" -C "$W/src" --strip-components=1
G="git -C $W/src -c user.name=luma -c user.email=luma@localhost"
$G init -q; $G add -A -f; $G commit -q -m base --date=@0
xargs $G am -q --committer-date-is-author-date <"$W/patches.list"
COMMIT=$($G rev-parse HEAD)

# Initramfs: the given tree as is. The bootloader writes a 0xa00-byte table
# at Image offset 0x1800000 after loading the kernel; the kernel keeps
# CONFIG_LUMA_FW_HOLE_START..END of its Image empty for it (checked below).
cp -a "$INITRAMFS" "$W/initramfs-root"

export SOURCE_DATE_EPOCH=$($G show -s --format=%ct HEAD)
export KBUILD_BUILD_TIMESTAMP="@$SOURCE_DATE_EPOCH" KBUILD_BUILD_USER=luma KBUILD_BUILD_HOST=comet-builder KBUILD_BUILD_VERSION=1
M="make -C $W/src O=$W/build ARCH=arm64 LLVM=1"
$M -s zumapro_defconfig
cat "$FRAGMENT" >>"$W/build/.config"
C="$W/src/scripts/config --file $W/build/.config"
$C --set-str CMDLINE "$CMDLINE" --set-str INITRAMFS_SOURCE "$W/initramfs-root" \
   --set-str EXTRA_FIRMWARE_DIR "$(cd "$MALI_FIRMWARE_DIR" && pwd)"
$M -s olddefconfig
# The resolved config must be the reference one, apart from the three
# build-specific strings
python3 -I - "$W/build/.config" "$REFERENCE" <<'PY'
import re, sys
skip = re.compile(r"^CONFIG_(CMDLINE|INITRAMFS_SOURCE|EXTRA_FIRMWARE_DIR)=")
def load(p): return [l for l in open(p).read().splitlines() if not skip.match(l)]
a, b = load(sys.argv[1]), load(sys.argv[2])
if a != b:
    import difflib
    print("\n".join(difflib.unified_diff(b, a, "reference", "built", lineterm="", n=0)))
    raise SystemExit("resolved .config differs from config/kernel/pixel9fold.config")
PY

$M -s -j"$JOBS" dtbs
mkdir -p "$W/build/luma"
cp "$W/build/arch/arm64/boot/dts/exynos/google/$DTB.dtb" "$W/build/luma/embedded.dtb"

# Modules go to /luma-fold/modules, in load order, for hook 83 to load.
# modpost needs vmlinux: link once, build the modules, then relink with them
# in the initramfs.
$M -s -j"$JOBS" Image
$M -s -j"$JOBS" modules
order="$W/modorder"; : >"$order"
add_mod() {
  local ko; ko=$(find "$W/build" -name "$1.ko" -print -quit)
  [ -n "$ko" ] || { echo "module $1 not built"; exit 1; }
  grep -qx "$ko" "$order" && return 0
  local d; for d in $(modinfo -F depends "$ko" | tr , ' '); do add_mod "$d"; done
  echo "$ko" >>"$order"
}
for m in $MODULES; do add_mod "$m"; done
mkdir -p "$W/initramfs-root/luma-fold/modules"
: >"$W/load-order"
while read -r ko; do
  install -m 644 "$ko" "$W/initramfs-root/luma-fold/modules/"
  llvm-strip --strip-debug "$W/initramfs-root/luma-fold/modules/$(basename "$ko")"
  basename "$ko" >>"$W/load-order"
done <"$order"
install -m 644 "$W/load-order" "$W/initramfs-root/luma-fold/modules/load-order"
echo "modules: $(tr '\n' ' ' <"$W/load-order")"
$M -s -j"$JOBS" Image
IMG=$W/build/arch/arm64/boot/Image

# The bootloader write zone must sit inside the linker hole: nothing of the
# kernel, its init data or the initramfs may start or end inside it.
python3 -I - "$IMG" "$W/build/System.map" "$FW_ZONE" <<'PY'
import sys
img_len = len(open(sys.argv[1], "rb").read())
syms = {}
for l in open(sys.argv[2]):
    a, _, n = l.split()[:3]
    syms.setdefault(n, int(a, 16))
lo, hi = (int(x, 16) for x in sys.argv[3].split(":"))
t = syms["_text"]; s = syms["__luma_fw_hole_start"] - t; e = syms["__luma_fw_hole_end"] - t
print(f"fw hole at Image {s:#x}..{e:#x}, zone {lo:#x}..{hi:#x}, text+rodata headroom {lo - s:#x}")
if not (s <= lo and e >= hi): raise SystemExit("fw hole does not cover the zone")
inside = sorted(n for n, a in syms.items() if s < a - t < e)
if inside: raise SystemExit(f"symbols inside the fw hole: {inside[:5]}")
if img_len < hi: raise SystemExit("Image ends inside the zone")
PY
python3 -I - "$IMG" "$W/build/luma/embedded.dtb" <<'PY'
import sys, pathlib
img = pathlib.Path(sys.argv[1]).read_bytes()
dtb = pathlib.Path(sys.argv[2]).read_bytes()
if img.count(dtb) != 1: raise SystemExit("embedded DTB must appear exactly once")
if img[:2] != b"MZ" or img[0x38:0x3c] != b"ARMd": raise SystemExit("arm64 PE/EFI header missing")
# The embedded DTB must sit inside one 2 MiB block of the Image: images where
# it straddled a 2 MiB boundary died in early boot before ramoops.
o = img.find(dtb)
if len(dtb) > 0x40000: raise SystemExit(f"embedded DTB is {len(dtb):#x} bytes, over 256 KiB")
if o // 0x200000 != (o + len(dtb) - 1) // 0x200000:
    raise SystemExit(f"embedded DTB at {o:#x}..{o + len(dtb):#x} straddles a 2 MiB boundary of the Image")
print(f"embedded DTB at {o:#x}, {len(dtb):#x} bytes, inside one 2 MiB block")
PY
fdtget -t s "$W/build/luma/embedded.dtb" / model | grep -q '^Pixel 9 Pro Fold'

mkdir -p "$W/pkg"
lz4 -q -l -12 -f "$IMG" "$W/pkg/Image.lz4"
python3 "$MKBOOTIMG" --header_version 4 --kernel "$W/pkg/Image.lz4" --output "$W/pkg/boot.img"
python3 -I "$AVBTOOL" add_hash_footer --image "$W/pkg/boot.img" --partition_name boot \
  --partition_size $BOOT_BYTES --key "$AVB_KEY" --algorithm SHA256_RSA4096 \
  --rollback_index $ROLLBACK --internal_release_string 'Luma comet; AOSP test key'
python3 -I "$AVBTOOL" verify_image --image "$W/pkg/boot.img" --key "$AVB_KEY" >/dev/null
[ "$(stat -c %s "$W/pkg/boot.img")" = $BOOT_BYTES ]

mkdir -p "$OUT"
cp "$W/pkg/boot.img" "$W/build/.config" "$OUT/"
cp "$W/build/luma/embedded.dtb" "$OUT/$DTB.dtb"
{
  echo "LINUX_COMMIT=$COMMIT"
  echo "KERNEL_RELEASE=$(cat "$W/build/include/config/kernel.release")"
  echo "EMBEDDED_DTB=$DTB"
  echo "CMDLINE=$CMDLINE"
  echo "IMAGE_BYTES=$(stat -c %s "$IMG")"
  echo "IMAGE_SHA256=$(sha256sum "$IMG" | cut -d' ' -f1)"
  echo "DTB_SHA256=$(sha256sum "$OUT/$DTB.dtb" | cut -d' ' -f1)"
  echo "BOOT_SHA256=$(sha256sum "$OUT/boot.img" | cut -d' ' -f1)"
  echo "MODULES=$(tr '\n' ' ' <"$W/load-order")"
} >"$OUT/manifest.env"
cat "$OUT/manifest.env"
