#!/bin/sh
# Fold bring-up: with luma.fold_debug=1, reboot after 5 minutes (survey copy); luma_diag
# sends every restart to fastboot, so the journal can be pulled unattended.
# luma.fold_hands=1 keeps the debug logging but skips the reboot, for a
# hands-on session whose journal is pulled afterwards.
s=/sysroot/etc/systemd/system
[ -d $s ] || exit 0
if grep -q "luma.fold_debug=1" /proc/cmdline && ! grep -q "luma.fold_hands=1" /proc/cmdline; then
	printf "[Unit]\nDescription=Fold bring-up auto reboot\n[Service]\nExecStart=/bin/sh -c \"sleep 300; systemctl reboot\"\n[Install]\nWantedBy=multi-user.target\n" > $s/luma-fold-autoreboot.service
	mkdir -p $s/multi-user.target.wants
	ln -sf ../luma-fold-autoreboot.service $s/multi-user.target.wants/luma-fold-autoreboot.service
else
	rm -f $s/luma-fold-autoreboot.service $s/multi-user.target.wants/luma-fold-autoreboot.service
fi
