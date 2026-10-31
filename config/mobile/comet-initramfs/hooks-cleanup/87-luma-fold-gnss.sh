#!/bin/sh
# Fold GNSS (Samsung KEPLER receiver beside the S5400 CP). Needs the modem
# driver to bring the CP ONLINE: the driver then stages kepler.bin into CP
# shared memory and starts the receiver; the receiver's data link is SPI
# (gnss-kepler, /dev/gnss0) plus the CP's GNSS dump port (/dev/wwan0gnss1).
# This hook adds, in /run only:
#  - luma-fold-gnss-stage.service: kepler.bin from the phone's own vendor
#    partition, with the header fields stock gnssd sets from gps.cfg, into the
#    firmware root the modem driver reads, before the driver loads;
#  - kepler-gnssd.service, started by udev once both nodes exist and stopped
#    with them: NMEA on /run/gnss-share.sock (group geoclue). The receiver
#    navigates only while a client is connected, so GPS runs only while an app
#    asks geoclue for an exact location;
# and to /etc a geoclue drop-in that reads that socket.
# With luma.fold_debug=1 luma.fold_gnsstest=1 it also adds the bring-up test.
g=/luma-fold/gnss
d=/sysroot/var/lib/luma-fold/gnss
r=/run/systemd/system
gc=/sysroot/etc/geoclue/conf.d
test=
grep -q "luma.fold_debug=1" /proc/cmdline && grep -q "luma.fold_gnsstest=1" /proc/cmdline && test=1
[ -n "$test" ] || rm -f $gc/91-luma-fold-gnss-test.conf
if [ ! -x $g/kepler-gnssd ] || [ ! -d /sysroot/var ] ||
   { [ ! -f /luma-fold/modem/s5xxx_modem.ko ] && [ ! -f /luma-fold/modules/s5xxx_modem.ko ]; }; then
	rm -f $gc/90-luma-fold-gnss.conf
	exit 0
fi
mkdir -p $d $r/multi-user.target.wants /run/udev/rules.d $gc
chmod 700 $d
cp $g/kepler-gnssd $g/caimito-prologue.bin $g/*.sh $g/*.py $d/
cat > $r/luma-fold-gnss-stage.service <<"UNIT"
[Unit]
Description=Fold GNSS firmware staging (kepler.bin from vendor)
After=local-fs.target systemd-udevd.service
Before=luma-fold-modem.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash /var/lib/luma-fold/gnss/luma-fold-gnss-stage.sh
UNIT
ln -sf ../luma-fold-gnss-stage.service $r/multi-user.target.wants/luma-fold-gnss-stage.service
cat > $r/kepler-gnssd.service <<"UNIT"
[Unit]
Description=Samsung KEPLER GNSS receiver daemon (NMEA on /run/gnss-share.sock)
BindsTo=dev-gnss0.device dev-wwan0gnss1.device
After=dev-gnss0.device dev-wwan0gnss1.device
[Service]
UMask=0077
ExecStart=/var/lib/luma-fold/gnss/kepler-gnssd --prologue /var/lib/luma-fold/gnss/caimito-prologue.bin --device /dev/gnss0 --dump-device /dev/wwan0gnss1 --socket /run/gnss-share.sock --socket-group geoclue
Restart=on-failure
RestartSec=10
UNIT
cat > /run/udev/rules.d/99-luma-fold-kepler-gnssd.rules <<"RULE"
# The receiver is usable once the KEPLER SPI node and the CP's GNSS dump port
# both exist (the dump port appears when the CP is ONLINE, after the codeload).
ACTION=="add", SUBSYSTEM=="gnss", KERNEL=="gnss0", TAG+="systemd"
ACTION=="add", SUBSYSTEM=="wwan", KERNEL=="wwan0gnss1", TAG+="systemd", ENV{SYSTEMD_WANTS}+="kepler-gnssd.service"
RULE
cat > $gc/90-luma-fold-gnss.conf <<"CONF"
# Fold GNSS: positions from kepler-gnssd's NMEA socket.
[network-nmea]
enable=true
nmea-socket=/run/gnss-share.sock
CONF
[ -n "$test" ] || exit 0

# Bring-up test: kepler-gnssd logs setup, record census and fixes (root-only
# file) and keeps every received record; the test asks geoclue for a location.
mkdir -p $r/kepler-gnssd.service.d $r/luma-fold-autoreboot.service.d
rm -f $d/gnssd-current.log $d/rx-current.bin
cat > $r/kepler-gnssd.service.d/90-luma-fold-gnss-test.conf <<"CONF"
[Service]
ExecStart=
ExecStart=/var/lib/luma-fold/gnss/kepler-gnssd -v --prologue /var/lib/luma-fold/gnss/caimito-prologue.bin --device /dev/gnss0 --dump-device /dev/wwan0gnss1 --socket /run/gnss-share.sock --socket-group geoclue --rx-capture /var/lib/luma-fold/gnss/rx-current.bin
StandardError=append:/var/lib/luma-fold/gnss/gnssd-current.log
CONF
cat > $gc/91-luma-fold-gnss-test.conf <<"CONF"
# Fold GNSS bring-up test client (luma.fold_gnsstest=1 boots only).
[luma-gnss-test]
allowed=true
system=true
users=
CONF
cat > $r/luma-fold-gnss-test.service <<"UNIT"
[Unit]
Description=Fold GNSS bring-up test
After=luma-fold-gnss-stage.service luma-fold-modem.service
[Service]
Type=oneshot
TimeoutStartSec=900
ExecStart=/bin/bash /var/lib/luma-fold/gnss/luma-fold-gnss-test.sh
UNIT
ln -sf ../luma-fold-gnss-test.service $r/multi-user.target.wants/luma-fold-gnss-test.service
# The modem stack's own test unit (hook 82) is not needed here, and its read of
# /sys/kernel/debug/pinctrl/*/pins takes an SError in samsung_pinconf_rw that
# panics the kernel (gnss-v4, 32 s): keep it off.
rm -f $r/luma-fold-modem-test.service $r/multi-user.target.wants/luma-fold-modem-test.service
# The test reboots itself when done; this is only the backstop.
printf '[Service]\nExecStart=\nExecStart=/bin/sh -c "sleep 600; systemctl reboot"\n' > $r/luma-fold-autoreboot.service.d/zz-luma-fold-gnss.conf
exit 0
