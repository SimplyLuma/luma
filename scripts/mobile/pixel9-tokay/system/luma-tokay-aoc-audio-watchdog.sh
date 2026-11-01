#!/bin/sh
# Pixel 9 stopgap: the AoC speaker PCM never recovers from an underrun
# (PipeWire logs "snd_pcm_avail after recover: Broken pipe" every 2 s and plays
# nothing). Restart the owner's audio stack when that starts; at most once a minute.
last=0
journalctl -f -n 0 -o json SYSLOG_IDENTIFIER=pipewire | while read -r line; do
	case $line in *"snd_pcm_avail after recover: Broken pipe"*) ;; *) continue ;; esac
	now=$(cut -d. -f1 /proc/uptime)
	[ $((now - last)) -ge 60 ] || continue
	last=$now
	uid=$(printf "%s" "$line" | sed -n "s/.*\"_UID\":\"\([0-9]*\)\".*/\1/p")
	user=$(id -nu "$uid" 2>/dev/null) || continue
	echo "AoC speaker stuck after an underrun; restarting PipeWire for $user"
	systemctl --user --machine="$user@" restart pipewire.service wireplumber.service pipewire-pulse.service
	# Android's audio HAL reconnects to the new pulse socket only on a fresh audioserver.
	sleep 2; timeout 20 waydroid shell -- sh -c 'kill $(pidof audioserver)' 2>/dev/null
done
