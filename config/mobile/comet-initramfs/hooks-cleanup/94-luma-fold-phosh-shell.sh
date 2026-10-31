#!/bin/sh
# Fold bring-up: run a newer phosh than the image's until an OS image carries
# it. luma-phosh 1.luma.90~fold20261029.4: 0065 holds the overview's current
# activity weakly (the image's build crashes on a Home drag after a call
# window closes); 0066 keeps waiting for a slow-starting app instead of
# reporting it failed (the launch pid is kept across compositor events so the
# wait runs); 0067 closes the splash when the window maps even without the
# compositor's startup-id report. Same imports as the image's binary plus kill(), so it
# runs on the image's libraries. Drop this hook once the image ships it.
[ -d /sysroot/var ] && [ -f /luma-fold/phosh-shell/phosh ] || exit 0
p=/sysroot/var/lib/luma-fold/phosh-shell
mkdir -p $p
cmp -s /luma-fold/phosh-shell/phosh $p/phosh || install -m 0755 /luma-fold/phosh-shell/phosh $p/phosh
d=/run/systemd/user/mobi.phosh.Shell.service.d
mkdir -p $d
printf '[Service]\nExecStart=\nExecStart=/var/lib/luma-fold/phosh-shell/phosh --locked\n' > $d/95-luma-fold-phosh-shell.conf
