#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build the Raspberry Pi 5 SD card image from a Luma aarch64 installer ISO.
#
#   MBR, disk identifier 0x4c554d41
#   p1  FAT32 "LUMA_PI5", 512 MiB at 1 MiB: config.txt, Fedora U-Boot, the
#       installer kernel's upstream device trees (CMA 256 MiB), the ISO's GRUB
#       as EFI/BOOT/BOOTAA64.EFI, the card menu (EFI/BOOT/grub.cfg, grubenv),
#       the installer kernel and initramfs
#   p2  the ISO, byte for byte
#
# Card sources: src/luma-rpi5-platform/card. Device profile:
# devices/raspberrypi/rpi5.
#
# usage: build-rpi5-sd-image.sh --iso FILE --output DIR [--uboot-rpm FILE] [--head-only]
#
# Runs as root on Linux: the ISO is loop-mounted read-only. --head-only writes
# CARD.head.img (MBR + p1) instead of the whole image; the card image is that
# file followed by the ISO (cat CARD.head.img ISO > /dev/sdX), and the
# checksum printed is the checksum of that concatenation.
#
# ISOs whose installer runtime predates the ARM module set
# (scripts/install/atlas-iso/prepare-lorax-boot.py --list-arm-modules) get an
# updates.img with those modules from the ISO's own OS payload.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
card_src="$repo_root/src/luma-rpi5-platform/card"

# Fedora U-Boot, rpi_arm64 target.
uboot_nevra=uboot-images-armv8-2026.10-1.fc45.noarch
uboot_url=https://kojipkgs.fedoraproject.org/packages/uboot-tools/2026.10/1.fc45/noarch/$uboot_nevra.rpm
uboot_rpm_sha256=46de40e4d35f2a648c2a307c50d35f3c0c5dc1966bf9073f68a9fda73a300845
uboot_bin_sha256=b552bd48c1745f565d424a5df7846283fbe66a244ea4e7c6f8a42c4e80a5ed55

# Installer arguments; the profile's cmdline.args (boot.toml). Never console=.
installer_args="clk_ignore_unused pd_ignore_unused"
fat_label=LUMA_PI5
fat_mib=512
disk_id=0x4c554d41
# 256 MiB, inside the trees' own 0-1 GiB alloc-ranges: cma=256M@0M-1024M.
cma_size="0 10000000"
cma_ranges="0 0 0 40000000"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
usage() { sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2; }

iso="" output="" uboot_rpm="" head_only=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --iso) iso=${2:?}; shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    --uboot-rpm) uboot_rpm=${2:?}; shift 2 ;;
    --head-only) head_only=1; shift ;;
    *) usage ;;
  esac
done
[ -n "$iso" ] && [ -n "$output" ] || usage
[ "$(uname -s)" = Linux ] || fail "build the card image on Linux"
[ "$(id -u)" = 0 ] || fail "run as root: the ISO is loop-mounted read-only"
for tool in blkid cpio curl depmod fdtget fdtput fsck.fat grub2-editenv gzip mcopy mkfs.vfat \
            mount ostree python3 rpm2cpio sfdisk sha256sum unsquashfs; do
  command -v "$tool" >/dev/null 2>&1 || fail "required tool is missing: $tool"
done

iso=$(realpath -e "$iso")
iso_bytes=$(stat -c %s "$iso")
[ $((iso_bytes % 512)) = 0 ] || fail "ISO size is not a whole number of sectors"
iso_name=$(basename "$iso" .iso)
volid=$(blkid -p -o value -s LABEL "$iso")
[[ "$volid" =~ ^[A-Za-z0-9._-]+$ ]] || fail "ISO volume ID '$volid' is unusable in inst.stage2=hd:LABEL="
case "$iso_name" in luma-*) image_name="luma-rpi5-${iso_name#luma-}" ;; *) image_name="luma-rpi5-$iso_name" ;; esac
mkdir -p "$output"
output=$(realpath -e "$output")
epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}

work=$(mktemp -d "$output/.rpi5-card.XXXXXX")
cleanup() {
  mountpoint -q "$work/iso" 2>/dev/null && umount "$work/iso"
  rm -rf "$work"
}
trap cleanup EXIT INT TERM
mkdir "$work/iso"
mount -o loop,ro "$iso" "$work/iso"
for file in images/pxeboot/vmlinuz images/pxeboot/initrd.img images/install.img \
            EFI/BOOT/grubaa64.efi EFI/BOOT/fonts/unicode.pf2; do
  [ -f "$work/iso/$file" ] || fail "not a Luma aarch64 installer ISO: $file is missing"
done

echo "ISO: $iso_name ($volid, $iso_bytes bytes)"
iso_sha256=$(sha256sum "$iso" | cut -d' ' -f1)

# The installer runtime names exactly one kernel; its device trees go on the card.
unsquashfs -no-progress -l "$work/iso/images/install.img" >"$work/stage2.lst"
release=$(grep -oE 'usr/lib/modules/[^/]+/kernel/' "$work/stage2.lst" | sort -u | cut -d/ -f4)
[ -n "$release" ] && [ "$(printf '%s\n' "$release" | wc -l)" = 1 ] || fail "installer runtime kernel is ambiguous: $release"
echo "Installer kernel: $release"
dtbs="usr/lib/modules/$release/dtb/broadcom"
unsquashfs -no-progress -q -d "$work/stage2" "$work/iso/images/install.img" \
  "$dtbs/bcm2712-rpi-5-b.dtb" "$dtbs/bcm2712-d-rpi-5-b.dtb" >/dev/null

fat="$work/fat"
mkdir -p "$fat/EFI/BOOT/fonts" "$fat/images/pxeboot"
# Board firmware loads bcm2712-rpi-5-b.dtb on C1 and bcm2712d0-rpi-5-b.dtb on
# D0; upstream calls the D0 tree bcm2712-d-rpi-5-b.dtb.
install -m 0644 "$work/stage2/$dtbs/bcm2712-rpi-5-b.dtb" "$fat/bcm2712-rpi-5-b.dtb"
install -m 0644 "$work/stage2/$dtbs/bcm2712-d-rpi-5-b.dtb" "$fat/bcm2712d0-rpi-5-b.dtb"
for dtb in "$fat/bcm2712-rpi-5-b.dtb" "$fat/bcm2712d0-rpi-5-b.dtb"; do
  node=/reserved-memory/linux,cma
  [ "$(fdtget "$dtb" "$node" compatible)" = shared-dma-pool ] || fail "$dtb: no CMA pool"
  [ "$(fdtget -t x "$dtb" "$node" alloc-ranges)" = "$cma_ranges" ] || fail "$dtb: unexpected CMA range"
  # shellcheck disable=SC2086
  fdtput -t x "$dtb" "$node" size $cma_size
  [ "$(fdtget -t x "$dtb" "$node" size)" = "$cma_size" ] || fail "$dtb: CMA size not applied"
done

# Display and USB modules the Pi needs in the installer runtime.
mapfile -t modules < <(python3 "$repo_root/scripts/install/atlas-iso/prepare-lorax-boot.py" --list-arm-modules)
[ "${#modules[@]}" -gt 0 ] || fail "no ARM runtime module list"
missing=()
for module in "${modules[@]}"; do
  grep -q "usr/lib/modules/$release/$module" "$work/stage2.lst" || missing+=("$module")
done
compat_updates=no
if [ "${#missing[@]}" -gt 0 ]; then
  echo "Installer runtime lacks ${#missing[@]} Pi modules; adding updates.img from the ISO payload"
  repo="$work/iso/luma/repo"
  ref=$(ostree --repo="$repo" refs | grep -vx ostree-metadata || true)
  [ -n "$ref" ] && [ "$(printf '%s\n' "$ref" | wc -l)" = 1 ] || fail "ISO payload ref is ambiguous: $ref"
  ostree --repo="$repo" ls "$ref" "/usr/lib/modules/$release" >/dev/null 2>&1 ||
    fail "the ISO payload does not carry the installer kernel $release"
  # depmod over the runtime's own modules plus the added ones, so the module
  # index matches what is really there.
  unsquashfs -no-progress -q -d "$work/modroot" "$work/iso/images/install.img" "usr/lib/modules/$release" >/dev/null
  ln -s usr/lib "$work/modroot/lib"
  updates="$work/updates/usr/lib/modules/$release"
  for module in "${missing[@]}"; do
    install -d "$updates/$(dirname "$module")" "$work/modroot/usr/lib/modules/$release/$(dirname "$module")"
    ostree --repo="$repo" cat "$ref" "/usr/lib/modules/$release/$module.xz" >"$updates/$module.xz"
    cp "$updates/$module.xz" "$work/modroot/usr/lib/modules/$release/$module.xz"
  done
  depmod -b "$work/modroot" "$release"
  for index in "$work/modroot/usr/lib/modules/$release"/modules.*; do
    case "${index##*/}" in modules.order|modules.builtin|modules.builtin.modinfo) continue ;; esac
    install -m 0644 "$index" "$updates/"
  done
  # Fixed modes and times: the archive must not depend on the build host's
  # umask, setgid directories or default ACLs.
  find "$work/updates" -type d -exec chmod 0755 {} +
  find "$work/updates" -type f -exec chmod 0644 {} +
  find "$work/updates" -exec touch -h -d "@$epoch" {} +
  (cd "$work/updates" && find . -mindepth 1 | LC_ALL=C sort |
     cpio -o -H newc -R 0:0 --quiet --reproducible) | gzip -n -9 >"$fat/updates.img"
  installer_args="$installer_args inst.updates=hd:LABEL=$fat_label:/updates.img"
  compat_updates="yes (${missing[*]})"
fi

# Fedora U-Boot.
if [ -n "$uboot_rpm" ]; then
  cp "$uboot_rpm" "$work/uboot.rpm"
else
  curl -fsSL --retry 3 -o "$work/uboot.rpm" "$uboot_url"
fi
echo "$uboot_rpm_sha256  $work/uboot.rpm" | sha256sum -c --quiet - || fail "U-Boot RPM differs from $uboot_nevra"
mkdir "$work/uboot"
(cd "$work/uboot" && rpm2cpio ../uboot.rpm | cpio -idm --quiet ./usr/share/uboot/rpi_arm64/u-boot.bin)
echo "$uboot_bin_sha256  $work/uboot/usr/share/uboot/rpi_arm64/u-boot.bin" | sha256sum -c --quiet - ||
  fail "rpi_arm64/u-boot.bin differs from the pinned build"
install -m 0644 "$work/uboot/usr/share/uboot/rpi_arm64/u-boot.bin" "$fat/u-boot.bin"

# GRUB from the ISO (not shim: U-Boot has no Secure Boot and shim's fallback
# resets the board), the card menu and its one-shot environment.
install -m 0644 "$work/iso/EFI/BOOT/grubaa64.efi" "$fat/EFI/BOOT/BOOTAA64.EFI"
install -m 0644 "$work/iso/EFI/BOOT/fonts/unicode.pf2" "$fat/EFI/BOOT/fonts/unicode.pf2"
sed -e "s|@ISO_VOLID@|$volid|g" -e "s|@INSTALLER_ARGS@|$installer_args|g" -e "s|@ISO_NAME@|$iso_name|g" \
  "$card_src/grub.cfg.in" >"$fat/EFI/BOOT/grub.cfg"
if grep -q '@[A-Z_]*@' "$fat/EFI/BOOT/grub.cfg"; then fail "unresolved placeholder in grub.cfg"; fi
if command -v grub2-script-check >/dev/null 2>&1; then
  check=$(grub2-script-check "$fat/EFI/BOOT/grub.cfg" 2>&1) || fail "grub.cfg: $check"
  case "${check,,}" in *error*) fail "grub.cfg: $check" ;; esac
fi
grub2-editenv "$fat/EFI/BOOT/grubenv" create
install -m 0644 "$work/iso/images/pxeboot/vmlinuz" "$fat/images/pxeboot/vmlinuz"
install -m 0644 "$work/iso/images/pxeboot/initrd.img" "$fat/images/pxeboot/initrd.img"
install -m 0644 "$card_src/config.txt" "$fat/config.txt"
sed -e "s|@ISO_NAME@|$iso_name|g" "$card_src/README.txt.in" >"$fat/README.txt"
find "$fat" -exec touch -h -d "@$epoch" {} +

# Partition 1.
fat_img="$work/p1.img"
truncate -s "${fat_mib}M" "$fat_img"
mkfs.vfat -F 32 -n "$fat_label" --invariant "$fat_img" >/dev/null
(cd "$fat" && MTOOLS_SKIP_CHECK=1 mcopy -s -m -i "$fat_img" ./* ::/)
fsck.fat -n "$fat_img" >/dev/null || fail "partition 1 failed fsck"
mkdir "$work/readback"
MTOOLS_SKIP_CHECK=1 mcopy -s -m -n -i "$fat_img" '::/*' "$work/readback/"
diff -r "$fat" "$work/readback" >/dev/null || fail "partition 1 does not read back as staged"

# MBR: write the table on a sparse file of the full card size (sfdisk refuses
# a partition past the end of the file), then keep its first sector.
p1_start=2048
p1_sectors=$((fat_mib * 2048))
p2_start=$((p1_start + p1_sectors))
iso_sectors=$((iso_bytes / 512))
total_bytes=$(((p2_start + iso_sectors) * 512))
truncate -s "$total_bytes" "$work/mbr.img"
sfdisk --quiet --no-reread --no-tell-kernel "$work/mbr.img" <<EOF
label: dos
label-id: $disk_id
unit: sectors
start=$p1_start, size=$p1_sectors, type=c, bootable
start=$p2_start, size=$iso_sectors, type=83
EOF
head_img="$work/head.img"
truncate -s $((p2_start * 512)) "$head_img"
dd if="$work/mbr.img" of="$head_img" bs=512 count=1 conv=notrunc status=none
dd if="$fat_img" of="$head_img" bs=1M seek=1 conv=notrunc status=none
rm -f "$work/mbr.img"
[ "$(blkid -p -O $((p1_start * 512)) -o value -s LABEL "$head_img")" = "$fat_label" ] ||
  fail "partition 1 is not at its table offset"
table=$(sfdisk --dump "$head_img" 2>/dev/null)
grep -q "start= *$p1_start, size= *$p1_sectors, type=c, bootable" <<<"$table" || fail "partition 1 entry: $table"
grep -q "start= *$p2_start, size= *$iso_sectors, type=83" <<<"$table" || fail "partition 2 entry: $table"

if [ "$head_only" = 1 ]; then
  artifact="$image_name.head.img"
  install -m 0644 "$head_img" "$output/$artifact"
  image_sha256=$(cat "$head_img" "$iso" | sha256sum | cut -d' ' -f1)
else
  artifact="$image_name.img"
  image_sha256=$(cat "$head_img" "$iso" | tee "$output/$artifact.partial" | sha256sum | cut -d' ' -f1)
  p2_sha256=$(dd if="$output/$artifact.partial" bs=1M skip=$((p2_start / 2048)) status=none | sha256sum | cut -d' ' -f1)
  [ "$p2_sha256" = "$iso_sha256" ] || fail "partition 2 differs from the ISO"
  mv "$output/$artifact.partial" "$output/$artifact"
fi
printf '%s  %s\n' "$image_sha256" "$image_name.img" >"$output/$image_name.img.sha256"
install -m 0644 "$fat/README.txt" "$output/$image_name.README.txt"
{
  echo "image $image_name.img"
  echo "image_bytes $total_bytes"
  echo "image_sha256 $image_sha256"
  echo "layout mbr label-id=$disk_id p1=$p1_start+$p1_sectors(fat32,$fat_label) p2=$p2_start+$iso_sectors(iso)"
  echo "iso $iso_name.iso $iso_bytes $iso_sha256"
  echo "iso_volume_id $volid"
  echo "kernel $release"
  echo "uboot $uboot_nevra $uboot_rpm_sha256"
  echo "compat_updates_img $compat_updates"
  echo "installer_args $installer_args"
  echo "partition1:"
  (cd "$fat" && find . -type f | LC_ALL=C sort | xargs sha256sum | sed 's/^/  /')
} >"$output/$image_name.manifest.txt"

echo "Card image: $output/$artifact"
[ "$head_only" = 1 ] && echo "  (write it, then the ISO: cat $artifact $iso_name.iso > /dev/sdX)"
echo "  $total_bytes bytes, sha256 $image_sha256"
