#!/bin/sh
# Fold modem bring-up, on every image that carries the s5xxx_modem module and
# the SIT modem stack: keep the modem module out of the early AOC loader (its
# firmware is staged from the root first), copy the module, the staging scripts
# and the modem stack -- ModemManager with the samsung-sit plugin, sit-proxy,
# exynos-modem -- to /var/lib/luma-fold, and add, in /run only, the udev rules,
# the units and the unit overrides that stage the firmware read-only and load
# the driver. The CP comes up ONLINE with the radio off (GNSS needs only the
# CP); nothing here enables the modem.
# Daily boots stop there. luma.fold_debug=1 boots also get the verbose daemons,
# the modem test unit and the auto-reboot; the test switches the radio on only
# with luma.fold_radio=1.
m=/luma-fold/modules
o=/luma-fold/modem
d=/sysroot/var/lib/luma-fold/modem
s=/sysroot/var/lib/luma-fold/modem-stack
e=/sysroot/etc/systemd/system
r=/run/systemd/system
rm -f $e/luma-fold-modem*.service $e/multi-user.target.wants/luma-fold-modem*.service
# The Wi-Fi/BT radio exerciser leaves its unit on the root; its own hook removes
# it on boots without luma.fold_radiotest=1, and so does this one.
if ! grep -q "luma.fold_radiotest=1" /proc/cmdline; then
	rm -f $e/luma-fold-radiotest.service $e/luma-fold-radiotest.sh $e/multi-user.target.wants/luma-fold-radiotest.service \
		$e/luma-fold-autoreboot.service.d/90-luma-fold-radiotest.conf $e/luma-fold-radio-diag.service.d/90-luma-fold-radiotest.conf
fi
if [ -f $m/s5xxx_modem.ko ]; then
	mkdir -p $o && mv $m/s5xxx_modem.ko $o/
	grep -v '^s5xxx_modem.ko$' $m/load-order > $m/load-order.new && mv $m/load-order.new $m/load-order
fi
[ -f $o/s5xxx_modem.ko ] || exit 0
[ -d /sysroot/var ] || exit 0
dbg=; grep -q "luma.fold_debug=1" /proc/cmdline && dbg=1
mkdir -p $d $r/multi-user.target.wants $r/ModemManager.service.d /run/udev/rules.d /run/NetworkManager/conf.d
chmod 700 $d
rm -f $d/*.py $d/*.json $d/luma-fold-modem-test.sh $d/calltest.env $d/esim-download.env $d/aosp-carrier-defaults.txt
# luma-fold-modem.sh needs the board/RF table and super-partition helpers on every boot
cp $o/s5xxx_modem.ko $o/luma-fold-modem.sh $o/luma-fold-modem-imsdb.sh $o/luma-fold-modem.py $o/luma-fold-modem-lp.py $d/
[ -f $o/aosp-carrier-defaults.txt ] && cp $o/aosp-carrier-defaults.txt $d/
if [ -n "$dbg" ]; then
	cp $o/*.sh $o/*.py $o/*.json $d/
	# busybox (musl) and its loader for ping/ping6; the root image has no ping
	[ -x /bin/busybox ] && cp /bin/busybox $d/busybox
	[ -e /lib/ld-musl-aarch64.so.1 ] && cp -L /lib/ld-musl-aarch64.so.1 $d/ld-musl-aarch64.so.1
	[ -f $o/calltest.env ] && install -m 0600 $o/calltest.env $d/calltest.env
	[ -f $o/esim-download.env ] && install -m 0600 $o/esim-download.env $d/esim-download.env
fi
loglevel=; [ -n "$dbg" ] && loglevel=" --log-level=DEBUG"
# The modem stack from the RPMs, run from /var unless the root carries the
# same binaries (then the installed ones run; the units and rules below are
# this image's in either case).
mode=none; P=/usr
if [ -f /luma-fold/modem-stack/SHA256SUMS ]; then
	if cmp -s /luma-fold/modem-stack/usr/bin/sit-proxy /sysroot/usr/bin/sit-proxy &&
	   cmp -s /luma-fold/modem-stack/usr/bin/ModemManager /sysroot/usr/bin/ModemManager; then
		mode=installed
		printf '[Service]\nExecStart=\nExecStart=/usr/bin/ModemManager%s\n' "$loglevel" > $r/ModemManager.service.d/luma-fold-modem.conf
	else
		mode=var
		if ! cmp -s /luma-fold/modem-stack/SHA256SUMS $s/SHA256SUMS; then
			rm -rf $s.new
			cp -a /luma-fold/modem-stack $s.new && rm -rf $s && mv $s.new $s
		fi
		P=/var/lib/luma-fold/modem-stack/usr
		cat > $r/ModemManager.service.d/luma-fold-modem.conf <<CONF
[Service]
Environment=LD_LIBRARY_PATH=$P/lib64
ExecStart=
ExecStart=$P/bin/ModemManager --test-plugin-dir=$P/lib64/ModemManager$loglevel
CONF
	fi
	# debug boots: the daemons say what they serve (SIT names and status only,
	# never payloads or subscriber data)
	v=; [ -n "$dbg" ] && v=" -v"
	sed "s,/usr/bin/,$P/bin/,; s,/sit-proxy\$,/sit-proxy$v," $o/sit-proxy.service > $r/sit-proxy.service
	sed "s,/usr/bin/,$P/bin/,; s,^EnvironmentFile=.*,Environment=EXYNOS_MODEM_OPTS=\"${v# }${v:+ }--uecap-dir /var/lib/luma-fold/firmware/google/s5400/uecapconfig --file-root /var/lib/luma-fold/firmware/google/s5400\"," \
		$o/exynos-modem.service > $r/exynos-modem.service
	cp $o/77-exynos-modem-ports.rules /run/udev/rules.d/
	# Bring-up: luma.fold_uecap_raw=1 serves carrier capability files unfiltered.
	if grep -q "luma.fold_uecap_raw=1" /proc/cmdline; then
		mkdir -p $r/exynos-modem.service.d
		printf '[Service]\nEnvironment=EXYNOS_MODEM_UECAP_RAW=1\n' > $r/exynos-modem.service.d/luma-fold-uecap-raw.conf
	fi
fi
echo $mode > /run/luma-fold-modem.stack
# Bring-up: luma.fold_attach_apn=<apn> forces the plugin's initial attach APN.
apn=$(sed -n 's/.*luma\.fold_attach_apn=\([A-Za-z0-9._-]*\).*/\1/p' /proc/cmdline)
# Mobile mode, daily boots only: "calls" attaches on ims (calls and texts over
# IMS, data on Wi-Fi), "data" on the data APN (mobile data, no calls or texts:
# this CP refuses a second PDN with cause 2253). luma-fold-mobile-mode saves
# the choice in /var/lib/luma-fold/mobile-mode (mode=, optional apn=);
# luma.fold_mobile_mode=calls|data and luma.fold_data_apn=<apn> override it.
# luma.fold_attach_apn still forces the attach APN in either mode.
mmode=; dapn=tello
if [ -z "$dbg" ]; then
	mf=/sysroot/var/lib/luma-fold/mobile-mode
	mmode=$(sed -n -E 's/^mode=(calls|data)$/\1/p' $mf 2>/dev/null | tail -n 1)
	mv=$(sed -n -E 's/^apn=([A-Za-z0-9._-]{1,62})$/\1/p' $mf 2>/dev/null | tail -n 1); [ -n "$mv" ] && dapn=$mv
	mv=$(sed -n -E 's/.*luma\.fold_mobile_mode=(calls|data).*/\1/p' /proc/cmdline); [ -n "$mv" ] && mmode=$mv
	mv=$(sed -n -E 's/.*luma\.fold_data_apn=([A-Za-z0-9._-]+).*/\1/p' /proc/cmdline); [ -n "$mv" ] && dapn=$mv
	[ -n "$mmode" ] || mmode=calls
fi
# Debug boots: the attach APN only from the flag.
if [ -n "$dbg" ] && [ -n "$apn" ]; then
	printf '[Service]\nEnvironment=MM_SIT_INITIAL_ATTACH_APN=%s\n' "$apn" > $r/ModemManager.service.d/luma-fold-attach-apn.conf
fi
# Bring-up: luma.fold_volte=1 turns the CP's voice operation (VoLTE) on and
# sends the VoLTE provisioning update before radio-on; luma.fold_usage=1|2 sets
# the UE usage setting (voice/data centric).
# mmenv collects the flags; each mode's defaults go before them (below), so a
# flag overrides a default.
mmenv=
grep -q "luma.fold_volte=1" /proc/cmdline && mmenv="$mmenv MM_SIT_VOICE_OPERATION=1 MM_SIT_VOLTE_PROVISION=1"
usage=$(sed -n 's/.*luma\.fold_usage=\([12]\).*/\1/p' /proc/cmdline)
[ -n "$usage" ] && mmenv="$mmenv MM_SIT_USAGE_SETTING=$usage"
# luma.fold_usage_restore=N: put the usage setting back to N before radio-off
# unless the stack registered.
urest=$(sed -n 's/.*luma\.fold_usage_restore=\([12]\).*/\1/p' /proc/cmdline)
[ -n "$urest" ] && mmenv="$mmenv MM_SIT_USAGE_RESTORE=$urest"
# luma.fold_vzw_profiles=1: Verizon's data profile table from the phone's own
# CarrierSettings (verizon_us.pb: profile id, APN, Android APN-type bits, auth,
# max conns, max-conns time), sent before radio-on.
grep -q "luma.fold_vzw_profiles=1" /proc/cmdline && mmenv="$mmenv MM_SIT_DATA_PROFILES=0:VZWINTERNET:d:0:20:300;3:VZWADMIN:20:0:20:300;2:IMS:140:0:20:300;4:VZWAPP:82:0:20:300"
# luma.fold_tello_profiles=1: Tello's data profiles from tello_us.pb (tello:
# default/supl/mms IPv4v6; ims), so a 5G SA PDU session has a traffic
# descriptor the network's URSP matches (else MATCH_ALL_RULE_NOT_ALLOWED 2253).
grep -q "luma.fold_tello_profiles=1" /proc/cmdline && mmenv="$mmenv MM_SIT_DATA_PROFILES=0:tello:7:0:0:0;2:ims:40:0:0:0"
# luma.fold_tello_profiles=ia: the same with the IA bit (0x100) on tello, as an
# attach-capable default profile (T-Mobile's default,supl,mms,ia).
grep -q "luma.fold_tello_profiles=ia" /proc/cmdline && mmenv="$mmenv MM_SIT_DATA_PROFILES=0:tello:107:0:0:0;2:ims:40:0:0:0"
# luma.fold_carrier_prop=N: publish carrier ID N to the CP (the manifest stays
# the matched profile); Tello's Android carrier ID is 2578.
cprop=$(sed -n 's/.*luma\.fold_carrier_prop=\([0-9]*\).*/\1/p' /proc/cmdline)
[ -n "$cprop" ] && mmenv="$mmenv MM_SIT_CARRIER_ID_PROP=$cprop"
# luma.fold_slice_sst=N / luma.fold_pdu_session=N: an explicit S-NSSAI (SST N)
# and PDU session ID in SETUP_DATA_CALL, for 5G SA URSP matching.
sst=$(sed -n 's/.*luma\.fold_slice_sst=\([0-9]*\).*/\1/p' /proc/cmdline)
[ -n "$sst" ] && mmenv="$mmenv MM_SIT_SLICE_SST=$sst"
psi=$(sed -n 's/.*luma\.fold_pdu_session=\([0-9]*\).*/\1/p' /proc/cmdline)
[ -n "$psi" ] && mmenv="$mmenv MM_SIT_PDU_SESSION_ID=$psi"
# luma.fold_td_match_all=0|1: the data call's match-all-rule flag.
tdm=$(sed -n 's/.*luma\.fold_td_match_all=\([01]\).*/\1/p' /proc/cmdline)
[ -n "$tdm" ] && mmenv="$mmenv MM_SIT_TD_MATCH_ALL=$tdm"
# luma.fold_attach_ims=1: the initial attach APN goes out as an IMS APN.
grep -q "luma.fold_attach_ims=1" /proc/cmdline && mmenv="$mmenv MM_SIT_INITIAL_ATTACH_IMS=1"
# NR mode before radio-on: 0 (no NR) by default, so data uses LTE/EPS -- this
# network refuses 5G SA PDU sessions without URSP rules (cause 2253);
# luma.fold_nr_mode=N overrides.
nrm=$(sed -n 's/.*luma\.fold_nr_mode=\([0-9]\).*/\1/p' /proc/cmdline)
mmenv="$mmenv MM_SIT_NR_MODE=${nrm:-0}"
# luma.fold_ims_pdp=ipv6: the IMS PDN asks for IPv6 only.
grep -q "luma.fold_ims_pdp=ipv6" /proc/cmdline && mmenv="$mmenv MM_SIT_IMS_PDP=ipv6"
# luma.fold_setup_layout=legacy|vtwo: the bearer's SETUP_DATA_CALL in the
# stock RIL's older 246- or 638-byte layout.
lay=$(sed -n 's/.*luma\.fold_setup_layout=\([a-z]*\).*/\1/p' /proc/cmdline)
[ -n "$lay" ] && mmenv="$mmenv MM_SIT_SETUP_LAYOUT=$lay"
# luma.fold_ims_td=<v1,v2,...>: one IMS traffic-descriptor variant per attempt,
# one attempt per variant, 15 s apart.
imstd=$(sed -n 's/.*luma\.fold_ims_td=\([a-z0-9,]*\).*/\1/p' /proc/cmdline)
if [ -n "$imstd" ]; then
	n=$(echo "$imstd" | tr ',' '\n' | grep -c .)
	mmenv="$mmenv MM_SIT_IMS_TD_VARIANTS=$imstd MM_SIT_IMS_ATTEMPTS=$n MM_SIT_IMS_RETRY_SECS=15"
fi
# Single standby (one live modem, the eUICC on logical stack 0) is the default,
# as Android runs an eSIM-only phone; luma.fold_dsds=1 keeps two live stacks.
grep -q "luma.fold_dsds=1" /proc/cmdline || mmenv="$mmenv MM_SIT_SINGLE_MODEM=1"
# Stack 1 has no SIM in single standby, so ModemManager's modem for it only
# fails (sim-missing), and Phone and Messages refuse to choose between two
# modems. Keep its ports out of ModemManager.
if ! grep -q "luma.fold_dsds=1" /proc/cmdline; then
	mkdir -p /run/udev/rules.d
	cat > /run/udev/rules.d/78-luma-fold-single-standby.rules <<"UDEV"
SUBSYSTEM=="wwan", KERNEL=="wwan[0-9]*sit1", ENV{ID_MM_PORT_IGNORE}="1", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="net", KERNEL=="rmnet1[5-9]|rmnet2[0-9]", ENV{ID_MM_PORT_IGNORE}="1", ENV{ID_MM_DEVICE_IGNORE}="1"
UDEV
fi
[ -n "$dbg" ] && [ -n "$mmenv" ] && printf '[Service]\nEnvironment=%s\n' "${mmenv# }" > $r/ModemManager.service.d/luma-fold-voice-mode.conf
# Screen-on SIT policy: with the plugin's screen-off default the CP holds every
# non-wake indication and late answer until the AP next transmits (v3z: 180 s
# of nothing, then a flood).
printf '[Service]\nEnvironment=MM_SIT_SCREEN_ON=1\n' > $r/ModemManager.service.d/luma-fold-screen-on.conf
# NetworkManager leaves the modem alone in calls mode and on debug boots, so
# nothing enables it behind the user's back: the radio comes on only through
# ModemManager (mmcli -e, the radio unit below, or the debug test with
# luma.fold_radio=1).
nm_unmanaged() {
	printf '[device-luma-fold-modem]\nmatch-device=type:gsm,type:cdma\nmanaged=0\n' > $1
}
# The "Mobile data" connection. Calls mode and debug boots: autoconnect off and
# the APN from mobile-broadband-provider-info (gsm.auto-config); inert while the
# modem is unmanaged. Data mode: the data APN, autoconnect on.
nm_connection() { # <file> <apn or empty>
	cat > $1 <<NMC
[connection]
id=Mobile data
uuid=5f0c3b1e-6a4d-4c43-9d55-3f0d2a8c7e11
type=gsm
autoconnect=$([ -n "$2" ] && echo true || echo false)
autoconnect-retries=0

[gsm]
$([ -n "$2" ] && echo "apn=$2" || echo "auto-config=true")

[ipv4]
method=auto

[ipv6]
method=auto
addr-gen-mode=default
NMC
	chmod 600 $1
}
mkdir -p /run/NetworkManager/system-connections
if [ -n "$dbg" ]; then
	nm_unmanaged /run/NetworkManager/conf.d/90-luma-fold-modem.conf
	nm_connection /run/NetworkManager/system-connections/luma-mobile-data.nmconnection ""
else
	# Both modes' files, so luma-fold-mobile-mode switches by copying a set:
	# /run/luma-fold-mobile/<mode>/ holds the ModemManager drop-ins (that mode's
	# defaults, then the flags), the NetworkManager rule if any and the
	# connection. Data mode: tello's profiles and the plain LTE attach of the
	# tmo6/tmo8 data runs (attach tello, profiles 0:tello:7 + 2:ims:40, NR mode
	# 0); IMS gets one try (it is refused there). Without NetworkManager's wwan
	# plugin the modem stays unmanaged and luma-fold-mobile-mode runs the bearer
	# itself (backend "helper").
	md=/run/luma-fold-mobile
	rm -rf $md && mkdir -p $md/calls $md/data
	nmb=helper; ls /sysroot/usr/lib64/NetworkManager/*/libnm-device-plugin-wwan.so >/dev/null 2>&1 && nmb=nm
	printf '[Service]\nEnvironment=MM_SIT_INITIAL_ATTACH_APN=%s\n' "${apn:-ims}" > $md/calls/luma-fold-attach-apn.conf
	printf '[Service]\nEnvironment=MM_SIT_INITIAL_ATTACH_APN=%s\n' "${apn:-$dapn}" > $md/data/luma-fold-attach-apn.conf
	printf '[Service]\nEnvironment=%s\n' "MM_SIT_INITIAL_ATTACH_IMS=1 MM_SIT_IMS_PDP=ipv6 MM_SIT_DATA_PROFILES=2:ims:40:0:0:0$mmenv" > $md/calls/luma-fold-voice-mode.conf
	printf '[Service]\nEnvironment=%s\n' "MM_SIT_DATA_PROFILES=0:$dapn:7:0:0:0;2:ims:40:0:0:0 MM_SIT_IMS_ATTEMPTS=1$mmenv" > $md/data/luma-fold-voice-mode.conf
	nm_unmanaged $md/calls/90-luma-fold-modem.conf
	[ $nmb = nm ] || nm_unmanaged $md/data/90-luma-fold-modem.conf
	nm_connection $md/calls/luma-mobile-data.nmconnection ""
	nm_connection $md/data/luma-mobile-data.nmconnection $([ $nmb = nm ] && echo $dapn)
	echo $nmb > $md/data/backend
	echo $dapn > $md/data/apn
	echo $mmode > $md/mode
	cp $md/$mmode/luma-fold-attach-apn.conf $md/$mmode/luma-fold-voice-mode.conf $r/ModemManager.service.d/
	[ -f $md/$mmode/90-luma-fold-modem.conf ] && cp $md/$mmode/90-luma-fold-modem.conf /run/NetworkManager/conf.d/
	install -m 600 $md/$mmode/luma-mobile-data.nmconnection /run/NetworkManager/system-connections/
	mkdir -p /run/luma
	printf '{"mode": "%s", "state": "ready", "calls": %s, "data": %s}\n' $mmode \
		$([ $mmode = calls ] && echo true || echo false) $([ $mmode = data ] && echo true || echo false) > /run/luma/mobile-mode.json
	# The switch service (D-Bus org.projectluma.MobileMode1; Mobile mode quick setting).
	if [ -f $o/luma-fold-mobile-mode.py ]; then
		cp $o/luma-fold-mobile-mode.py $d/
		mkdir -p /sysroot/etc/dbus-1/system.d
		cp $o/org.projectluma.MobileMode1.conf /sysroot/etc/dbus-1/system.d/
		cp $o/luma-fold-mobile-mode.service $r/
		ln -sf ../luma-fold-mobile-mode.service $r/multi-user.target.wants/luma-fold-mobile-mode.service
	fi
	# MMS (session side): mmsd-tng's MMSC for the data APN, and MMS windows in
	# calls mode. The session user cannot read $d, so it lives in session/.
	if [ -f $o/luma-fold-mms.py ]; then
		install -d -m 755 /sysroot/var/lib/luma-fold/session
		install -m 644 $o/luma-fold-mms.py /sysroot/var/lib/luma-fold/session/
		mkdir -p /run/systemd/user/default.target.wants
		cp $o/luma-fold-mms.service /run/systemd/user/
		ln -sf ../luma-fold-mms.service /run/systemd/user/default.target.wants/luma-fold-mms.service
	fi
fi
# The .25 base image's FP6 userspace IMS (luma-fp6-imsd owns net.catcrafts.IMS1)
# and its session dialer are for the Fairphone modem; keep them off this modem.
# This hook runs before the root's systemd starts, so "systemctl mask" reaches
# nothing; mask through /run, which the root's systemd reads at startup.
# system.control outranks /etc, in case the root ships the unit there.
mkdir -p $r /run/systemd/system.control /run/systemd/user
for u in luma-fp6-imsd.service imsd-dialerd.service; do
	ln -sf /dev/null $r/$u
	ln -sf /dev/null /run/systemd/system.control/$u
	ln -sf /dev/null /run/systemd/user/$u
done
# The session also starts imsd-dialerd from XDG autostart; on this modem it
# hears the bridge's calls and rings beside Prairie Phone, and keeps ringing
# after Phone answers.
a=/sysroot/etc/xdg/autostart/imsd-dialerd.desktop
if [ -f $a ] && ! grep -q '^Hidden=true' $a; then
	printf 'Hidden=true\n' >> $a
fi
# Prairie Phone dials and answers through net.catcrafts.IMS1; on this modem the
# bridge serves that name from ModemManager's Voice interface.
if [ -f $o/luma-fold-ims-bridge.py ]; then
	cp $o/luma-fold-ims-bridge.py $d/
	mkdir -p /sysroot/etc/dbus-1/system.d
	cp $o/net.catcrafts.IMS1.conf /sysroot/etc/dbus-1/system.d/net.catcrafts.IMS1.conf
	cp $o/luma-fold-ims-bridge.service $r/
	ln -sf ../luma-fold-ims-bridge.service $r/multi-user.target.wants/luma-fold-ims-bridge.service
fi
# Settings' eSIM page (luma-esim) reaches the eUICC through sit-lpa; after a
# profile switch it starts the card reload, which needs the SIT helper.
if [ -f $o/luma-esim-fold.conf ]; then
	cp $o/luma-fold-modem-sit.py $o/luma-fold-esim-switch.sh $d/
	cp $o/luma-fold-esim-switch.service $r/
	mkdir -p $r/luma-esim.service.d
	cp $o/luma-esim-fold.conf $r/luma-esim.service.d/50-luma-fold-sit.conf
fi
# Staging right after the AOC loader (not behind multi-user.target, whose
# oneshot units would hold it).
cat > $r/luma-fold-modem.service <<"UNIT"
[Unit]
Description=Fold modem firmware staging and driver load
After=luma-fold-aoc-load.service local-fs.target systemd-udevd.service ModemManager.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash /var/lib/luma-fold/modem/luma-fold-modem.sh
UNIT
ln -sf ../luma-fold-modem.service $r/multi-user.target.wants/luma-fold-modem.service
# Daily boots: switch the radio on for calls and texts once ModemManager has
# the SIM stack (the eSIM on logical stack 0, single standby). luma.fold_radio=0
# keeps it off.
if [ -z "$dbg" ] && ! grep -q "luma.fold_radio=0" /proc/cmdline; then
	cat > /run/luma-fold-modem-radio.sh <<SH
#!/bin/sh
export LD_LIBRARY_PATH=$P/lib64
for i in \$(seq 180); do
	$P/bin/mmcli -m exynos-modem-stack0 >/dev/null 2>&1 && break
	sleep 1
done
exec $P/bin/mmcli -m exynos-modem-stack0 --enable
SH
	chmod 755 /run/luma-fold-modem-radio.sh
	cat > $r/luma-fold-modem-radio.service <<"UNIT"
[Unit]
Description=Fold modem radio on (calls and texts over IMS)
After=ModemManager.service luma-fold-modem.service
Wants=ModemManager.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh /run/luma-fold-modem-radio.sh
UNIT
	ln -sf ../luma-fold-modem-radio.service $r/multi-user.target.wants/luma-fold-modem-radio.service
fi
[ -n "$dbg" ] || exit 0
# Debug boots only from here: the modem test and the auto-reboot.
mkdir -p $r/luma-fold-autoreboot.service.d
# Registration, bearer and the reports need more than the 180 s debug cycle.
rb=900; grep -q "luma.fold_calltest=1" /proc/cmdline && rb=960
printf '[Service]\nExecStart=\nExecStart=/bin/sh -c "sleep %s; systemctl reboot"\n' $rb > $r/luma-fold-autoreboot.service.d/luma-fold-modem.conf
cat > $r/luma-fold-modem-test.service <<"UNIT"
[Unit]
Description=Fold bring-up modem stack test
After=luma-fold-modem.service
[Service]
Type=oneshot
ExecStart=/bin/bash /var/lib/luma-fold/modem/luma-fold-modem-test.sh
UNIT
ln -sf ../luma-fold-modem-test.service $r/multi-user.target.wants/luma-fold-modem-test.service
