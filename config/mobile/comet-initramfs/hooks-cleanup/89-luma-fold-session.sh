#!/bin/sh
# Shared Luma shell (GDM + GNOME Shell) on the Fold: sign the phone's user in
# as greetd's initial session did, and name the device class Tablet (the
# inner screen) for luma-presentation-profile; Settings can still choose
# Mobile. Images without GDM keep their greetd session untouched. mmsd-tng
# runs in the session for MMS (phone images enable it; the desktop base
# does not). Mutter treats the hinge as a laptop lid (folded it turns the
# outer panel off), so UPower ignores it; the packaged luma-fold-gate
# follows the hinge and lights the facing panel with Mobile (outer) or Tablet
# (inner) presentation.
[ -e /sysroot/usr/lib/systemd/system/gdm.service ] || exit 0
c=/sysroot/etc/gdm/custom.conf
u=$(awk -F: '$3 >= 1000 && $3 < 60000 { print $1; exit }' /sysroot/etc/passwd 2>/dev/null)
if [ -n "$u" ] && ! grep -q '^AutomaticLogin=' $c 2>/dev/null; then
	mkdir -p /sysroot/etc/gdm
	[ -f $c ] || printf '[daemon]\n' > $c
	grep -q '^\[daemon\]' $c || printf '[daemon]\n' >> $c
	sed -i "s/^\[daemon\]/[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=$u/" $c
fi
[ -s /sysroot/etc/luma-device-class ] || printf 'tablet\n' > /sysroot/etc/luma-device-class
if [ -e /sysroot/usr/lib/systemd/user/mmsd-tng.service ]; then
	mkdir -p /run/systemd/user/default.target.wants
	ln -sf /usr/lib/systemd/user/mmsd-tng.service /run/systemd/user/default.target.wants/mmsd-tng.service
fi
# Handheld device class (as phone images set in environment.d): Mutter's
# handheld scales and the shell's handheld chrome.
if [ ! -e /sysroot/usr/lib/environment.d/90-luma-handheld.conf ]; then
	mkdir -p /sysroot/etc/environment.d
	printf 'LUMA_DEVICE_CLASS=handheld\n' > /sysroot/etc/environment.d/90-luma-handheld.conf
fi
u=/sysroot/etc/UPower/UPower.conf
if [ -f $u ]; then
	sed -i 's/^IgnoreLid=false/IgnoreLid=true/' $u
fi
