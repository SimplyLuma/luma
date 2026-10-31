#!/bin/sh
# Fold: radio firmware from the stock vendor image (/luma-fold/firmware, see
# LUMA-FOLD-FIRMWARE there). Built-in drivers request it during kernel init,
# before any hook runs: /usr/lib/firmware/brcm (and google) in the initramfs
# link into /luma-fold/firmware for that. /usr on the rootfs is immutable, so
# keep a copy in /var/lib/luma-fold/firmware for loads after switch_root
# (re-probe after a firmware crash or power loss, Bluetooth patchram) and point
# the firmware loader there.
[ -d /sysroot/var ] && [ -f /luma-fold/firmware/SHA256SUMS ] || exit 0
d=/sysroot/var/lib/luma-fold/firmware
if ! cmp -s /luma-fold/firmware/SHA256SUMS $d/SHA256SUMS; then
	rm -rf $d.new; mkdir -p /sysroot/var/lib/luma-fold
	cp -a /luma-fold/firmware $d.new && rm -rf $d && mv $d.new $d
fi
echo /var/lib/luma-fold/firmware > /sys/module/firmware_class/parameters/path
