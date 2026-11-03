#!/bin/sh
# Pixel 9 device files on the shared ARM image, installed from the boot image
# every boot so the OS commit is the same on every ARM device: Wi-Fi power
# saving, the AoC quantum floor, thermal and CPU-cap rules, the IMS and AoC
# audio watchdogs, Mutter's frame-thread floor, and an update policy that
# never downloads on its own (this boot image names one deployment).
d=/luma-fold/tokay-system; e=/sysroot/etc
[ -d $d ] && [ -d $e ] || exit 0
put() { mkdir -p "$(dirname "$2")"; cmp -s "$1" "$2" || install -m "$3" "$1" "$2"; }
put $d/50-luma-tokay-wifi-powersave.conf $e/NetworkManager/conf.d/50-luma-tokay-wifi-powersave.conf 0644
put $d/60-luma-tokay-aoc-quantum.conf $e/pipewire/pipewire.conf.d/60-luma-tokay-aoc-quantum.conf 0644
put $d/60-luma-tokay-thermal.rules $e/udev/rules.d/60-luma-tokay-thermal.rules 0644
put $d/60-luma-tokay-x4-cap.conf $e/tmpfiles.d/60-luma-tokay-x4-cap.conf 0644
mkdir -p $e/systemd/system/multi-user.target.wants
for w in ims aoc-audio; do
	put $d/luma-tokay-$w-watchdog.sh /sysroot/var/lib/luma-tokay/$w-watchdog.sh 0755
	put $d/luma-tokay-$w-watchdog.service $e/systemd/system/luma-tokay-$w-watchdog.service 0644
	ln -sfn ../luma-tokay-$w-watchdog.service $e/systemd/system/multi-user.target.wants/luma-tokay-$w-watchdog.service
done
[ -d $e/dconf/db/luma.d ] && put $d/20-luma-tokay-frames $e/dconf/db/luma.d/20-luma-tokay-frames 0644
put $d/update.conf $e/luma/update.conf 0644
echo "luma-tokay: device files installed" > /dev/kmsg
exit 0
