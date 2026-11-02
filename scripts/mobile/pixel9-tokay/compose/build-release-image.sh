#!/bin/bash
# On the Pixel 9 (root): build the fresh-install userdata image for a release
# commit in /ostree/repo. userdata holds a disk: LumaBoot (FAT) and LumaRoot
# (ext4) with the UUIDs the boot image's command line names; the initramfs
# grows LumaRoot to fill userdata on the first boot. Nothing from the running
# system's /etc or /var is copied. The image has one user, luma, password 1234.
# Writes $OUT/luma-pixel9-$TAG-userdata.sparse, its sha256 and ostree-path (for
# the boot image). Compress it off the phone: zstd -19 on all cores overheats it.
# Usage: COMMIT=<commit> TAG=<name> OUT=<dir> build-release-image.sh
set -euo pipefail
: "${COMMIT:?}" "${TAG:?}" "${OUT:?}"
ROOT_UUID=${ROOT_UUID:-03797c05-cf87-4eda-adc4-6ce111baf2d3}
BOOT_UUID=${BOOT_UUID:-032D-57AD}
SIZE=${SIZE:-10G}
REF=luma/1/aarch64/google-tokay/$TAG
mkdir -p $OUT; D=$OUT/disk.raw; M=$OUT/mnt
rm -f $D; truncate -s $SIZE $D
# The initramfs reads userdata with 4096-byte sectors (UFS), so the disk
# inside uses them too; it can't see a 512-byte partition table.
L=$(losetup -b 4096 -fP --show $D)
trap 'umount -R $M 2>/dev/null || true; losetup -d $L' EXIT
sfdisk -q $L <<EOF
label: gpt
start=256, size=480MiB, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name=LumaBoot
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=LumaRoot
EOF
partx -u $L 2>/dev/null || true
mkfs.vfat -F 32 -S 4096 -n LUMABOOT -i ${BOOT_UUID/-/} ${L}p1 >/dev/null
mkfs.ext4 -q -L LumaRoot -U $ROOT_UUID ${L}p2
# OSTree's /boot is a directory on LumaRoot; LumaBoot stays empty.
mkdir -p $M; mount ${L}p2 $M

ostree admin init-fs $M
ostree admin os-init --sysroot=$M luma
ostree --repo=$M/ostree/repo pull-local /ostree/repo $COMMIT
printf '[origin]\nrefspec=luma:%s\n' $REF > $OUT/origin
ostree --repo=$M/ostree/repo refs --create=luma:$REF $COMMIT
ostree admin deploy --sysroot=$M --os=luma --origin-file=$OUT/origin $COMMIT
DEP=$M/ostree/deploy/luma/deploy/$COMMIT.0
ln -s deploy/luma/deploy/$COMMIT.0 $M/ostree/root.a
grep -o 'ostree=[^ ]*' $M/boot/loader/entries/*.conf | head -1 | cut -d= -f2- > $OUT/ostree-path

# The user. Appending keeps each file's SELinux label.
HASH=$(openssl passwd -6 1234)
echo "luma:x:1000:1000:Luma:/home/luma:/bin/bash" >> $DEP/etc/passwd
echo "luma:$HASH:20000:0:99999:7:::" >> $DEP/etc/shadow
echo "luma:x:1000:" >> $DEP/etc/group
echo "luma:!::" >> $DEP/etc/gshadow
for g in wheel audio luma-display; do
	grep -q "^$g:" $DEP/etc/group || continue
	sed -i "/^$g:/{s/:\$/:luma/;t;s/\$/,luma/}" $DEP/etc/group
done
V=$M/ostree/deploy/luma/var
mkdir -p $V/home
cp -a $DEP/etc/skel $V/home/luma
# The AoC speaker comes up at 0 and muted, and WirePlumber saves that as the
# first route state; start it audible instead (50%, cubic).
mkdir -p $V/home/luma/.local/state/wireplumber
cat > $V/home/luma/.local/state/wireplumber/default-routes <<'ROUTES'
[default-routes]
alsa_card.platform-aoc-sound:output:\oOut\c\sSpeaker={"mute":false, "latencyOffsetNsec":0, "channelVolumes":[0.125000, 0.125000], "channelMap":["FL", "FR"]}
alsa_card.platform-aoc-sound:profile:HiFi\s(Mic,\sSpeaker)=["[Out] Speaker"]
ROUTES
chown -R 1000:1000 $V/home/luma; chmod 0700 $V/home/luma
# The FP6 lineage's /boot automount stays off, as on the tested phone.
: > $DEP/etc/systemd/system/boot.automount
setfiles -r $DEP $DEP/etc/selinux/targeted/contexts/files/file_contexts $DEP/etc 2>/dev/null || true
setfiles -r $M/ostree/deploy/luma $DEP/etc/selinux/targeted/contexts/files/file_contexts $V/home 2>/dev/null || true

sync; umount -R $M; trap 'losetup -d $L' EXIT
e2fsck -fy ${L}p2 >/dev/null || [ $? -le 1 ]
losetup -d $L; trap - EXIT
img2simg $D $OUT/luma-pixel9-$TAG-userdata.sparse
rm -f $D
cd $OUT && sha256sum luma-pixel9-$TAG-userdata.sparse > userdata.sparse.sha256
cat userdata.sparse.sha256 ostree-path
