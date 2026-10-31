#!/bin/sh
# Fold: only the panel facing the user takes touch (hinge SW_LID gates the
# outer and inner touchscreens), and folding must not suspend. The packaged
# luma-fold-gate takes over as far as the image carries it: with its session
# part (luma-fold-outputs) it owns touch, logind, outputs and the posture and
# this gate is removed; with only its system service it owns touch and logind
# and this one switches outputs and writes /run/luma/posture.
s=/sysroot/etc/systemd/system
[ -d $s ] && [ -f /luma-fold/fold-touch-gate.py ] || exit 0
if [ -e /sysroot/usr/lib/systemd/user/luma-fold-outputs.service ]; then
	rm -f $s/luma-fold-touch-gate.service $s/multi-user.target.wants/luma-fold-touch-gate.service \
		/sysroot/etc/systemd/logind.conf.d/90-luma-fold-hinge.conf /sysroot/var/lib/luma-fold/fold-touch-gate.py
	exit 0
fi
mkdir -p /sysroot/var/lib/luma-fold /sysroot/etc/systemd/logind.conf.d $s/multi-user.target.wants
cp /luma-fold/fold-touch-gate.py /sysroot/var/lib/luma-fold/fold-touch-gate.py
chmod 755 /sysroot/var/lib/luma-fold/fold-touch-gate.py
touch_env=""
if [ -e /sysroot/usr/lib/systemd/system/luma-fold-gate.service ]; then
	touch_env="Environment=LUMA_FOLD_GATE_TOUCH=0"
	rm -f /sysroot/etc/systemd/logind.conf.d/90-luma-fold-hinge.conf
else
	printf "[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\nHandleLidSwitchDocked=ignore\n" \
		> /sysroot/etc/systemd/logind.conf.d/90-luma-fold-hinge.conf
fi
printf "[Unit]\nDescription=Fold gate (hinge selects the panel facing the user)\nAfter=systemd-udev-settle.service systemd-udevd.service\n[Service]\n%s\nExecStart=/usr/bin/python3 /var/lib/luma-fold/fold-touch-gate.py\nRestart=on-failure\n[Install]\nWantedBy=multi-user.target\n" \
	"$touch_env" > $s/luma-fold-touch-gate.service
ln -sf ../luma-fold-touch-gate.service $s/multi-user.target.wants/luma-fold-touch-gate.service
