#!/bin/sh
# Pixel 9 (tokay): one panel, no hinge. The Fold image carries luma-fold-gate
# (posture-driven touch/output switching); keep it off here.
s=/sysroot/etc/systemd/system; u=/sysroot/etc/systemd/user
[ -d $s ] || exit 0
mkdir -p /sysroot/var/lib/luma-fold
mkdir -p $u
ln -sfn /dev/null $s/luma-fold-gate.service
ln -sfn /dev/null $s/luma-fold-touch-gate.service
ln -sfn /dev/null $u/luma-fold-outputs.service
mkdir -p /sysroot/etc/systemd/logind.conf.d
printf "[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\nHandleLidSwitchDocked=ignore\n" > /sysroot/etc/systemd/logind.conf.d/90-luma-tokay.conf
echo "luma-tokay: fold gate masked" > /dev/kmsg

# Data mode APN from the SIM (luma-tokay-apn.py) instead of the Fold's tello.
if [ -f /luma-fold/luma-tokay-apn.py ]; then
	install -m 0755 /luma-fold/luma-tokay-apn.py /sysroot/var/lib/luma-fold/luma-tokay-apn.py
	printf "[Unit]\nDescription=Pixel 9: data APN from the SIM\nAfter=ModemManager.service luma-fold-modem.service\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 /var/lib/luma-fold/luma-tokay-apn.py\n[Install]\nWantedBy=multi-user.target\n" > $s/luma-tokay-apn.service
	mkdir -p $s/multi-user.target.wants
	ln -sf ../luma-tokay-apn.service $s/multi-user.target.wants/luma-tokay-apn.service
fi
exit 0
