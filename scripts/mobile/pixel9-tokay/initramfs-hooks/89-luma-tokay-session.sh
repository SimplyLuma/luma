#!/bin/sh
# Shared Luma shell (GDM + GNOME Shell) on the Pixel 9: sign the phone's user in
# as greetd's initial session did, and boot in Mobile presentation (a phone;
# Settings can still choose another). Images without GDM keep
# their greetd session untouched.
[ -e /sysroot/usr/lib/systemd/system/gdm.service ] || exit 0
c=/sysroot/etc/gdm/custom.conf
u=$(awk -F: '$3 >= 1000 && $3 < 60000 { print $1; exit }' /sysroot/etc/passwd 2>/dev/null)
if [ -n "$u" ] && ! grep -q '^AutomaticLogin=' $c 2>/dev/null; then
	mkdir -p /sysroot/etc/gdm
	[ -f $c ] || printf '[daemon]\n' > $c
	grep -q '^\[daemon\]' $c || printf '[daemon]\n' >> $c
	sed -i "s/^\[daemon\]/[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=$u/" $c
fi
# luma-presentation-profile reads the device class; a phone is mobile
[ -s /sysroot/etc/luma-device-class ] || printf 'mobile\n' > /sysroot/etc/luma-device-class
mkdir -p /run/luma
[ -s /run/luma/experience ] || printf 'mobile\n' > /run/luma/experience
# MMS: phone images enable mmsd-tng in the session; the desktop base does not.
if [ -e /sysroot/usr/lib/systemd/user/mmsd-tng.service ]; then
	mkdir -p /run/systemd/user/default.target.wants
	ln -sf /usr/lib/systemd/user/mmsd-tng.service /run/systemd/user/default.target.wants/mmsd-tng.service
fi
# Handheld device class (as phone images set in environment.d): Mutter's
# handheld scales and the shell's handheld chrome, login and lock included.
if [ ! -e /sysroot/usr/lib/environment.d/90-luma-handheld.conf ]; then
	mkdir -p /sysroot/etc/environment.d
	printf 'LUMA_DEVICE_CLASS=handheld\n' > /sysroot/etc/environment.d/90-luma-handheld.conf
fi
# Shared handheld defaults (config/mobile/luma-shell-handheld.dconf): the
# Shell owns a short Power press as display sleep/wake (power-button-action
# nothing, power-static released), 1 min blank + lock, no system suspend.
# The FP6 image installs them; the desktop-based image does not.
h=/sysroot/etc/dconf/db/luma.d/10-luma-handheld
if [ -d /sysroot/etc/dconf/db/luma.d ] && [ -f /luma-fold/luma-shell-handheld.dconf ] &&
   ! cmp -s /luma-fold/luma-shell-handheld.dconf $h; then
	install -m 0644 /luma-fold/luma-shell-handheld.dconf $h
	s=/sysroot/etc/systemd/system
	printf "[Unit]\nDescription=Pixel 9: compile the handheld dconf defaults\nDefaultDependencies=no\nAfter=local-fs.target\nBefore=display-manager.service\nConditionPathExists=/etc/dconf/db/luma.d/10-luma-handheld\n[Service]\nType=oneshot\nExecStart=/usr/bin/dconf update\n[Install]\nWantedBy=multi-user.target\n" > $s/luma-tokay-dconf.service
	mkdir -p $s/multi-user.target.wants
	ln -sf ../luma-tokay-dconf.service $s/multi-user.target.wants/luma-tokay-dconf.service
fi
