#!/bin/bash
# Fold GNSS bring-up test (luma.fold_gnsstest=1 only). From 45 s uptime: report
# the KEPLER SPI driver, the kepler.bin staging, the CP state and the modem
# driver's in-kernel GNSS codeload, the device nodes; then the product path:
# kepler-gnssd idle without clients, a geoclue client at accuracy EXACT gets a
# location (accuracy only is logged), navigation stops when it leaves; then
# reboot (luma_diag sends the restart to fastboot). The whole
# report and the GNSS kernel lines also go to
# /var/lib/systemd/coredump/luma-gnss-<boot id>.txt (journald runs behind and
# loses its tail on an early reboot), flushed before the reboot. The radio is
# never switched on here. Coordinates never reach the journal or the report:
# decimal runs that could be latitude/longitude are masked; the raw output stays
# root-only in /var/lib/luma-fold/gnss.
d=/var/lib/luma-fold/gnss
up() { cut -d' ' -f1 /proc/uptime; }
say() { echo "luma-gnss: [$(up)s] $*"; }
mask() { sed -E 's/-?[0-9]{1,3}\.[0-9]{4,}/<coord>/g; s/[0-9]{4}\.[0-9]{2,},[NS]/<coord>/g; s/[0-9]{5}\.[0-9]{2,},[EW]/<coord>/g'; }
finish() {
	say "done: $1"
	{ echo "== dmesg: GNSS, KEPLER, modem GNSS channel"
	  dmesg | grep -i -E "kepler|gnss|spi21|111e0000|CP is ONLINE|CP2AP_PHONE_ACTIVE|s5xxx.*(crash|reset|error)" | mask | tail -80; } >> $sum
	sleep 1
	journalctl --flush 2>/dev/null
	sync
	if grep -q "luma.fold_hands=1" /proc/cmdline; then
		say "luma.fold_hands=1: no reboot"
	else
		say "rebooting to the bootloader"
		systemctl reboot
	fi
	exit 0
}
while [ "$(cut -d. -f1 /proc/uptime)" -lt 45 ]; do sleep 1; done
boot=$(cut -c1-8 /proc/sys/kernel/random/boot_id)
# journald runs behind on these images and loses its tail on an early reboot:
# keep the whole report in the directory the journal pull collects as well.
mkdir -p /var/lib/systemd/coredump
sum=/var/lib/systemd/coredump/luma-gnss-$(cat /proc/sys/kernel/random/boot_id).txt
exec > >(tee -a "$sum") 2>&1
say "start (boot $boot, kernel $(uname -r))"

# 1. KEPLER SPI driver
sp=/sys/bus/spi/devices/spi21.0
say "module gnss_kepler: $(grep -c '^gnss_kepler ' /proc/modules) loaded; spi21.0: $([ -e $sp ] && echo present || echo absent), driver $(basename "$(readlink $sp/driver 2>/dev/null)" 2>/dev/null)"
say "deferred: $(grep -E 'spi|gnss|111e0000' /sys/kernel/debug/devices_deferred 2>/dev/null | tr '\n' ';')"
dmesg | grep -i -E "kepler|111e0000|spi21|gnss" | grep -v -i "GNSS boot cmd" | tail -15 | sed 's/^/luma-gnss: kmsg: /'
for g in /sys/class/gnss/gnss*; do
	[ -e "$g" ] || continue
	say "$(basename $g): type $(cat $g/type 2>/dev/null) device $(basename "$(readlink -f $g/device)") driver $(basename "$(readlink $g/device/driver 2>/dev/null)" 2>/dev/null)"
done
say "rails: $(for r in /sys/class/regulator/regulator.*; do n=$(cat $r/name 2>/dev/null); case $n in vdd_gnss*) echo -n "$n=$(cat $r/state 2>/dev/null) ";; esac; done)"

# 2. firmware and CP
say "staging: $(journalctl -b -u luma-fold-gnss-stage --no-pager -o cat 2>/dev/null | grep 'luma-gnss' | tr '\n' ';')"
f=/var/lib/luma-fold/firmware/google/s5400/kepler.bin
[ -f $f ] && say "kepler.bin: $(stat -c %s $f) bytes, sha256 $(sha256sum $f | cut -c1-16), header block $(od -An -tu4 -j4 -N4 $f | tr -d ' ')" || say "kepler.bin: not staged"
for i in $(seq 90); do
	[ -e /dev/wwan0gnss1 ] && break
	sleep 1
done
say "modem module: $(grep -c '^s5xxx_modem ' /proc/modules) loaded"
dmesg | grep -E "s5xxx" | grep -E "CP is ONLINE|GNSS|kepler|staged|ONLINE|crash|reset|error" | tail -30 | sed 's/.*s5xxx-modem [^:]*: //; s/^/luma-gnss: kmsg: /'
say "codeload replies: $(dmesg | grep -c 'GNSS boot cmd.*reply'), no reply: $(dmesg | grep -c 'GNSS boot cmd.*no reply')"
say "nodes: $(ls /dev/gnss* /dev/wwan0gnss* 2>/dev/null | tr '\n' ' ')"
if [ ! -e /dev/gnss0 ]; then finish "FAIL no /dev/gnss0"; fi
if [ ! -e /dev/wwan0gnss1 ]; then finish "FAIL no /dev/wwan0gnss1 (CP not ONLINE or no GNSS ports)"; fi

# 3. The product path: kepler-gnssd.service (udev-started once gnss0 and
# wwan0gnss1 exist) must sit idle until a client connects; a geoclue client at
# accuracy EXACT must make geoclue connect to the socket, start navigation and
# serve a location; navigation must stop again when the client leaves.
irqs() { awk '/gnss-kepler/ { s = 0; for (i = 2; i <= NF && $i ~ /^[0-9]+$/; i++) s += $i; print s; f = 1 } END { if (!f) print "n/a" }' /proc/interrupts; }
log=$d/gnssd-current.log; sock=/run/gnss-share.sock
umask 077
for i in $(seq 60); do [ -S $sock ] && break; sleep 1; done
say "kepler-gnssd.service: $(systemctl is-active kepler-gnssd.service) (restarts $(systemctl show -p NRestarts --value kepler-gnssd.service)), socket $([ -S $sock ] && stat -c '%U:%G %a' $sock || echo missing), irqs $(irqs)"
[ -S $sock ] || { journalctl -b -u kepler-gnssd --no-pager -o cat | tail -15 | sed 's/^/luma-gnss: unit: /'; tail -20 $log 2>/dev/null | mask | sed 's/^/luma-gnss: gnssd: /'; finish "FAIL kepler-gnssd not serving"; }
i0=$(irqs); sleep 20
idle=yes; grep -q 'navigation started' $log && idle=no
say "idle without clients: $idle (irqs $i0 -> $(irqs) over 20 s)"
say "geoclue: $(systemctl is-active geoclue.service) before the request; drop-ins: $(ls /etc/geoclue/conf.d 2>/dev/null | tr '\n' ' ')"
t0=$(cut -d. -f1 /proc/uptime)
go=/run/luma-fold-gnss-geoclue.out; rm -f $go $go.rc
# as the session user, like an app (root has no geoclue agent and its
# GetClient call timed out in gnss-v6); the script must be readable for it
u=$(loginctl list-sessions --no-legend 2>/dev/null | awk '$2 >= 1000 { print $3; exit }'); u=${u:-luma}
install -m 0644 $d/luma-fold-gnss-geoclue.py /run/luma-fold-gnss-geoclue.py
say "geoclue client runs as $u (uid $(id -u $u 2>/dev/null)); agents: $(pgrep -a -f 'geoclue-2.0/demos/agent|phosh' | awk '{print $2}' | xargs -n1 basename 2>/dev/null | sort -u | tr '\n' ' ')"
( runuser -u $u -- python3 -I /run/luma-fold-gnss-geoclue.py luma-gnss-test 300 30 > $go 2>&1; echo $? > $go.rc ) &
gp=$!
last=0
while kill -0 $gp 2>/dev/null; do
	sleep 5
	el=$(( $(cut -d. -f1 /proc/uptime) - t0 ))
	if [ $((el - last)) -ge 30 ]; then
		last=$el
		say "t+${el}s: geoclue $(systemctl is-active geoclue.service), gnssd clients $(grep -c 'NMEA client connected' $log), navigation $(grep -c 'navigation started' $log) started/$(grep -c 'navigation stopped' $log) stopped, irqs $(irqs), receiver fixes $(grep -c 'kepler-gnssd: fix' $log)"
	fi
done
wait $gp; grc=$(cat $go.rc 2>/dev/null); grc=${grc:-9}
mask < $go | sed 's/^/luma-gnss: /'
sleep 15
say "after the client left: navigation $(grep -c 'navigation started' $log) started/$(grep -c 'navigation stopped' $log) stopped, receiver fixes $(grep -c 'kepler-gnssd: fix' $log), irqs $(irqs)"
journalctl -b -u geoclue --no-pager -o cat 2>/dev/null | grep -v -i -E 'latitude|longitude' | tail -15 | mask | sed 's/^/luma-gnss: geoclue log: /'
echo "== kepler-gnssd log (setup, clients, states)"
grep -v -E 'kepler-gnssd: (fix|rx census|first rx)' $log | head -60 | mask | sed 's/^/luma-gnss: gnssd: /'
grep 'rx census' $log | tail -1 | sed 's/^/luma-gnss: /'
cp $d/rx-current.bin $d/rx-$boot.bin 2>/dev/null; cp $log $d/gnssd-$boot.log 2>/dev/null
say "raw output root-only: $d/gnssd-$boot.log, $d/rx-$boot.bin ($(stat -c %s $d/rx-$boot.bin 2>/dev/null) bytes)"
nfix=$(grep -c 'kepler-gnssd: fix' $log 2>/dev/null); nfix=${nfix:-0}
if [ $grc = 0 ]; then res="PASS geoclue served a location (receiver fixes $nfix, idle before the client: $idle)"
elif [ $nfix -gt 0 ]; then res="PARTIAL receiver fixed ($nfix) but geoclue served nothing (client rc $grc)"
elif grep -q 'navigation started' $log; then res="PARTIAL navigation ran, no fix (client rc $grc)"
else res="FAIL geoclue never started navigation (client rc $grc)"; fi
finish "$res"
