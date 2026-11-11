#!/bin/bash
# make-arm1-inputs.sh MAIN_CHECKOUT: the Pixel 9 initramfs inputs for the Luma ARM image, on luma-build.
# tokay-luma-initramfs-arm1 = tokay-luma-initramfs-alpha1 + the shared OSTree boot resolver from
# SimplyLuma/luma main (scripts/mobile/common/initramfs: /usr/lib/luma/ostree-boot.sh and
# /hooks-extra/50-luma-ostree-default.sh) + init_functions.sh leaving /boot on LumaRoot (as
# scripts/mobile/fp6-native-init-functions.sh on main), where ostree writes the boot entries the
# resolver reads. -dev adds the lab SSH key from tokay-luma-initramfs-v2.
set -euo pipefail
MAIN=$(realpath "${1:?usage: make-arm1-inputs.sh MAIN_CHECKOUT}")
cd /mnt/luma-secondary/luma-build/pixel9-fold
A=inputs/tokay-luma-initramfs-alpha1; R=inputs/tokay-luma-initramfs-arm1; D=inputs/tokay-luma-initramfs-arm1-dev
[ ! -e $R ] && [ ! -e $D ] || { echo "inputs exist"; exit 1; }
cp -a $A $R
install -D -m 0644 $MAIN/scripts/mobile/common/initramfs/luma-ostree-boot.sh $R/usr/lib/luma/ostree-boot.sh
install -D -m 0755 $MAIN/scripts/mobile/common/initramfs/hooks-extra/50-luma-ostree-default.sh $R/hooks-extra/50-luma-ostree-default.sh
python3 - $R/init_functions.sh <<'PY'
import sys,pathlib
p=pathlib.Path(sys.argv[1]); s=p.read_text()
anchor="""	find_boot_partition partition
	mount_opts="-o nodev,nosuid,noexec"
"""
assert s.count(anchor)==1
i=s.index("mount_boot_partition() {"); j=s.index(anchor,i)
head=s[i:j]
assert "local mount_opts" in head
new=head+"""	# Luma OSTree: /boot is the root filesystem's /boot, which the OSTree
	# system generator bind-mounts after switch_root. ostree writes its boot
	# entries and greenboot its grubenv there, and the OSTree boot resolver
	# (/usr/lib/luma/ostree-boot.sh) reads them there. The boot partition
	# (LumaBoot) holds only the device kit; luma-device-layer mounts it itself.
	if [ "$1" = /sysroot/boot ] && [ -e /etc/luma-fp6-ostree ]; then
		echo "Luma OSTree: /boot stays on the root filesystem; not mounting the boot partition"
		return 0
	fi

"""
s=s[:i]+new+s[j:]; p.write_text(s)
PY
sh -n $R/init_functions.sh; sh -n $R/hooks-extra/50-luma-ostree-default.sh
cp -a $R $D
cp -a inputs/tokay-luma-initramfs-v2/luma-fold/usbnet/authorized_keys $D/luma-fold/usbnet/authorized_keys
install -m 0755 "$(dirname "$0")"/build-tokay-arm1.sh "$(dirname "$0")"/build-tokay-arm1-dev.sh probe/
echo PREP-OK; diff <(cd $A && find . | sort) <(cd $R && find . | sort) | head
