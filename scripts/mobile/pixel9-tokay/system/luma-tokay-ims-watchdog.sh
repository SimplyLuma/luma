#!/bin/sh
# The S5400 sometimes ignores the first IMS SETUP_DATA_CALLs after boot and
# ModemManager stops retrying: LTE works, calls go to voicemail. Restarting
# ModemManager, then the radio service once it is up, registers IMS.
# Checks every minute; acts when the IMS attach is still being retried 3
# minutes after ModemManager started; never during a call; at most 5
# recoveries per boot.
tries=0
while sleep 60; do
	# Monotonic time and the running PID: the wall clock is still wrong when
	# ModemManager starts, so a wall-clock --since also matched older boots.
	start=$(systemctl show -P ActiveEnterTimestampMonotonic ModemManager.service)
	pid=$(systemctl show -P MainPID ModemManager.service)
	[ "${start:-0}" -gt 0 ] && [ "${pid:-0}" -gt 0 ] || continue
	up=$(( $(cut -d. -f1 /proc/uptime) - start / 1000000 ))
	[ "$up" -ge 180 ] || continue
	# Data mode turns IMS off on purpose, and a switch restarts ModemManager.
	mode=$(busctl --system get-property org.projectluma.MobileMode1 /org/projectluma/MobileMode1 org.projectluma.MobileMode1 Mode 2>/dev/null)
	state=$(busctl --system get-property org.projectluma.MobileMode1 /org/projectluma/MobileMode1 org.projectluma.MobileMode1 State 2>/dev/null)
	[ -z "$mode" ] || { [ "$mode" = 's "calls"' ] && [ "$state" = 's "ready"' ]; } || continue
	# Restarting ModemManager ends every call: never act while one exists.
	mmcli -m any --voice-list-calls 2>/dev/null | grep -q "/Call/" && continue
	# ModemManager logs no IMS registration at its default level. A failed IMS
	# attach is visible as its retries: it re-sends SETUP_DATA_CALL every 30 s
	# and gives up after the third, while an accepted one is sent once.
	tries_ims=$(journalctl -q -b _PID="$pid" -o cat 2>/dev/null | grep -c "SETUP_DATA_CALL (IMS")
	[ "$tries_ims" -ge 2 ] || continue
	mmcli -m any 2>/dev/null | grep -qE "state: (registered|connected)" || continue
	[ "$tries" -lt 5 ] || continue
	tries=$((tries + 1))
	echo "IMS not registered ${up}s after ModemManager start; restarting the modem stack ($tries/5)"
	systemctl restart ModemManager.service
	for i in $(seq 20); do mmcli -L 2>/dev/null | grep -q Modem && break; sleep 1; done
	systemctl try-restart luma-fold-modem-radio.service
done
