#!/bin/bash
# Fold bring-up: exercise the modem stack (ModemManager's samsung-sit plugin
# through sit-proxy, exynos-modem on the vendor channels) and report to the
# journal (unit stdout plus logger), every step stamped with the uptime. The
# radio stays off unless the kernel command line carries luma.fold_radio=1;
# then the modem holding the SIM is enabled, registration awaited, a data
# bearer brought up and pinged, HTTPS fetched through it, a DNS query sent to
# the bearer's resolver, the SMS storage state and the IMS lines reported,
# and the bearer disconnected and the modem disabled again. Nothing here
# places a call or sends a message. No identifier is printed: ModemManager
# key/value output is whitelisted and every run of nine or more digits masked.
T=luma-fold-modem-test
mode=$(cat /run/luma-fold-modem.stack 2>/dev/null || echo none)
if [ "$mode" = var ]; then
	P=/var/lib/luma-fold/modem-stack
	export LD_LIBRARY_PATH=$P/usr/lib64
	MMCLI=$P/usr/bin/mmcli
else
	MMCLI=/usr/bin/mmcli
fi
radio=0
grep -q "luma.fold_radio=1" /proc/cmdline && radio=1
up() { cut -d' ' -f1 /proc/uptime; }
say() { echo "## [$(up)s] $*"; logger -t $T -- "[$(up)s] $*"; }
step() { say "step $*"; }
mask() { sed -E 's/[0-9]{9,}/<digits>/g'; }
# ModemManager key/value output without identifiers
mmk() {
	timeout 25 "$MMCLI" "$@" -K 2>&1 | grep -v -E 'equipment-identifier|device-identifier|own-numbers|\.imsi|\.iccid|\.eid|gid1|gid2|emergency-numbers|\.number ' | mask
}
val() { sed -n "s/^$1 *: *//p" | head -1; }
state_line() {	# $1: modem index
	mmk -m $1 | grep -E 'generic\.(state|power-state|access-technologies|signal-quality\.value)|3gpp\.(registration-state|operator-name|operator-code|packet-service-state)' | sed 's/  */ /g; s/^modem\.//' | tr '\n' ';'
}
kmsg() { dmesg | grep -E "s5xxx|12100000.pcie|cp-power|wwan|rmnet" | tail -n "$1" | mask; }

abort() {	# $1: reason; the lines that explain it follow, then the unit ends
	say "ABORT: $1"
	echo "== kernel: CP power, PCIe, modem driver"
	dmesg | grep -E "cp-power|12100000.pcie|0001:01:00|s5xxx" | tail -40 | mask
	echo "== units"
	systemctl --no-pager --plain list-units "luma-fold-modem*" sit-proxy.service exynos-modem.service ModemManager.service 2>&1 | head -8
	journalctl -b -u exynos-modem -u sit-proxy --no-pager -o short-monotonic 2>/dev/null | tail -20 | mask
	say "done (aborted)"
	exit 1
}
# Call test (luma.fold_calltest=1 candidates only; the number comes from the
# root-only calltest.env the call-test build adds): once the modem is
# registered, one SMS and one voice call to that single number, every state
# change logged; the number itself is never printed (masked like any run of
# nine or more digits). No audio is routed: callaudiod and the session's call
# UI are stopped for the test.
calltest() {	# $1: modem $2: registration state
	local m=$1 st=$2 N= text sid cid i cs last t0 ok
	grep -q "luma.fold_calltest=1" /proc/cmdline || return 0
	step "call test"
	[ -f /var/lib/luma-fold/modem/calltest.env ] && N=$(sed -n 's/^CALLTEST_NUMBER=//p' /var/lib/luma-fold/modem/calltest.env)
	if ! echo "$N" | grep -q -E '^\+1[0-9]{10}$'; then say "call test skipped: no valid test number"; return 0; fi
	case $st in home|roaming) ;; *) say "call test skipped: not registered ($st)"; return 0;; esac
	[ -e /run/luma-fold-calltest.done ] && { say "call test skipped: already run this boot"; return 0; }
	: > /run/luma-fold-calltest.done
	systemctl --user -M luma@ mask --runtime --now callaudiod.service >/dev/null 2>&1 && say "callaudiod stopped for the test"
	systemctl mask --runtime --now luma-fold-callaudio.service >/dev/null 2>&1 && say "luma-fold-callaudio stopped for the test (no amps, no telephony PCM)"
	pkill -u luma -x gnome-calls >/dev/null 2>&1 && say "session call UI stopped for the test"
	step "call test: waiting up to 45 s for IMS registration"
	for i in $(seq 45); do
		journalctl -b -u ModemManager --no-pager -o cat 2>/dev/null | grep -q "IMS registered" && break
		sleep 1
	done
	say "IMS: $(journalctl -b -u ModemManager --no-pager -o cat 2>/dev/null | grep -E 'IMS (registered|deregistered|provisioned|PDN up|stopped)|no IMS configuration|VoPS' | tail -3 | tr '\n' ';' | mask)"
	text="Luma Fold test $(cut -c1-8 /proc/sys/kernel/random/boot_id)"
	step "sms: create and send one message"
	sid=$(timeout 20 "$MMCLI" -m $m --messaging-create-sms="number='$N',text='$text'" 2>&1 | grep -o '/SMS/[0-9]*' | sed 's,.*/,,')
	if [ -z "$sid" ]; then
		say "sms: could not create the message"
	else
		t0=$(up)
		timeout 75 "$MMCLI" -s $sid --send --timeout=60 2>&1 | mask
		say "sms send result after $(awk -v a=$t0 -v b=$(up) 'BEGIN{printf "%.1f", b-a}')s: $(mmk -s $sid | grep -E 'sms\.properties\.(state|delivery-state|message-reference|storage)' | sed 's/  */ /g' | tr '\n' ';')"
		journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -i -E "sms|send" | grep -v -i "imsi" | tail -8 | mask
	fi
	step "call: one voice call, up to 40 s for ringing or answer, then hang up"
	cid=$(timeout 20 "$MMCLI" -m $m --voice-create-call="number=$N" 2>&1 | grep -o '/Call/[0-9]*' | sed 's,.*/,,')
	if [ -z "$cid" ]; then
		say "call: could not create the call"
		return 0
	fi
	timeout 30 "$MMCLI" -o $cid --start 2>&1 | mask
	last=; ok=; t0=$(up)
	for i in $(seq 80); do
		cs=$(mmk -o $cid | grep -E 'call\.properties\.(state|state-reason|direction|audio-port)' | sed 's/  */ /g' | tr '\n' ';')
		[ "$cs" != "$last" ] && { say "call state after $(awk -v a=$t0 -v b=$(up) 'BEGIN{printf "%.1f", b-a}')s: $cs"; last=$cs; }
		case $cs in
		*"state : active"*) ok=answered; sleep 3; break;;
		*"state : terminated"*) break;;
		*"state : ringing-out"*) ok=ringing;;
		esac
		sleep 0.5
	done
	timeout 20 "$MMCLI" -o $cid --hangup 2>&1 | mask
	sleep 2
	say "call ended (${ok:-no ring}): $(mmk -o $cid | grep -E 'call\.properties\.(state|state-reason)' | sed 's/  */ /g' | tr '\n' ';')"
	timeout 10 "$MMCLI" -m $m --voice-delete-call=$cid >/dev/null 2>&1
	journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -i -E "call|dial|ims" | grep -v -i "imsi" | tail -15 | mask
}

# eSIM (eUICC) through sit-lpa + the relocatable lpac: read-only report, and a
# download only with luma.fold_esim_download=1 and the root-only
# esim-download.env (ACTIVATION_CODE=..., optional CONFIRMATION_CODE=...).
LPAC=$P/usr/libexec/luma-lpac/executables/lpac
SITLPA=$P/usr/bin/sit-lpa
[ -x "$SITLPA" ] || SITLPA=/usr/bin/sit-lpa
lpa() {	# $1: logical stack; the rest: lpac command
	local port=$1; shift
	timeout -s KILL 120 "$SITLPA" --port "$port" --lpac "$LPAC" -- "$@" 2>/run/luma-fold-lpa.err
}
lpa_summary() {	# stdin: lpac JSON lines; prints states and names, masks EID/ICCID
	python3 -c '
import json, sys
for line in sys.stdin:
    try:
        j = json.loads(line)
    except ValueError:
        continue
    p = j.get("payload", {})
    if j.get("type") == "progress":
        print("  progress:", p.get("message"))
        continue
    print("  result: code %s message %s" % (p.get("code"), p.get("message")))
    d = p.get("data")
    if isinstance(d, dict):
        eid = d.get("eidValue") or ""
        if eid:
            print("  EID: %d digits, ...%s" % (len(eid), eid[-2:]))
        i2 = d.get("EUICCInfo2") or {}
        for k in ("profileVersion", "svn", "euiccFirmwareVer", "extCardResource", "ppVersion", "sasAcreditationNumber"):
            if k in i2:
                print("  %s: %s" % (k, i2[k]))
        cfg = d.get("EuiccConfiguredAddresses") or {}
        if cfg:
            print("  SM-DP+ default: %s, SM-DS: %s" % (cfg.get("defaultDpAddress"), cfg.get("rootDsAddress")))
    elif isinstance(d, list):
        print("  profiles: %d" % len(d))
        for e in d:
            iccid = e.get("iccid") or ""
            print("  - ICCID ...%s  state %s  class %s  provider %s" % (iccid[-2:], e.get("profileState"), e.get("profileClass"), e.get("serviceProviderName")))
'
}
esim_report() {	# $1: logical stack, $2: label
	step "eSIM: eUICC info and profiles on stack $1 ($2; read-only)"
	lpa "$1" chip info | lpa_summary
	lpa "$1" profile list | lpa_summary
	grep -v -E "[0-9]{9,}" /run/luma-fold-lpa.err | tail -5
}
esim_download() {	# $1: logical stack
	local f=/var/lib/luma-fold/modem/esim-download.env
	grep -q "luma.fold_esim_download=1" /proc/cmdline || return 0
	if [ ! -s "$f" ]; then say "eSIM download requested but $f is missing"; return 0; fi
	step "eSIM: profile download on stack $1 (activation code from the root-only file)"
	say "default route: $(ip route show default 2>/dev/null | head -1 | sed 's/ proto.*//')"
	# read literally: an activation code contains "$" (LPA:1$host$id), so the
	# file is never sourced
	( ACTIVATION_CODE=$(sed -n 's/^ACTIVATION_CODE=//p' "$f" | head -1 | sed "s/^[\"']//; s/[\"']\$//")
	  CONFIRMATION_CODE=$(sed -n 's/^CONFIRMATION_CODE=//p' "$f" | head -1 | sed "s/^[\"']//; s/[\"']\$//")
	  if [ -z "${ACTIVATION_CODE:-}" ]; then echo "  no ACTIVATION_CODE in the file" >&2; exit 0; fi
	  case "$ACTIVATION_CODE" in LPA:1\$*\$*) ;; *) echo "  ACTIVATION_CODE is not an LPA:1\$...\$... code" >&2; exit 0 ;; esac
	  if [ -n "${CONFIRMATION_CODE:-}" ]; then
		lpa "$1" profile download -a "$ACTIVATION_CODE" -c "$CONFIRMATION_CODE"
	  else
		lpa "$1" profile download -a "$ACTIVATION_CODE"
	  fi ) | lpa_summary
	grep -v -E "[0-9]{9,}|LPA:|lpa:|\\$" /run/luma-fold-lpa.err | tail -5
	esim_report "$1" "after the download"
}

lpa_profiles() {	# stdin: lpac profile list JSON; stdout: "ICCID STATE PROVIDER" (for the shell, never logged)
	python3 -c '
import json, sys
for line in sys.stdin:
    try:
        j = json.loads(line)
    except ValueError:
        continue
    d = j.get("payload", {}).get("data")
    if isinstance(d, list):
        for e in d:
            print(e.get("iccid", ""), e.get("profileState", ""), (e.get("serviceProviderName") or "-").replace(" ", "_"))
'
}
esim_switch() {	# $1: logical stack; luma.fold_esim_switch=<provider>: download (if absent, -dl only) and enable that profile
	local lp=$1 list tmo tst vzw vst want
	want=$(sed -n 's/.*luma\.fold_esim_switch=\([A-Za-z0-9_-]*\).*/\1/p' /proc/cmdline | tr A-Z a-z)
	[ -n "$want" ] || return 0
	list=$(lpa "$lp" profile list | lpa_profiles)
	vzw=$(echo "$list" | awk '$1 ~ /^891480/ {print $1; exit}'); vst=$(echo "$list" | awk '$1 ~ /^891480/ {print $2; exit}')
	[ -n "$vzw" ] && say "Verizon profile: ICCID ...${vzw: -2}, state $vst (switch back by enabling it)"
	tmo=$(echo "$list" | awk -v w="$want" 'tolower($3) ~ w || (w == "tmo" && (tolower($3) ~ /t-mobile|tmobile/ || $1 ~ /^890126/)) {print $1; exit}')
	# fallback: the one profile that is neither Verizon nor Skylo
	[ -z "$tmo" ] && [ "$want" != tmo ] && tmo=$(echo "$list" | awk '$1 !~ /^891480/ && tolower($3) !~ /verizon|skylo/ {print $1; exit}')
	if [ -z "$tmo" ]; then
		say "profile \"$want\" not on the eUICC: downloading"
		for i in $(seq 60); do ip route show default 2>/dev/null | grep -q . && break; sleep 1; done
		esim_download "$lp"
		list=$(lpa "$lp" profile list | lpa_profiles)
		tmo=$(echo "$list" | awk -v w="$want" 'tolower($3) ~ w || (w == "tmo" && (tolower($3) ~ /t-mobile|tmobile/ || $1 ~ /^890126/)) {print $1; exit}')
		[ -n "$tmo" ] || { say "profile \"$want\" still absent; no switch"; return 0; }
	else
		say "profile \"$want\" on the eUICC (ICCID ...${tmo: -2}): no download"
	fi
	tst=$(echo "$list" | awk -v i="$tmo" '$1 == i {print $2; exit}')
	if [ "$tst" = enabled ]; then
		say "profile \"$want\" ...${tmo: -2} already enabled: no switch"
	else
		step "eSIM: enable profile \"$want\" ...${tmo: -2} (state $tst)"
		lpa "$lp" profile enable "$tmo" | lpa_summary
		grep -v -E "[0-9]{9,}|LPA:" /run/luma-fold-lpa.err | tail -3
		ESIM_SWITCHED=1
	fi
	esim_report "$lp" "after the switch"
}

# ping: the root image has none; a wrapper runs the initramfs busybox through its
# musl loader (a script, so "timeout ping" finds it)
PING=/usr/bin/ping
if [ ! -x "$PING" ] && [ -x /var/lib/luma-fold/modem/ld-musl-aarch64.so.1 ]; then
	PING=/run/luma-fold-ping
	cat > $PING <<'EOF'
#!/bin/sh
six=; [ "$1" = -6 ] && { six=6; shift; }
exec /var/lib/luma-fold/modem/ld-musl-aarch64.so.1 /var/lib/luma-fold/modem/busybox ping$six "$@"
EOF
	chmod 755 $PING
fi

mountpoint -q /sys/kernel/debug || mount -t debugfs none /sys/kernel/debug
say "start stack=$mode radio=$radio"
# The battery-current-limiter lines the stock BCL driver drives as outputs to
# the modem RF front end (gpp11-0 "bcl-rffe-modem", gpp13-0 "bcl-batoilo-modem",
# active-high, held low). A DT gpio-hog drives both low at pinctrl probe.
# Never read debugfs pinctrl (pins/pinconf-pins): its pinconf walk over every
# bank took a synchronous external abort (v3x). Only the hog messages
# (gpiolib.dyndbg) and the GPIO chardev line info for these two lines, which
# reports owner/direction from gpiolib state without touching other banks.
bcl_lines() {	# $1: label
	echo "== BCL modem lines ($1)"
	dmesg | grep -E "hogged as|bcl-" | head -6
	timeout 10 python3 - <<'PY' 2>&1
import fcntl, glob, struct
CHIPINFO = 0x8044B401	# GPIO_GET_CHIPINFO_IOCTL, struct gpiochip_info (68 bytes)
LINEINFO = 0xC100B405	# GPIO_V2_GET_LINEINFO_IOCTL, struct gpio_v2_line_info (256 bytes)
want = {"gpp11": 0, "gpp13": 0}
for dev in sorted(glob.glob("/dev/gpiochip*")):
	try:
		with open(dev, "rb", buffering=0) as f:
			ci = bytearray(68)
			fcntl.ioctl(f, CHIPINFO, ci)
			label = ci[32:64].split(b"\0")[0].decode()
			if label not in want:
				continue
			li = bytearray(256)
			struct.pack_into("<I", li, 64, want[label])
			fcntl.ioctl(f, LINEINFO, li)
			consumer = li[32:64].split(b"\0")[0].decode()
			fl = struct.unpack_from("<Q", li, 72)[0]
			d = "out" if fl & 8 else "in" if fl & 4 else "?"
			print("-- %s-0 (%s): used=%d consumer=%r dir=%s active-low=%d" % (label, dev, fl & 1, consumer, d, (fl >> 1) & 1))
	except OSError as e:
		print("-- %s: %s" % (dev, e))
PY
}
bcl_lines "at test start"
echo "-- FP6 IMS units (must be masked here): luma-fp6-imsd $(systemctl is-active luma-fp6-imsd.service 2>/dev/null) / $(systemctl is-enabled luma-fp6-imsd.service 2>/dev/null)"
step "driver"
grep -q '^s5xxx_modem ' /proc/modules || abort "s5xxx_modem is not loaded (staging: $(journalctl -b -u luma-fold-modem --no-pager -o cat 2>/dev/null | grep -i driver | tail -1))"
if dmesg | grep -q "s5xxx-modem.*CP endpoint not enumerated"; then
	dmesg | grep -E "s5xxx-modem|12100000.pcie|cp-power" | tail -12 | mask
	abort "the CP endpoint was not on the PCIe bus (no link at boot, CP power cycle did not bring it back)"
fi
dmesg | grep -q "s5xxx-modem.*error" && { dmesg | grep "s5xxx-modem.*error" | tail -3 | mask; }
for i in $(seq 120); do dmesg | grep -q -E "CP is ONLINE|CP CRASHED" && break; sleep 0.5; done
dmesg | grep -q "CP is ONLINE" || abort "no CP ONLINE within 60 s of the test start: $(dmesg | grep s5xxx-modem | tail -1 | sed 's/.*s5xxx-modem modem: //' | mask)"
say "CP ONLINE: $(dmesg | grep -o -E '\[ *[0-9.]+\] s5xxx-modem modem: CP is ONLINE.*' | tail -1 | mask)"
dmesg | grep -E "s5xxx-modem.*(port up|handover)" | tail -8 | mask
step "daemons"
for i in $(seq 40); do [ -S /run/sit-proxy/0 ] && [ -S /run/sit-proxy/1 ] && break; sleep 0.5; done
[ -S /run/sit-proxy/0 ] && [ -S /run/sit-proxy/1 ] || abort "sit-proxy sockets missing ($(systemctl is-active sit-proxy))"
say "sit-proxy $(systemctl is-active sit-proxy), sockets: $(ls /run/sit-proxy 2>/dev/null | tr '\n' ' ')"
for i in $(seq 20); do [ "$(systemctl is-active exynos-modem)" = active ] && break; sleep 0.5; done
if [ "$(systemctl is-active exynos-modem)" != active ]; then
	systemctl start --no-block exynos-modem.service 2>&1
	sleep 3
fi
say "exynos-modem $(systemctl is-active exynos-modem) (udev-wanted by wwan0oem1)"
[ "$(systemctl is-active exynos-modem)" = active ] || abort "exynos-modem is not running: $(systemctl --no-pager status exynos-modem 2>&1 | grep -E 'Active|Dependency|failed' | head -2 | tr '\n' ' ')"
[ "$(systemctl is-active ModemManager)" = active ] || abort "ModemManager is not running"
say "ModemManager $(systemctl is-active ModemManager), binary $(readlink -f /proc/$(systemctl show -p MainPID --value ModemManager 2>/dev/null)/exe 2>/dev/null)"
echo "== udev properties"
for p in /sys/class/wwan/wwan0*; do
	echo "$(basename $p): $(udevadm info -q property $p 2>/dev/null | grep -E '^ID_MM_' | tr '\n' ' ')"
done
for n in rmnet0 rmnet14 rmnet15 rmnet29; do
	echo "$n: $(udevadm info -q property /sys/class/net/$n 2>/dev/null | grep -E '^ID_MM_' | tr '\n' ' ')"
done

# the modem objects: ModemManager probes the stacks' netdevs first, then again
# once the SIT ports appear ("probing had failed, will retry")
step "wait for modem objects"
n=0
for i in $(seq 90); do n=$(timeout 10 "$MMCLI" -L 2>/dev/null | grep -c "/Modem/"); [ "$n" -ge 2 ] && break; sleep 1; done
say "modems: $n"
if [ "$n" -lt 1 ]; then
	journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "<msg>|<wrn>|<err>" | tail -20 | mask
	abort "ModemManager created no modem within 90 s"
fi
timeout 10 "$MMCLI" -L 2>&1 | mask
journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "not supported|will retry|creating modem|couldn't create|grabbing|SIT|sit-proxy" | tail -12 | mask
echo "== SIT read-only probe through sit-proxy"
timeout -s KILL 45 python3 /var/lib/luma-fold/modem/luma-fold-modem-sit.py --readonly 2>&1 | mask
ESIM_SWITCHED=
if grep -q "luma.fold_esim_switch=" /proc/cmdline; then
	lp=0; grep -q "luma.fold_dsds=1" /proc/cmdline && lp=1
	esim_switch $lp
fi
if grep -q "luma.fold_map_esim=1" /proc/cmdline; then
	step "map the eUICC to logical slot 0 (SET_LOGICAL_SLOT_MAPPING; stack 1 enable as fallback)"
	say "stopping ModemManager for the remap (radios go off on both stacks)"
	systemctl stop ModemManager
	# after a profile switch, power-cycle the card so the CP loads the new profile
	timeout -s KILL 120 python3 /var/lib/luma-fold/modem/luma-fold-modem-sit.py --slot-map ${ESIM_SWITCHED:+--sim-cycle} 2>&1 | mask
	say "starting ModemManager so it re-reads the SIM on each stack"
	systemctl start ModemManager
	n=0
	for i in $(seq 90); do n=$(timeout 10 "$MMCLI" -L 2>/dev/null | grep -c "/Modem/"); [ "$n" -ge 2 ] && break; sleep 1; done
	for i in $(seq 60); do
		st0=$(mmk -m exynos-modem-stack0 | val 'modem.generic.state')
		fr0=$(mmk -m exynos-modem-stack0 | val 'modem.generic.state-failed-reason')
		case "$st0/$fr0" in /*|initializing/*|unknown/*|failed/sim-missing) sleep 1;; *) break;; esac
	done
	say "stack 0 after ${i} s: state ${st0:-?} failed reason ${fr0:---}"
	say "modems after the remap: $n; stack 0 $(state_line exynos-modem-stack0); stack 1 $(state_line exynos-modem-stack1)"
fi
sim_modem=
for path in $(timeout 10 "$MMCLI" -L 2>/dev/null | grep -o "/org/freedesktop/ModemManager1/Modem/[0-9]*"); do
	m=${path##*/}
	echo "== modem $m"
	mmk -m $m | grep -E 'generic\.(state|state-failed-reason|power-state|plugin|primary-port|sim |sim-slots|primary-sim-slot|access-technologies|signal-quality\.value|current-capabilities|current-modes|ports)|3gpp\.(registration-state|operator-name|operator-code|packet-service-state|enabled-locks)'
	s=$(mmk -m $m | val 'modem.generic.sim')
	if [ -n "$s" ] && [ "$s" != "--" ]; then
		sim_modem=$m
		echo "-- sim of modem $m"
		mmk -i ${s##*/} | grep -E 'sim\.properties\.(active|operator-name|operator-code|esim-status|removability|sim-type)'
	fi
done
say "modem with SIM: ${sim_modem:-none}"
if grep -q "luma.fold_esim_report=1" /proc/cmdline; then
	# the eUICC sits on logical stack 0 in single standby, else on 1
	lp=0; grep -q "luma.fold_dsds=1" /proc/cmdline && lp=1
	esim_report $lp "before radio-on"
	# the switch flow already downloaded what it needed
	grep -q "luma.fold_esim_switch=" /proc/cmdline || esim_download $lp
fi
# The eSIM lives on logical stack 1; address that modem by its device uid so
# the run does not depend on the index ModemManager handed out this boot.
target=1
# luma.fold_stack=0: test the physical SIM (slot 0, logical stack 0) and leave
# the eSIM stack disabled (radio off), for a clean comparison.
if grep -q "luma.fold_stack=0" /proc/cmdline; then
	s0=$(mmk -m exynos-modem-stack0 | val 'modem.generic.sim')
	s0fail=$(mmk -m exynos-modem-stack0 | val 'modem.generic.state-failed-reason')
	if [ -n "$s0" ] && [ "$s0" != "--" ] && [ "$s0fail" != sim-missing ]; then
		target=0
		sim_modem=exynos-modem-stack0
		home0=$(mmk -i ${s0##*/} | val 'sim.properties.operator-code')
		say "physical SIM on stack 0: home PLMN $(mmk -i ${s0##*/} | val 'sim.properties.operator-code'), operator '$(mmk -i ${s0##*/} | val 'sim.properties.operator-name')', type $(mmk -i ${s0##*/} | val 'sim.properties.sim-type'), state $(mmk -m exynos-modem-stack0 | val 'modem.generic.state')"
		say "the eSIM stack (1) stays disabled: $(state_line exynos-modem-stack1)"
	else
		say "NO PHYSICAL SIM on stack 0 (sim ${s0:---}, failed reason ${s0fail:---}); falling back to the eSIM on stack 1"
	fi
fi
if [ "$target" = 1 ] && [ "$(mmk -m exynos-modem-stack1 | val 'modem.generic.device')" = exynos-modem-stack1 ]; then
	stack1_index=$(mmk -m exynos-modem-stack1 | val 'modem.generic.dbus-path' | sed 's,.*/,,')
	say "stack 1 (eSIM) is modem index ${stack1_index:-?}; using the uid exynos-modem-stack1"
	[ -n "$sim_modem" ] && [ "$sim_modem" != "${stack1_index:-$sim_modem}" ] && say "note: the SIM showed up on modem $sim_modem, not on stack 1"
	sim_modem=exynos-modem-stack1
fi

if [ "$radio" = 1 ] && [ -n "$sim_modem" ]; then
	m=$sim_modem
	# luma.fold_nr_mode=N: read the CP's NR and EN-DC modes and set the NR mode
	# to N before radio-on; the original is put back after radio-off.
	nrmode=$(sed -n 's/.*luma\.fold_nr_mode=\([0-9]\).*/\1/p' /proc/cmdline); nrorig=
	if [ -n "$nrmode" ]; then
		step "NR mode: read, then set to $nrmode"
		out=$(timeout -s KILL 40 python3 /var/lib/luma-fold/modem/luma-fold-modem-sit.py --nr-mode $nrmode 2>&1)
		echo "$out" | grep -E "NR_MODE|ENDC_MODE|NRMODE_ORIG" | mask
		nrorig=$(echo "$out" | sed -n 's/^NRMODE_ORIG=//p')
	fi
	# luma.fold_cplog=1: capture the CP's DM channel (umts_dm0, ch 0x51)
	# from before radio-on for up to 300 s / 16 MiB, into the coredump
	# directory the journal probe collects. Read-only: nothing is sent.
	# luma.fold_cplog=2: the same after the stock DM start sequence
	# (DmStartRequest, control 0x06, DmTraceStartRequest; no trace selection).
	cplog=
	if grep -q -E "luma.fold_cplog=[12]" /proc/cmdline; then
		dmdev=$(ls /dev/wwan*dm* 2>/dev/null | head -1)
		if [ -n "$dmdev" ]; then
			cplog=/var/lib/systemd/coredump/luma-cplog-$(date +%Y%m%dT%H%M%S).bin
			step "CP DM capture: $dmdev -> $cplog"
			if grep -q "luma.fold_cplog=2" /proc/cmdline; then
				python3 -u /var/lib/luma-fold/modem/luma-fold-modem-dm.py "$dmdev" "$cplog" 300 16777216 &
			else
				( timeout -s INT 300 cat "$dmdev" | head -c 16777216 > "$cplog" ) &
			fi
		else
			say "CP DM capture: no DM port (kernel without the umts_dm0 channel)"
		fi
	fi
	step "enable: modem $m (radio on)"
	timeout 90 "$MMCLI" -m $m --enable 2>&1 | mask
	say "enable done: $(state_line $m)"
	# luma.fold_lte_only=1: allowed modes 4G only, so the CP tries Verizon LTE
	# instead of camping on NR.
	if grep -q "luma.fold_lte_only=1" /proc/cmdline; then
		timeout 30 "$MMCLI" -m $m --set-allowed-modes=4g 2>&1 | mask
		say "allowed modes now: $(mmk -m $m | grep -E 'generic.current-modes' | sed 's/  */ /g' | tr '\n' ';')"
	fi
	# luma.fold_no_nr=1: every generation but NR (2G/3G/4G), so the CP keeps its
	# normal search but cannot camp on NR.
	if grep -q "luma.fold_no_nr=1" /proc/cmdline; then
		timeout 30 "$MMCLI" -m $m --set-allowed-modes="2g|3g|4g" 2>&1 | mask
		say "allowed modes now: $(mmk -m $m | grep -E 'generic.current-modes' | sed 's/  */ /g' | tr '\n' ';')"
	fi
	step "registration: waiting up to 180 s for automatic home/roaming"
	timeout 15 "$MMCLI" -m $m --signal-setup=5 2>&1 | mask
	st=; last=; crashed=
	for i in $(seq 180); do
		cur=$(state_line $m)
		st=$(echo "$cur" | sed -n 's/.*3gpp.registration-state : \([^;]*\);.*/\1/p')
		[ "$cur" != "$last" ] && { say "registration $st: $cur"; last=$cur; }
		case $st in home|roaming) break;; esac
		if dmesg | grep -q "CP CRASHED"; then
			crashed=1
			say "CP CRASHED while waiting for registration: $(dmesg | grep -o 'CP CRASHED.*' | tail -1)"
			break
		fi
		[ $((i % 10)) = 0 ] && { echo "-- signal"; mmk -m $m --signal-get | grep -E 'signal\.(lte|5g|umts|gsm)\.' | grep -v -- '--' | sed 's/  */ /g' | tr '\n' ';'; echo; }
		# Cells the CP sees (operator, EARFCN / NR-ARFCN, PCI, RSRP/RSRQ, serving);
		# no TAC or cell identity.
		[ $((i % 30)) = 0 ] && { echo "-- cell info at ${i} s"; timeout 15 "$MMCLI" -m $m --get-cell-info 2>&1 | grep -i -E "cell type|operator id|arfcn|physical ci|rsrp|rsrq|sinr|serving|bandwidth" | sed 's/  */ /g' | tr '\n' ';' | sed 's/cell type/\ncell type/g'; echo; }
		sleep 1
	done
	say "registration $st after the wait: $(state_line $m)"
	echo "-- ALLOW_DATA / SET_MOBILE_DATA_STATE / GET_PS_SERVICE so far"
	journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "ALLOW_DATA|SET_MOBILE_DATA_STATE|GET_PS_SERVICE \(|GET_DEVICE_SERVICE|GET_VOICE_OPERATION|GET_VO_LTE|SET_VOICE_OPERATION|SET_VO_LTE|SET_DEVICE_SERVICE|usage setting" | grep -v -- "-->" | tail -16 | mask
	echo "-- data profiles and the initial attach APN"
	journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "SET_DATA_PROFILE [0-9]|initial attach APN|SET_INITIAL_ATTACH_APN.*status" | grep -v -- "-->" | tail -10 | mask
	echo "-- dds.bin the CP wrote (size, digest prefix, nonzero bytes, first 16 bytes)"
	for f in /var/lib/exynos-modem/replay/dds.bin*; do [ -f "$f" ] && python3 -c "import sys,hashlib; d=open(sys.argv[1],'rb').read(); print(sys.argv[1], len(d), hashlib.sha256(d).hexdigest()[:16], sum(1 for b in d if b), d[:16].hex())" "$f" | mask; done
	echo "-- carrier ID property (published by the plugin, read by the CP)"
	{ journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "published vendor|selected Samsung carrier"; journalctl -b -u exynos-modem --no-pager -o short-monotonic 2>/dev/null | grep -E "property read"; } | tail -10 | mask
	echo "-- RRC / PHY / NAS-timer / registration-failure indications so far"
	journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "indication 0x0(747|742|607|74a)|REGISTRATION_FAILED|NET_RRC|PHYSICAL_CHANNEL|NAS_TIMER" | tail -30 | mask
	if [ -z "$crashed" ] && [ "$st" != home ] && [ "$st" != roaming ]; then
		step "network scan (reads EF_FPLMN, then lists PLMNs with availability)"
		timeout 90 "$MMCLI" -m $m --3gpp-scan --timeout=80 2>&1 | mask
		say "scan done: $(state_line $m)"
		# The plugin logs the forbidden-PLMN count and reads EF_FPLMN during the
		# scan; 311480 shown as "forbidden" in the scan list, or present in that
		# count, means the card forbids Verizon.
		echo "-- forbidden-PLMN / scan diagnostics"
		journalctl -b -u ModemManager --no-pager -o cat 2>/dev/null | grep -i -E "forbidden PLMN|forbidden|EF_FPLMN|scan" | grep -v -i "imsi" | tail -15 | mask
	fi
	if [ -z "$crashed" ] && [ "$st" != home ] && [ "$st" != roaming ] && [ "$target" = 0 ]; then
		say "stack 0 (physical SIM): no manual selection; automatic only"
		echo "-- registration failures / rejects on stack 0"
		journalctl -b -u ModemManager --no-pager -o cat 2>/dev/null | grep -i -E "reject|cause|registration fail|denied|REGISTRATION_FAILED" | grep -v -i "imsi" | tail -15 | mask
	fi
	if [ -z "$crashed" ] && [ "$st" != home ] && [ "$st" != roaming ] && [ "$target" = 1 ]; then
		# Manual selection bypasses the forbidden list: if the card forbidding
		# 311480 is the gate, a manual register attaches; otherwise the CP gives
		# an EMM reject cause we can read.
		step "manual register to 311480 (bypasses the forbidden list)"
		mstart=$(cut -d' ' -f1 /proc/uptime)
		timeout 360 "$MMCLI" -m $m --3gpp-register-in-operator=311480 --timeout=340 2>&1 | mask
		echo "-- the CP's answer to SET_NETWORK_SELECTION_MANUAL (status + frame)"
		journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "SET_NETWORK_SELECTION_MANUAL (answered|: no answer)|late response" | tail -4 | mask
		ms=; for i in $(seq 30); do
			ms=$(state_line $m | sed -n 's/.*3gpp.registration-state : \([^;]*\);.*/\1/p')
			case $ms in home|roaming) break;; esac
			sleep 1
		done
		say "after manual register: $(state_line $m)"
		bcl_lines "after the registration window"
		echo "-- GET_PS_SERVICE answers so far"
		journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "GET_PS_SERVICE \(" | tail -12 | mask
		echo "-- every SIT indication since the manual register (names, with RRC/PHY/NAS/barring bodies)"
		journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | awk -v t=$mstart -F"[][]" '{ if ($2+0 >= t) print }' | grep -E "<-- indication|indication 0x" | grep -v -i "imsi\|SIGNAL_STRENGTH" | mask | tail -80
		journalctl -b -u ModemManager --no-pager -o cat 2>/dev/null | grep -i -E "register|reject|cause|manual|denied|forbidden|plmn" | grep -v -i "imsi" | tail -20 | mask
		[ "$ms" = home ] || [ "$ms" = roaming ] && { st=$ms; say "manual selection registered ($ms) -- the forbidden list was the gate"; }
	fi
	if [ -n "$crashed" ]; then
		step "crash record: one dump boot through debugfs"
		timeout -s KILL 100 sh -c 'echo 1 > /sys/kernel/debug/s5xxx-modem/dump_boot' 2>&1
		echo "dump boot exit $?"
		dmesg | grep -E "dump boot|link_diag|srinfo|crash|CRASH|assert|PAL_" | tail -n 60 | mask
	fi
	if grep -q "luma.fold_slicing=1" /proc/cmdline && state_line $m | grep -q -E "3gpp.registration-state : (home|roaming);"; then
		step "slicing config (URSP) from the CP"
		timeout -s KILL 40 python3 /var/lib/luma-fold/modem/luma-fold-modem-sit.py --slicing 2>&1 | grep -E "SLICING|0x062|ursp|  "   # URSP carries no subscriber identifiers; hex left unmasked
		journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "UNKNOWN \(0x06|SLICING" | tail -6 | mask
	fi
	apns=vzwinternet
	case "$home0" in 310260|310240|310160|310200|310210|310220|310230|310250|310270|310310|310490|310660|310800) apns="tello:ipv6 tello:ipv4v6 fast.t-mobile.com:ipv6"
		grep -q "luma.fold_apn_order=v4v6first" /proc/cmdline && apns="tello:ipv4v6 tello:ipv6 fast.t-mobile.com:ipv6" ;; esac
	apn=${apns%% *}; apn=${apn%%:*}
	step "bearer: simple-connect, in order until one connects: $apns (apn[:ip-type], default ipv4v6)"
	# Unregistered, simple-connect would send SET_NETWORK_SELECTION_AUTO and
	# cancel a manual attempt the CP may still be running.
	if [ -n "$crashed" ]; then
		say "bearer skipped: the CP crashed"
	elif [ "$target" = 0 ] && [ "$home0" != 311480 ] && [ "$apns" = vzwinternet ]; then
		say "bearer skipped: stack 0 test with home ${home0:-?} (registration only; the APN is unknown)"
	elif ! state_line $m | grep -q -E "3gpp.registration-state : (home|roaming);"; then
		say "bearer skipped: not registered (no automatic re-selection over a pending manual attempt)"
	else
		for ent in $apns; do
			apn=${ent%%:*}; ipt=ipv4v6; [ "$ent" != "$apn" ] && ipt=${ent#*:}
			say "bearer: trying apn=$apn ip-type=$ipt"
			timeout 90 "$MMCLI" -m $m --simple-connect="apn=$apn,ip-type=$ipt" 2>&1 | mask
			bb=$(mmk -m $m | val 'modem.generic.bearers.value\[1\]')
			if [ -n "$bb" ] && [ "$bb" != "--" ] && [ "$(mmk -b ${bb##*/} | val 'bearer.status.connected')" = yes ]; then
				say "bearer: connected with apn=$apn ip-type=$ipt"; break
			fi
			say "bearer: apn=$apn ip-type=$ipt did not connect"
			timeout 30 "$MMCLI" -m $m --simple-disconnect >/dev/null 2>&1
			[ -n "$bb" ] && [ "$bb" != "--" ] && timeout 15 "$MMCLI" -m $m --delete-bearer=${bb##*/} >/dev/null 2>&1
			# the plugin deactivates and releases a failed context asynchronously;
			# the next attempt must not find it "awaiting cleanup"
			for i in $(seq 20); do
				journalctl -b -u ModemManager --no-pager -o cat 2>/dev/null | tail -40 | grep -q -E "context id [0-9]+ released|cleanup failed" && break
				sleep 1
			done
			sleep 2
		done
		echo "-- data-call requests and answers (cause in the answer's item at +0x0c)"
		journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "SETUP_DATA_CALL (cid|answer)|returned no address" | tail -24 | mask
	fi
	b=$(mmk -m $m | val 'modem.generic.bearers.value\[1\]')
	IF= A4= P4= G4= D4= M4= A6= P6= G6= D6= C=
	if [ -n "$b" ] && [ "$b" != "--" ]; then
		bk=$(mmk -b ${b##*/})
		echo "$bk" | grep -E 'bearer\.(status\.(connected|interface|multiplexed)|ipv4-config|ipv6-config|properties\.(apn|ip-type))'
		C=$(echo "$bk" | val 'bearer.status.connected')
		IF=$(echo "$bk" | val 'bearer.status.interface')
		A4=$(echo "$bk" | val 'bearer.ipv4-config.address'); P4=$(echo "$bk" | val 'bearer.ipv4-config.prefix')
		G4=$(echo "$bk" | val 'bearer.ipv4-config.gateway'); D4=$(echo "$bk" | val 'bearer.ipv4-config.dns.value\[1\]')
		M4=$(echo "$bk" | val 'bearer.ipv4-config.mtu')
		A6=$(echo "$bk" | val 'bearer.ipv6-config.address'); P6=$(echo "$bk" | val 'bearer.ipv6-config.prefix')
		G6=$(echo "$bk" | val 'bearer.ipv6-config.gateway'); D6=$(echo "$bk" | val 'bearer.ipv6-config.dns.value\[1\]')
	fi
	for v in IF A4 P4 G4 D4 M4 A6 P6 G6 D6; do eval "[ \"\$$v\" = -- ] && $v="; done
	say "bearer ${b:-none} connected=${C:-no} interface=${IF:-none} ipv4=${A4:-none}/${P4:-} gw=${G4:-none} dns=${D4:-none} mtu=${M4:-default} ipv6=${A6:+set}${A6:-none}"
	if [ -n "$IF" ] && [ -e /sys/class/net/$IF ] && { [ -n "$A4" ] || [ -n "$A6" ]; }; then
		ip link set $IF up
		[ -n "$M4" ] && ip link set $IF mtu $M4
		rx0=$(cat /sys/class/net/$IF/statistics/rx_bytes); tx0=$(cat /sys/class/net/$IF/statistics/tx_bytes)
		if [ -n "$A4" ]; then
			ip addr add $A4/${P4:-32} dev $IF 2>&1
			if [ -n "$G4" ]; then
				ip route add $G4 dev $IF 2>/dev/null
				ip route add default via $G4 dev $IF metric 700 2>&1
			else
				ip route add default dev $IF metric 700 2>&1
			fi
			ip -4 addr show dev $IF | mask; ip -4 route show dev $IF | mask
			if [ -n "$G4" ]; then
				step "ping gateway via $IF"
				timeout 20 "$PING" -I $IF -c 3 -W 3 $G4 2>&1 | tail -2
			fi
			step "ping 1.1.1.1 via $IF"
			timeout 25 "$PING" -I $IF -c 3 -W 3 1.1.1.1 2>&1 | tail -2
			step "https 1.1.1.1 via $IF"
			timeout 30 curl -sS --interface $IF --max-time 20 -o /dev/null -w 'http %{http_code} in %{time_total}s, %{size_download} bytes\n' https://1.1.1.1/ 2>&1
			if [ -n "$D4" ]; then
				step "dns $D4 via $IF (www.cloudflare.com A)"
				timeout 15 python3 - $IF $D4 <<'PY' 2>&1
import random, socket, struct, sys
ifname, server = sys.argv[1], sys.argv[2]
q = struct.pack('>HHHHHH', random.randrange(65536), 0x100, 1, 0, 0, 0)
q += b''.join(bytes([len(l)]) + l.encode() for l in 'www.cloudflare.com'.split('.')) + b'\0' + struct.pack('>HH', 1, 1)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, 25, ifname.encode())
s.settimeout(6)
s.sendto(q, (server, 53))
r = s.recv(512)
flags, qd, an = struct.unpack_from('>HHH', r, 2)
print('dns rcode', flags & 15, 'answers', an)
PY
			fi
		fi
		if [ -n "$A6" ]; then
			ip -6 addr add $A6/${P6:-64} dev $IF 2>&1
			# raw-IP rmnet is point-to-point: a device default route; the bearer's
			# IPv6 "gateway" is not a usable next hop
			ip -6 route add default dev $IF metric 700 2>&1
			ip -6 addr show dev $IF | grep inet6 | sed 's/  */ /g' | mask; ip -6 route show dev $IF | mask
			step "ping6 2606:4700:4700::1111 via $IF"
			timeout 25 "$PING" -6 -I $IF -c 3 -W 3 2606:4700:4700::1111 2>&1 | tail -2
			step "https6 via $IF"
			timeout 30 curl -sS -6 --interface $IF --max-time 20 -o /dev/null -w 'http %{http_code} in %{time_total}s, %{size_download} bytes\n' 'https://[2606:4700:4700::1111]/' 2>&1
		fi
		rx1=$(cat /sys/class/net/$IF/statistics/rx_bytes); tx1=$(cat /sys/class/net/$IF/statistics/tx_bytes)
		say "data counters: $IF rx $((rx1 - rx0)) tx $((tx1 - tx0)) bytes"
	else
		say "no bearer interface or address to test"
	fi
	step "sms: messaging status and stored count"
	mmk -m $m --messaging-status
	nsms=$(timeout 10 "$MMCLI" -m $m --messaging-list-sms 2>/dev/null | grep -c "/SMS/")
	say "sms stored on the modem: $nsms (contents never read)"
	step "ims: voice status and the plugin's IMS lines"
	mmk -m $m --voice-status
	# SMS-over-IMS readiness: the plugin starts the CP's IMS stack once the radio
	# registers; wait up to 90 s for "IMS registered" (read-only, nothing sent).
	if state_line $m | grep -q -E "3gpp.registration-state : (home|roaming);"; then
		ims=no
		for i in $(seq 90); do
			journalctl -b -u ModemManager --no-pager -o cat 2>/dev/null | grep -q -E "IMS registered" && { ims=yes; break; }
			sleep 1
		done
		say "IMS registration (SMS-over-IMS readiness): $ims"
		journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "IMS (PDN up|provisioned|registered|deregistered|stopped)|IMS .* failed|P-CSCF|VoPS|no IMS configuration|starting IMS|not starting IMS|AIMS|ADD_PDN|STACK_START|indication 0x030|CP dropped data call|registration state changed" | tail -40 | mask
		mmk -m $m --messaging-status 2>/dev/null | grep -E "supported|default" | sed 's/  */ /g' | tr '\n' ';'; echo
	fi
	journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -i -E "ims|volte|vops" | grep -v -i "imsi" | tail -25 | mask
	# luma.fold_sim5g=1: the USIM's 5G files (UST, DF 5GS sizes, EF URSP
	# content); identity files are sized, never read.
	if grep -q "luma.fold_sim5g=1" /proc/cmdline; then
		step "USIM 5G files (read-only)"
		timeout -s KILL 60 python3 /var/lib/luma-fold/modem/luma-fold-modem-sit.py --sim5g 2>&1 | grep -E "==|  (EF|UST|DF|USIM|no )|    " | grep -v -- "->"
	fi
	if [ -n "$cplog" ]; then
		echo "-- CP DM capture so far: $(stat -c %s "$cplog" 2>/dev/null) bytes"
		head -c 512 "$cplog" 2>/dev/null | od -A x -t x1 | head -34
		dmesg | grep -E "DM port" | tail -3
	fi
	# luma.fold_slicing=1: a second URSP read, a minute or more after
	# registration (network UE policy arrives after the registration itself).
	if grep -q "luma.fold_slicing=1" /proc/cmdline && state_line $m | grep -q -E "3gpp.registration-state : (home|roaming);"; then
		step "slicing config (URSP) from the CP, late read"
		timeout -s KILL 40 python3 /var/lib/luma-fold/modem/luma-fold-modem-sit.py --slicing 2>&1 | grep -E "SLICING|0x062|ursp|  "
		journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "UNKNOWN \(0x06|SLICING|access technology changed" | tail -8 | mask
	fi
	echo "-- GEMS/GIPC: what the CP asked the daemon for"
	journalctl -b -u exynos-modem --no-pager -o short-monotonic 2>/dev/null | grep -E "served|uecap|gipc <=|gipc =>|oem <=|oem =>|rejected|read .*: " | tail -30 | mask
	echo "-- RFS pulls (kernel)"
	dmesg | grep -E "RFS serve" | tail -20 | mask
	echo "-- ims.db builder"
	journalctl -b -u luma-fold-modem-imsdb --no-pager -o cat 2>/dev/null | tail -8
	calltest "$m" "$st"
	step "disconnect"
	timeout 60 "$MMCLI" -m $m --simple-disconnect 2>&1 | mask
	step "disable (radio off)"
	timeout 60 "$MMCLI" -m $m --disable 2>&1 | mask
	if [ -n "$nrorig" ] && [ "$nrorig" != "$nrmode" ]; then
		say "NR mode: putting the original $nrorig back"
		timeout -s KILL 40 python3 /var/lib/luma-fold/modem/luma-fold-modem-sit.py --nr-mode $nrorig 2>&1 | grep -E "NR_MODE" | mask
	fi
	say "after disable: $(state_line $m)"
	# disable leaves the radio on (power-state on); power-state low runs the
	# plugin's power-down, which restores the usage setting when asked.
	timeout 30 "$MMCLI" -m $m --set-power-state-low 2>&1 | mask
	say "after power-state low: $(state_line $m)"
	echo "-- usage setting at radio-off"
	sleep 2; journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -E "usage setting|GET_DEVICE_SERVICE \\(after the restore" | tail -4 | mask
elif [ -n "$sim_modem" ]; then
	say "radio stays off: modem $sim_modem $(state_line $sim_modem)"
fi
echo "== services"
systemctl --no-pager status sit-proxy exynos-modem ModemManager 2>&1 | grep -E "^\s*(Active|Main PID|Loaded)|\.service" | head -12
echo "== journal: sit-proxy and exynos-modem"
journalctl -b -u sit-proxy -u exynos-modem --no-pager -o short-monotonic 2>/dev/null | tail -40 | mask
echo "== journal: ModemManager"
journalctl -b -u ModemManager --no-pager -o short-monotonic 2>/dev/null | grep -v -i "imsi\|iccid\|imei\|eid" | grep -E "<msg>|<wrn>|<err>|state changed|registration|SIT|sit|radio|attach|carrier|IMS|signal|operator|serving|cell|PLMN|network" | tail -220 | mask
echo "== kernel"
kmsg 80
say "done"
