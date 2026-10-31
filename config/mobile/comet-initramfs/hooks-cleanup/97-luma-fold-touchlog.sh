#!/bin/sh
# Fold bring-up: with luma.fold_debug=1, log libinput touch events and phoc
# debug messages; otherwise remove both again.
s=/sysroot/etc/systemd/system
h=/sysroot/var/home/luma/.phoshdebug
[ -d $s ] || exit 0
if grep -q "luma.fold_debug=1" /proc/cmdline; then
	cat > $s/luma-fold-touchlog.service <<"UNIT"
[Unit]
Description=Fold bring-up touch event log
After=systemd-udev-settle.service
[Service]
ExecStart=/usr/bin/libinput debug-events --show-keycodes
[Install]
WantedBy=multi-user.target
UNIT
	mkdir -p $s/multi-user.target.wants
	ln -sf ../luma-fold-touchlog.service $s/multi-user.target.wants/luma-fold-touchlog.service
	if [ -d /sysroot/var/home/luma ]; then
		printf "export G_MESSAGES_DEBUG=all\n" > $h
		chown 1000:1000 $h
	fi
else
	rm -f $s/luma-fold-touchlog.service $s/multi-user.target.wants/luma-fold-touchlog.service $h
fi
