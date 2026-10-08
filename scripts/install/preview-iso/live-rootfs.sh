#!/bin/bash
# Stage 1 (tools container, privileged): turn the Luma commit into a live root.
set -euo pipefail
REF=luma/0.5/x86_64/desktop-preview
L=/w/live
R=$L/rootfs
rm -rf $L && mkdir -p $L
ostree --repo=/w/luma-repo checkout $REF $R
# An OSTree commit keeps default configuration under /usr/etc; a booted
# deployment merges it into /etc. A live root has no deployment, so move it.
mv $R/usr/etc $R/etc
for d in var var/tmp run tmp proc sys dev sysroot boot; do mkdir -p $R/$d; done
chmod 1777 $R/var/tmp
chmod 1777 $R/tmp
KVER=$(ls $R/usr/lib/modules | head -1)
echo "kernel $KVER"
# The live initramfs needs dracut's live modules, which an installed Luma
# does not carry. Copy them into this live root only.
for m in 70dmsquash-live 70img-lib 70overlayfs; do
  [ -d $R/usr/lib/dracut/modules.d/$m ] || cp -a /usr/lib/dracut/modules.d/$m $R/usr/lib/dracut/modules.d/
done
mount --bind /proc $R/proc; mount --bind /sys $R/sys; mount --bind /dev $R/dev
trap 'umount $R/dev $R/sys $R/proc 2>/dev/null || true' EXIT
chroot $R dracut --no-hostonly --no-hostonly-cmdline --add "dmsquash-live overlayfs" --force /boot/initrd-live.img $KVER
mkdir -p $L/boot && cp $R/boot/initrd-live.img $L/boot/initrd.img && cp $R/usr/lib/modules/$KVER/vmlinuz $L/boot/vmlinuz
rm -f $R/boot/initrd-live.img
# The live person: no password, signed in automatically, allowed to sudo.
mkdir -p $R/var/home
chroot $R useradd -m -d /var/home/liveuser -c "Luma Live" -G wheel liveuser
chroot $R passwd -d liveuser
printf '%s\n' 'liveuser ALL=(ALL) NOPASSWD: ALL' > $R/etc/sudoers.d/luma-live && chmod 0440 $R/etc/sudoers.d/luma-live
mkdir -p $R/etc/gdm
if [ -f $R/etc/gdm/custom.conf ] && grep -q '^\[daemon\]' $R/etc/gdm/custom.conf; then
  sed -i '/^\[daemon\]/a AutomaticLoginEnable=True\nAutomaticLogin=liveuser' $R/etc/gdm/custom.conf
else
  printf '[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=liveuser\n' >> $R/etc/gdm/custom.conf
fi
mkdir -p $R/var/home/liveuser/.config && echo yes > $R/var/home/liveuser/.config/gnome-initial-setup-done
chroot $R chown -R liveuser:liveuser /var/home/liveuser
echo luma-live > $R/etc/hostname
# Nothing from a real machine: no machine id, no SSH host keys.
: > $R/etc/machine-id
rm -f $R/etc/ssh/ssh_host_*
cat $R/etc/gdm/custom.conf
du -sh $R
echo STAGE1-DONE
