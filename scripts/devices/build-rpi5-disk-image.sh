#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build the preinstalled Raspberry Pi 5 card image (luma-rpi5-<channel>-
# <version>-aarch64.img.xz) from a Luma aarch64 installer ISO: the ISO's own
# OSTree payload, deployed the way the installer deploys it, plus the Pi 5 boot
# partition. Write it to a microSD card and boot; there is no installer.
#
#   MBR, disk identifier 0x4c554d42
#   p1  FAT32 "LUMA_PI5", 512 MiB at 1 MiB, mounted at /boot/efi: config.txt,
#       Fedora U-Boot, the deployed kernel's device trees (CMA 256 MiB; HDMI
#       audio merged when the payload has luma-rpi5-kmod), GRUB as
#       EFI/BOOT/BOOTAA64.EFI with the card menu, EFI/fedora (the system's
#       GRUB stub and bootuuid.cfg)
#   p2  ext4 "boot", 1 GiB: /boot (BLS entries, kernels, grub2/)
#   p3  btrfs "luma", the rest: subvolumes root (the OSTree physical root)
#       and home (/home); grows to the card on the first boot
#
# The deployment's origin is <remote>:<ref> from the medium's media.env, the
# remote definition is the image's own (mirror list, GPG on), and the mirror
# list holds the public repository URL: the system updates online like any
# other Luma install. No account exists and root is locked: the first boot
# starts Luma's first-boot setup. A medium carrying a preview credential is
# refused.
#
# usage: build-rpi5-disk-image.sh --iso FILE --output DIR [--uboot-rpm FILE] [--size GIB] [--no-xz]
#
# Runs as root on Linux (loop devices). Nothing from the payload is executed:
# every step uses the build host's own tools, so an x86_64 host can build it.
set -euo pipefail
export TZ=UTC LC_ALL=C MTOOLS_SKIP_CHECK=1

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
card_src="$repo_root/src/luma-rpi5-platform/card"

# Fedora U-Boot, rpi_arm64 target (the same pin as build-rpi5-sd-image.sh).
uboot_nevra=uboot-images-armv8-2026.10-1.fc45.noarch
uboot_url=https://kojipkgs.fedoraproject.org/packages/uboot-tools/2026.10/1.fc45/noarch/$uboot_nevra.rpm
uboot_rpm_sha256=46de40e4d35f2a648c2a307c50d35f3c0c5dc1966bf9073f68a9fda73a300845
uboot_bin_sha256=b552bd48c1745f565d424a5df7846283fbe66a244ea4e7c6f8a42c4e80a5ed55

# The profile's cmdline.args (devices/raspberrypi/rpi5/boot.toml). Never console=.
board_args="clk_ignore_unused pd_ignore_unused"
fat_label=LUMA_PI5
fat_mib=512
boot_mib=1024
disk_id=0x4c554d42

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
usage() { sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2; }

iso="" output="" uboot_rpm="" size_gib=12 compress=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --iso) iso=${2:?}; shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    --uboot-rpm) uboot_rpm=${2:?}; shift 2 ;;
    --size) size_gib=${2:?}; shift 2 ;;
    --no-xz) compress=0; shift ;;
    *) usage ;;
  esac
done
[ -n "$iso" ] && [ -n "$output" ] || usage
[[ "$size_gib" =~ ^[0-9]+$ ]] && [ "$size_gib" -ge 8 ] || fail "--size must be at least 8 (GiB)"
[ "$(uname -s)" = Linux ] || fail "build the card image on Linux"
[ "$(id -u)" = 0 ] || fail "run as root: the image is assembled on loop devices"
for tool in blkid btrfs cpio curl dtc e2fsck fdtget fdtoverlay fdtput findmnt fsck.fat grub2-editenv losetup \
            mkfs.btrfs mkfs.ext4 mkfs.vfat mount ostree python3 rpm2cpio setfiles sfdisk sha256sum \
            unsquashfs xz; do
  command -v "$tool" >/dev/null 2>&1 || fail "required tool is missing: $tool"
done
ostree --version | grep -qx '  - composefs' || fail "the host's ostree has no composefs support"

iso=$(realpath -e "$iso")
iso_name=$(basename "$iso" .iso)
case "$iso_name" in luma-*) image_name="luma-rpi5-${iso_name#luma-}" ;; *) image_name="luma-rpi5-$iso_name" ;; esac
case "$image_name" in *-aarch64) ;; *) image_name="$image_name-aarch64" ;; esac
mkdir -p "$output"
output=$(realpath -e "$output")
epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}

work=$(mktemp -d "$output/.rpi5-disk.XXXXXX")
loop=""
cleanup() {
  set +e
  for m in "$work/sysroot/ostree/deploy/"*/deploy/*/var/home "$work/sysroot/ostree/deploy/"*/deploy/*/var \
           "$work/sysroot/boot/efi" "$work/sysroot/boot" "$work/sysroot" "$work/top" "$work/iso"; do
    mountpoint -q "$m" 2>/dev/null && umount "$m"
  done
  [ -n "$loop" ] && losetup -d "$loop"
  rm -rf "$work"
}
trap cleanup EXIT INT TERM
mkdir "$work/iso" "$work/top" "$work/sysroot"
mount -o loop,ro "$iso" "$work/iso"
for file in images/install.img EFI/BOOT/fonts/unicode.pf2; do
  [ -f "$work/iso/$file" ] || fail "not a Luma aarch64 installer ISO: $file is missing"
done

# The installer's medium description and release key, from its runtime.
atlas=usr/share/luma-installer-atlas
unsquashfs -no-progress -q -d "$work/stage2" "$work/iso/images/install.img" \
  "$atlas/media.env" "$atlas/luma-release.gpg" "$atlas/preview-credential" >/dev/null 2>&1 || true
[ -f "$work/stage2/$atlas/media.env" ] && [ -f "$work/stage2/$atlas/luma-release.gpg" ] ||
  fail "the installer runtime has no $atlas/media.env or luma-release.gpg"
[ ! -e "$work/stage2/$atlas/preview-credential" ] ||
  fail "this medium carries a preview credential; build public card images from public media only"
declare -A media=()
while IFS='=' read -r key value; do
  case "$key" in ''|'#'*) continue ;; esac
  media[$key]=$value
done <"$work/stage2/$atlas/media.env"
for key in channel channels remote https_url mirrorlist ref stateroot medium_repo target_key collection_id; do
  [ -n "${media[$key]:-}" ] || fail "media.env has no $key"
done
[ -z "${media[test_payload_url]:-}" ] || fail "this medium installs a test payload"
channel=${media[channel]} remote=${media[remote]} ref=${media[ref]} stateroot=${media[stateroot]}
[[ " ${media[channels]} " == *" $channel "* ]] || fail "unknown channel $channel"
[[ "$remote" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fail "invalid remote name $remote"
[[ "$stateroot" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fail "invalid stateroot $stateroot"
[[ "${media[https_url]}" =~ ^https://[^[:space:]]+$ ]] || fail "the update repository must use HTTPS"
[[ "${media[mirrorlist]}" =~ ^/etc/luma/[A-Za-z0-9._-]+$ ]] || fail "invalid mirror list path"
[[ "${media[target_key]}" =~ ^/etc/[A-Za-z0-9._/-]+$ ]] || fail "invalid release key path"
key="$work/stage2/$atlas/luma-release.gpg"
medium="$work/iso/${media[medium_repo]}"
commit=$(ostree --repo="$medium" rev-parse "$ref") || fail "the medium does not carry $ref"
version=$(ostree --repo="$medium" show --print-metadata-key=version "$commit" | tr -d "'")
echo "Payload: $remote:$ref $commit ($version)"

# The deployed kernel and its device trees.
release=$(ostree --repo="$medium" ls "$commit" /usr/lib/modules | awk '$1 ~ /^d/ { sub(".*/", "", $5); print $5 }' | grep -v '^modules$')
[ -n "$release" ] && [ "$(printf '%s\n' "$release" | wc -l)" = 1 ] || fail "payload kernel is ambiguous: $release"
echo "Kernel: $release"
ostree --repo="$medium" checkout -U --subpath="/usr/lib/modules/$release/dtb/broadcom" "$commit" "$work/dtbs"
hdmi_audio=no
if ostree --repo="$medium" ls "$commit" "/usr/lib/modules/$release/updates/luma-rpi5/bcm2712-dma.ko.xz" >/dev/null 2>&1; then
  hdmi_audio=yes
else
  echo "warning: the payload has no luma-rpi5-kmod for $release; HDMI audio stays off" >&2
fi

# Kernel arguments: root, the board's, and what the release declares in
# /usr/lib/bootc/kargs.d (as the installer adds them).
kargs_d=$(mktemp -d "$work/kargs.XXXXXX")
if ostree --repo="$medium" ls "$commit" /usr/lib/bootc/kargs.d >/dev/null 2>&1; then
  ostree --repo="$medium" checkout -U --subpath=/usr/lib/bootc/kargs.d "$commit" "$kargs_d/d"
fi
release_kargs=$(python3 - "$kargs_d/d" <<'PY'
import glob, os, sys, tomllib
out = []
for path in sorted(glob.glob(os.path.join(sys.argv[1], "*.toml"))):
    data = tomllib.load(open(path, "rb"))
    arches = data.get("match-architectures")
    if arches is not None and "aarch64" not in arches:
        continue
    for karg in data.get("kargs", []):
        if not karg or any(c.isspace() for c in karg):
            sys.exit(f"{os.path.basename(path)}: unusable kernel argument {karg!r}")
        if karg not in out:
            out.append(karg)
print(" ".join(out))
PY
)

# Filesystem identities, derived from the payload so a rebuild is the same.
ids=$(python3 -c 'import sys, uuid; n = uuid.uuid5(uuid.NAMESPACE_URL, "luma-rpi5:" + sys.argv[1]); print(uuid.uuid5(n, "boot"), uuid.uuid5(n, "root"), uuid.uuid5(n, "esp").hex[:8].upper())' "$commit")
read -r boot_uuid root_uuid esp_id <<<"$ids"
esp_uuid="${esp_id:0:4}-${esp_id:4:4}"

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

# The disk.
img="$output/$image_name.img.partial"
rm -f "$img"
truncate -s "${size_gib}G" "$img"
p1_start=2048
p1_sectors=$((fat_mib * 2048))
p2_start=$((p1_start + p1_sectors))
p2_sectors=$((boot_mib * 2048))
p3_start=$((p2_start + p2_sectors))
sfdisk --quiet "$img" <<EOF
label: dos
label-id: $disk_id
unit: sectors
start=$p1_start, size=$p1_sectors, type=c, bootable
start=$p2_start, size=$p2_sectors, type=83
start=$p3_start, type=83
EOF
loop=$(losetup --find --show --partscan "$img")
udevadm settle 2>/dev/null || true
for n in 1 2 3; do [ -b "${loop}p$n" ] || fail "${loop}p$n did not appear"; done
mkfs.vfat -F 32 -n "$fat_label" -i "$esp_id" --invariant "${loop}p1" >/dev/null
mkfs.ext4 -q -L boot -U "$boot_uuid" -E "hash_seed=$boot_uuid" -T default "${loop}p2"
mkfs.btrfs -q -L luma -U "$root_uuid" "${loop}p3"
mount "${loop}p3" "$work/top"
btrfs -q subvolume create "$work/top/root"
btrfs -q subvolume create "$work/top/home"
umount "$work/top"
sysroot="$work/sysroot"
mount -o subvol=root,compress=zstd:1 "${loop}p3" "$sysroot"
install -d "$sysroot/boot"
mount "${loop}p2" "$sysroot/boot"
install -d "$sysroot/boot/efi"
mount -o umask=0077,shortname=winnt "${loop}p1" "$sysroot/boot/efi"

# The OSTree deployment, as the installer's ostreesetup makes it: physical
# root, stateroot, the payload pulled from the medium with its signature
# checked against the release key, and one deployment with origin
# <remote>:<ref>. The remote is then defined only by the image's own
# /etc/ostree/remotes.d, so it is removed from the physical repository.
ostree admin init-fs --sysroot="$sysroot" "$sysroot" >/dev/null
repo="$sysroot/ostree/repo"
ostree config --repo="$repo" set sysroot.bootloader none
ostree config --repo="$repo" set sysroot.readonly true
ostree admin os-init --sysroot="$sysroot" "$stateroot" >/dev/null
ostree remote add --repo="$repo" --set=gpg-verify=true --set=gpg-verify-summary=false \
  --set=gpgkeypath="$key" "$remote" "file://$medium"
ostree pull --repo="$repo" "$remote" "$ref" >/dev/null
[ "$(ostree --repo="$repo" rev-parse "$remote:$ref")" = "$commit" ] || fail "pulled commit differs from $commit"
kargs=(root=UUID="$root_uuid" rootflags=subvol=root rw $board_args $release_kargs)
for arg in "${kargs[@]}"; do
  case "$arg" in console=*|firmware_class.path=*|selinux=0|enforcing=0|init=*|rd.break) fail "forbidden kernel argument $arg" ;; esac
done
ostree admin deploy --sysroot="$sysroot" --os="$stateroot" --no-merge \
  "${kargs[@]/#/--karg=}" "$remote:$ref" >/dev/null
ostree remote delete --repo="$repo" "$remote"
deploys=("$sysroot/ostree/deploy/$stateroot/deploy/"*.0)
[ "${#deploys[@]}" = 1 ] && [ -d "${deploys[0]}" ] || fail "expected one deployment"
deploy=${deploys[0]}
var="$sysroot/ostree/deploy/$stateroot/var"
grep -Fxq "refspec=$remote:$ref" "$deploy.origin" || fail "the deployment's origin is not $remote:$ref"

# The update path (what the installer's %post writes): the image's remote
# definition reads the mirror list; the mirror list holds the public URL; the
# channel is recorded for luma-update.
remote_conf="$deploy/etc/ostree/remotes.d/$remote.conf"
[ -f "$remote_conf" ] || fail "the image does not define the $remote remote"
grep -Fxq "url=mirrorlist=file://${media[mirrorlist]}" "$remote_conf" || fail "$remote_conf does not read ${media[mirrorlist]}"
for opt in gpg-verify gpg-verify-summary; do
  grep -Exq "$opt=(true|1|yes)" "$remote_conf" || fail "$remote_conf does not turn $opt on"
done
grep -Fxq "collection-id=${media[collection_id]}" "$remote_conf" || fail "$remote_conf has another collection id"
install -D -m 0644 "$key" "$deploy${media[target_key]}"
install -d -m 0755 "$deploy/etc/luma"
(umask 077 && printf '%s\n' "${media[https_url]}" >"$deploy${media[mirrorlist]}")
chmod 0600 "$deploy${media[mirrorlist]}"
printf 'channel=%s\n' "$channel" >"$deploy/etc/luma/update-channel.conf"

# Mounts: / comes from the kernel command line (composefs), as on installed
# systems.
cat >"$deploy/etc/fstab" <<EOF
UUID=$boot_uuid /boot ext4 defaults 1 2
UUID=$esp_uuid /boot/efi vfat umask=0077,shortname=winnt 0 2
UUID=$root_uuid /home btrfs subvol=home,compress=zstd:1 0 0
EOF
cp -a "$deploy"/etc/skel/.bash* "$var/roothome/" 2>/dev/null || true

# First boot: grow the root partition to the card.
install -m 0755 "$card_src/luma-rpi5-grow-root" "$deploy/etc/luma/rpi5-grow-root"
install -m 0644 "$card_src/luma-rpi5-grow-root.service" "$deploy/etc/systemd/system/luma-rpi5-grow-root.service"
install -d "$deploy/etc/systemd/system/multi-user.target.wants"
ln -sf /etc/systemd/system/luma-rpi5-grow-root.service \
  "$deploy/etc/systemd/system/multi-user.target.wants/luma-rpi5-grow-root.service"

# The system's GRUB configuration, as bootupd writes it with its static
# configs: /boot/grub2/grub.cfg and the ESP stub that finds /boot by UUID.
static="$deploy/usr/lib/bootupd/grub2-static"
[ -f "$static/grub-static-pre.cfg" ] && [ -f "$static/grub-static-efi.cfg" ] || fail "the payload has no bootupd static GRUB configs"
install -d -m 0700 "$sysroot/boot/grub2/fonts"
{
  cat "$static/grub-static-pre.cfg"
  for file in "$static"/configs.d/*.cfg; do
    printf '\n### BEGIN %s ###\n' "$(basename "$file")"
    cat "$file"
    printf '### END %s ###\n' "$(basename "$file")"
  done
} >"$sysroot/boot/grub2/grub.cfg"
chmod 0600 "$sysroot/boot/grub2/grub.cfg"
printf 'set BOOT_UUID="%s"\n' "$boot_uuid" >"$sysroot/boot/grub2/bootuuid.cfg"
chmod 0600 "$sysroot/boot/grub2/bootuuid.cfg"
# U-Boot routes the EFI text console to serial only; the system's menu draws on HDMI.
cat >"$sysroot/boot/grub2/console.cfg" <<'EOF'
# Raspberry Pi 5: U-Boot routes the EFI text console to serial only.
insmod all_video
insmod gfxterm
if loadfont $prefix/fonts/unicode.pf2; then terminal_output gfxterm; fi
EOF
install -m 0644 "$work/iso/EFI/BOOT/fonts/unicode.pf2" "$sysroot/boot/grub2/fonts/unicode.pf2"
: >"$sysroot/boot/grub2/.grub2-blscfg-supported"
grub2-editenv "$sysroot/boot/grub2/grubenv" create
chmod 0600 "$sysroot/boot/grub2/grubenv"

# SELinux labels for what the deployment checkout does not carry: /boot and
# the stateroot's /var (with /home), from the payload's own policy.
fc="$deploy/etc/selinux/targeted/contexts/files/file_contexts"
[ -f "$fc" ] || fail "the payload has no targeted file_contexts"
setfiles -F -r "$sysroot" "$fc" "$sysroot/boot" 2>&1 | grep -v 'Read-only\|efi' || true
install -d "$var/home"
mount --bind "$var" "$deploy/var"
mount -o subvol=home "${loop}p3" "$deploy/var/home"
setfiles -F -r "$deploy" "$fc" "$deploy/var" || fail "labelling /var failed"
umount "$deploy/var/home" "$deploy/var"

# The boot partition (also the ESP).
fat="$work/fat"
"$repo_root/scripts/devices/rpi5-board-dtbs.sh" --kernel-dtbs "$work/dtbs" \
  --hdmi-audio "$hdmi_audio" --output "$fat" >/dev/null
install -m 0644 "$card_src/config.txt" "$fat/config.txt"
install -m 0644 "$work/uboot/usr/share/uboot/rpi_arm64/u-boot.bin" "$fat/u-boot.bin"
grub_efi=$(find "$deploy/usr/lib/efi/grub2" -path '*/EFI/fedora/grubaa64.efi' | sort | tail -n 1)
[ -n "$grub_efi" ] || fail "the payload has no grubaa64.efi"
install -d "$fat/EFI/BOOT/fonts" "$fat/EFI/fedora"
# GRUB itself, not shim: U-Boot has no Secure Boot and shim's fallback
# resets the board.
install -m 0644 "$grub_efi" "$fat/EFI/BOOT/BOOTAA64.EFI"
install -m 0644 "$card_src/grub-system.cfg" "$fat/EFI/BOOT/grub.cfg"
install -m 0644 "$work/iso/EFI/BOOT/fonts/unicode.pf2" "$fat/EFI/BOOT/fonts/unicode.pf2"
grub2-editenv "$fat/EFI/BOOT/grubenv" create
install -m 0644 "$static/grub-static-efi.cfg" "$fat/EFI/fedora/grub.cfg"
printf 'set BOOT_UUID="%s"\n' "$boot_uuid" >"$fat/EFI/fedora/bootuuid.cfg"
sed -e "s|@VERSION@|$version|g" -e "s|@REF@|$remote:$ref|g" "$card_src/README-system.txt.in" >"$fat/README.txt"
find "$fat" -exec touch -h -d "@$epoch" {} +
(cd "$fat" && find . -mindepth 1 -type d | sort | while read -r d; do install -d "$sysroot/boot/efi/${d#./}"; done
 find . -type f | sort | while read -r f; do cp --preserve=timestamps "$f" "$sysroot/boot/efi/${f#./}"; done)
diff -r "$fat" "$sysroot/boot/efi" >/dev/null || fail "the boot partition does not read back as staged"

used_mib=$(df -BM --output=used "$sysroot" | tail -n 1 | tr -dc 0-9)
sync
umount "$sysroot/boot/efi" "$sysroot/boot" "$sysroot"
fsck.fat -n "${loop}p1" >/dev/null || fail "the boot partition failed fsck"
e2fsck -fn "${loop}p2" >/dev/null || fail "/boot failed fsck"
btrfs check --readonly "${loop}p3" >/dev/null 2>&1 || fail "the root filesystem failed btrfs check"
losetup -d "$loop"
loop=""

mv "$img" "$output/$image_name.img"
image_bytes=$(stat -c %s "$output/$image_name.img")
image_sha256=$(sha256sum "$output/$image_name.img" | cut -d' ' -f1)
artifact="$image_name.img"
if [ "$compress" = 1 ]; then
  xz -T0 -6 -f "$output/$image_name.img"
  artifact="$image_name.img.xz"
fi
artifact_sha256=$(sha256sum "$output/$artifact" | cut -d' ' -f1)
printf '%s  %s\n' "$artifact_sha256" "$artifact" >"$output/$artifact.sha256"
install -m 0644 "$fat/README.txt" "$output/$image_name.README.txt"
{
  echo "image $image_name.img"
  echo "image_bytes $image_bytes"
  echo "image_sha256 $image_sha256"
  echo "artifact $artifact $(stat -c %s "$output/$artifact") $artifact_sha256"
  echo "layout mbr label-id=$disk_id p1=$p1_start+$p1_sectors(fat32,$fat_label,$esp_uuid) p2=$p2_start+$p2_sectors(ext4,boot,$boot_uuid) p3=$p3_start+rest(btrfs,luma,$root_uuid)"
  echo "root_used_mib $used_mib"
  echo "iso $iso_name.iso"
  echo "origin $remote:$ref"
  echo "commit $commit"
  echo "version $version"
  echo "channel $channel"
  echo "kernel $release"
  echo "kargs ${kargs[*]}"
  echo "uboot $uboot_nevra $uboot_rpm_sha256"
  echo "grub $(sha256sum "$grub_efi" | cut -d' ' -f1) ${grub_efi#"$deploy"}"
  echo "hdmi_audio $hdmi_audio"
  echo "partition1:"
  (cd "$fat" && find . -type f | LC_ALL=C sort | xargs sha256sum | sed 's/^/  /')
} >"$output/$image_name.manifest.txt"

echo "Card image: $output/$artifact"
echo "  raw $image_bytes bytes, sha256 $image_sha256; root uses $used_mib MiB"
