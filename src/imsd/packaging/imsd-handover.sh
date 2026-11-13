#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®
#
# ExecStartPre of imsd.service, after ims-pdn-up.sh: hand a daemon older
# than 0.3.10 its bring-up throttle wait over. Such a daemon kept the wait
# in memory only, so the restart an upgrade makes registered fresh at once
# instead of waiting the schedule out. imsd.post-upgrade
# records the running daemon's invocation in /run/imsd.handover; here, with
# that daemon stopped and its journal final, the invocation's last bring-up
# line is read and, when it is the old throttle's `bring-up deferred:
# fresh-SA throttle active (spi-s=0) — … next fresh registration in
# N min`, what is left of the N min is written to
# the holds file (/run/imsd.holds, or HOLDS_FILE) the way 0.3.10 keeps
# it — with the verdict that the old registration is gone: that line
# follows a throttled initial REGISTER, at which KPN drops the
# registration, so the new start does not resume over the written-off SAs.
# The old daemon deferred the same way after a 401 it could not use (no
# ipsec-3gpp mechanism, an unsupported algorithm); nothing says the
# network dropped the registration then, and that wait is not handed over.
# The wait itself is handed over only for the address it was earned on
# (the old daemon logged its LOCAL once at start): KPN's refusal is the
# address's — a fresh pair is refused where a registration is live or has
# just gone — and on the normal hop the old daemon sat on the kernel's
# temporary address while this start registers on the PDN's own, which
# holds none: then only the verdict is written and the new start registers
# fresh at once (should the network refuse it there, the daemon's own
# schedule takes over from its start: the old daemon's place in it was
# the other address's).
# When the old address cannot be read the wait is handed over anyway.
# Reading at the start, not at the upgrade, means a wait that
# ended between the two — the old daemon making another attempt — is
# read as it ended.
set -u
marker=/run/imsd.handover
[ -r "$marker" ] || exit 0
inv=$(cat "$marker"); rm -f "$marker"
[ -n "$inv" ] || exit 0
holds=${HOLDS_FILE:-/run/imsd.holds}  # the holds file as imsd reads it (HoldsPath)
[ -e "$holds" ] && exit 0             # a 0.3.10 or later daemon kept its own holds
line=$(journalctl -u imsd.service "_SYSTEMD_INVOCATION_ID=$inv" -o json --no-pager 2>/dev/null \
	| grep -E '"MESSAGE" *: *"imsd: (bring-up deferred: |REGISTERED|FRESH registration|a hold the network)' | tail -n 1)
case "$line" in *'imsd: bring-up deferred: fresh-SA throttle active (spi-s=0)'*) ;; *) exit 0 ;; esac
mins=$(printf '%s\n' "$line" | sed -n 's/.*next fresh registration in \([0-9][0-9]*\) min.*/\1/p')
# the line's age on the monotonic clock (journald stamps every entry with it;
# /proc/timer_list reads it now), which no clock step moves — this phone's
# wall clock reads 1970 until its first NTP sync. It stops in a suspend, so
# the age can only come out short and the wait handed over long, never
# short of the network's window.
at=$(printf '%s\n' "$line" | sed -n 's/.*"__MONOTONIC_TIMESTAMP" *: *"\([0-9][0-9]*\)".*/\1/p')
now=$(sed -n 's/^now at \([0-9][0-9]*\) nsecs$/\1/p' /proc/timer_list 2>/dev/null | head -n 1)
if [ -z "$mins" ] || [ -z "$at" ] || [ -z "$now" ]; then
	echo "imsd-handover: the previous daemon waited for a fresh registration, but its wait could not be timed: NOT handed over"
	exit 0
fi
left=$((mins * 60 - (now / 1000000000 - at / 1000000)))
[ "$left" -gt 0 ] || exit 0
# the address this start registers on, as systemd hands it to imsd: the unit
# reads its EnvironmentFile= lines anew for every command it spawns, so LOCAL
# here is the /etc/imsd.env pin when there is one, else the one ims-pdn-up.sh
# just wrote to /run/imsd.env — unquoted by systemd's parser, the same bytes
# imsd compares the holds file's pdn with. The file read is for a run outside
# the unit.
pdn=${LOCAL:-$(sed -n 's/^LOCAL=//p' /run/imsd.env 2>/dev/null | tail -n 1)}
if [ -z "$pdn" ]; then
	echo "imsd-handover: the previous daemon waited $mins min for a fresh registration, but this start has no LOCAL: NOT handed over"
	exit 0
fi
# the address the wait was earned on: the old daemon's own start line
old=$(journalctl -u imsd.service "_SYSTEMD_INVOCATION_ID=$inv" -o cat --no-pager 2>/dev/null \
	| sed -n 's/^imsd: LOCAL=\([^ ]*\) P-CSCF=.*/\1/p' | tail -n 1)
boot=$(cat /proc/sys/kernel/random/boot_id); now_s=$(cut -d. -f1 /proc/uptime)
if [ -n "$old" ] && [ "$old" != "$pdn" ]; then
	printf '{"pdn": "%s", "boot": "%s", "written_at": %s, "hold": 0, "fresh_hold": 0, "old_gone": true}\n' \
		"$pdn" "$boot" "$now_s" > "$holds" \
		&& echo "imsd-handover: the previous daemon's fresh-registration wait ($left s of $mins min left) was earned on $old, not this start's $pdn: only the verdict that its registration is gone is handed over"
	exit 0
fi
tm=$((mins * 3 / 2)); [ "$tm" -gt 60 ] && tm=60
printf '{"pdn": "%s", "boot": "%s", "written_at": %s, "hold": 0, "fresh_hold": %s, "throttle_minutes": %s, "old_gone": true}\n' \
	"$pdn" "$boot" "$now_s" "$left" "$tm" > "$holds" \
	&& echo "imsd-handover: the previous daemon's fresh-registration wait ($left s of $mins min left) handed over for $pdn$([ -n "$old" ] && echo " (its own address)" || echo "; its address could not be read")"
exit 0
