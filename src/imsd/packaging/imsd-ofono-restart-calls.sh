#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®
#
# ExecStartPost of imsd-ofonod.service. GNOME Calls 50.0 keeps a stale modem
# when org.ofono vanishes and dials nothing until it is restarted (AGENTS.md
# §8.3, Known gaps), so every start of imsd-ofonod after org.ofono went away
# restarts the Calls daemon of each user running one. Its first start since
# boot is not one: no Calls has seen org.ofono go, and a Calls that runs
# already (a login during the bring-up) is left alone. The unit's own
# timestamp tells the first start from a restart (an upgrade from 0.3.9 is
# a restart); the marker covers a stop and a later start.
marker=/run/imsd-ofonod.started
if [ ! -e "$marker" ] && [ "$(systemctl show -P ActiveExitTimestampMonotonic imsd-ofonod.service)" = 0 ]; then
	: > "$marker"
	echo "imsd-ofono-restart-calls: first start since boot, Calls left alone"
	exit 0
fi
: > "$marker"
for uid in $(systemctl list-units 'user@*.service' --legend=no --plain | sed -n 's/^user@\([0-9]*\)\.service.*/\1/p'); do
	echo "imsd-ofono-restart-calls: restarting the Calls daemon of uid $uid, if it runs"
	systemctl --user -M "$uid@" try-restart --no-block calls-daemon.service || :
done
exit 0
