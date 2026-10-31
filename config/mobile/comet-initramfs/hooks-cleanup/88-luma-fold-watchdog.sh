#!/bin/sh
# Fold bring-up: with luma.fold_wdt=1, let systemd pet the cluster watchdog so
# a hard hang resets the SoC; the kernel pre-arms the bootloader reboot reason,
# so the reset lands in fastboot. Untested until tried with hands available.
d=/sysroot/etc/systemd/system.conf.d
[ -d /sysroot/etc/systemd ] || exit 0
if grep -q "luma.fold_wdt=1" /proc/cmdline; then
	mkdir -p $d
	printf "[Manager]\nRuntimeWatchdogSec=20s\nRebootWatchdogSec=30s\n" > $d/90-luma-fold-watchdog.conf
else
	rm -f $d/90-luma-fold-watchdog.conf
fi
