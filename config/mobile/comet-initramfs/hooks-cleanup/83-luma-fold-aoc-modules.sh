#!/bin/sh
# Fold bring-up: the AOC and its clients are modules, loaded from a unit once
# journald is up rather than during kernel init (built in, the AOC start hung
# the boot before the display handover), and before the display manager, so
# the sound card exists when the session's WirePlumber starts: started without
# one, its role loopbacks link to each other and a stream's link into them
# never activates. Unless luma.fold_aoc=0, copy
# /luma-fold/modules to /var/lib/luma-fold/modules and insmod them in
# load-order, each step announced in kmsg and synced to the journal before it
# runs; a module still loading after 90 s ends the sequence. The kernel's
# hung-task and stall panics (command line) turn a wedged load into a reboot
# to fastboot with the log in ramoops.
s=/sysroot/etc/systemd/system
[ -d $s ] && [ -f /luma-fold/modules/load-order ] || exit 0
if ! grep -q "luma.fold_aoc=0" /proc/cmdline; then
	m=/sysroot/var/lib/luma-fold/modules
	mkdir -p /sysroot/var/lib/luma-fold
	rm -rf $m && cp -a /luma-fold/modules $m
	cat > $s/luma-fold-aoc-load.sh <<"SH"
#!/bin/sh
m=/var/lib/luma-fold/modules
say() { echo "luma-fold-aoc: $*" > /dev/kmsg; journalctl --sync 2>/dev/null; sync; }
say "loading $(tr '\n' ' ' < $m/load-order)"
for k in $(cat $m/load-order); do
	say "insmod $k"
	insmod $m/$k &
	p=$!; t=0
	while kill -0 $p 2>/dev/null; do
		sleep 1; t=$((t + 1))
		[ $((t % 15)) = 0 ] && say "$k still loading after ${t}s"
		[ $t -ge 90 ] && { say "$k stuck after ${t}s, stopping"; exit 1; }
	done
	wait $p; rc=$?
	say "$k rc=$rc"
	[ $rc = 0 ] || { say "stopping after $k"; exit 1; }
done
say "all loaded"
# A session that started first picks the card up only after a restart.
if systemctl --user -M luma@ is-active -q wireplumber 2>/dev/null; then
	say "restarting the session's wireplumber"
	systemctl --user -M luma@ restart wireplumber 2>&1
fi
SH
	chmod 755 $s/luma-fold-aoc-load.sh
	printf "[Unit]\nDescription=Fold bring-up: load the AOC modules\nAfter=systemd-journald.service local-fs.target\nBefore=greetd.service display-manager.service bluetooth.service\n[Service]\nType=oneshot\nExecStart=/bin/sh /etc/systemd/system/luma-fold-aoc-load.sh\n[Install]\nWantedBy=multi-user.target\n" > $s/luma-fold-aoc-load.service
	mkdir -p $s/multi-user.target.wants
	ln -sf ../luma-fold-aoc-load.service $s/multi-user.target.wants/luma-fold-aoc-load.service
else
	rm -f $s/luma-fold-aoc-load.service $s/luma-fold-aoc-load.sh $s/multi-user.target.wants/luma-fold-aoc-load.service
fi
