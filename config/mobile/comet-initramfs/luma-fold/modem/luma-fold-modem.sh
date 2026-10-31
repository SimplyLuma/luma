#!/bin/bash
# Project Luma, Pixel 9 Pro Fold: stage the Exynos 5400 modem firmware for the
# s5xxx_modem driver from the phone's own partitions, then load the driver.
#
# The CP boots from the factory modem image (modem partition) and needs this
# unit's calibration and identity: the NV files (efs), the CP signature
# (persist), the RF table hardware_config.json names for the board id, and a
# fresh replay archive packed from modem_userdata. Nothing on those partitions
# may ever be written: they are set read-only for the rest of the boot and
# mounted through read-only loop devices with recovery disabled, because a
# plain "ro" mount of f2fs or ext4 still replays pending recovery data. The
# staged copies are root-only, under the firmware root the shared initramfs
# points firmware_class at (/var/lib/luma-fold/firmware), where the driver
# finds them by its own relative paths (google/s5400/...).
set -u
base=/var/lib/luma-fold/modem
fwroot=/var/lib/luma-fold/firmware
fw=$fwroot/google/s5400
mnt=/run/luma-fold-modem
log() { echo "luma-fold-modem: $*"; }
umask 077

slot=$(sed -n 's/.*androidboot\.slot_suffix=\(_[ab]\).*/\1/p' /proc/cmdline)
[ -n "$slot" ] || slot=_a

part() {	# $1: partition label; waits for udev, prints the device
	local p=/dev/disk/by-partlabel/$1 i
	for i in $(seq 40); do
		[ -e "$p" ] && { echo "$p"; return 0; }
		sleep 0.5
	done
	log "$1: no such partition"
	return 1
}
mount_ro() {	# $1: partition label $2: mount name
	local d l
	d=$(part "$1") || return 1
	mkdir -p "$mnt/$2"
	mountpoint -q "$mnt/$2" && return 0
	blockdev --setro "$d" || { log "$1: cannot set read-only, not mounting"; return 1; }
	l=$(losetup --read-only --find --show "$d") || { log "$1: no read-only loop device"; return 1; }
	echo "$l" >> "$mnt/loops"
	mount -o ro,norecovery,nodev,nosuid,noexec "$l" "$mnt/$2" ||
		{ log "$1: mount failed"; return 1; }
}
cleanup() {
	local d l
	for d in modem efs persist modem_userdata vendor; do
		mountpoint -q "$mnt/$d" && umount "$mnt/$d"
	done
	dmsetup remove luma-fold-vendor 2>/dev/null
	if [ -f "$mnt/loops" ]; then
		while read -r l; do losetup -d "$l" 2>/dev/null; done < "$mnt/loops"
		rm -f "$mnt/loops"
	fi
}
trap cleanup EXIT
mkdir -p "$mnt" "$fw/modem/images/default" "$fw/efs" "$fw/persist/modem"
chmod 700 "$fw"
rm -f "$mnt/loops"
rm -rf "$base/fw"	# staging location before the shared firmware root
chmod 700 "$base"

# Board identity: bootconfig, the command line, the bootloader's device tree
# (carried over by the Luma kernel), else the value recorded from this unit.
cdt=$(sed -n 's/^androidboot\.cdt_hwid *= *"\{0,1\}\(0x[0-9a-fA-F]*\).*/\1/p' /proc/bootconfig 2>/dev/null | head -1)
[ -n "$cdt" ] || cdt=$(sed -n 's/.*androidboot\.cdt_hwid=\(0x[0-9a-fA-F]*\).*/\1/p' /proc/cmdline | head -1)
dt=/sys/firmware/devicetree/base/chosen/config/cdt_hwid
if [ -z "$cdt" ] && [ -f $dt ]; then
	cdt=$dt
fi
if [ -z "$cdt" ]; then
	cdt=0x00060606000100000000000000000000	# comet MP1.0 (stock dump)
	log "board id not provided by the boot, using the recorded one"
fi

# modem image: factory modem.bin, the configuration table and the RF table
if mount_ro "modem$slot" modem; then
	img=$mnt/modem/images/default
	ver=$(readlink "$img"); ver=${ver##*/}
	if [ -n "$ver" ] && { [ "$(cat "$base/.modem-version" 2>/dev/null)" != "$ver" ] ||
	   [ ! -s "$fw/modem/images/default/modem.bin" ] || [ ! -s "$fw/modem/images/default/hardware_config.json" ]; }; then
		if cp "$img/modem.bin" "$fw/modem/images/default/modem.bin.new" &&
		   mv "$fw/modem/images/default/modem.bin.new" "$fw/modem/images/default/modem.bin" &&
		   cp "$img/hardware_config.json" "$fw/modem/images/default/hardware_config.json"; then
			echo "$ver" > "$base/.modem-version"
			log "modem image $ver staged"
		else
			log "modem image copy failed"
		fi
	fi
	if rf=$(python3 "$base/luma-fold-modem.py" hwcfg "$cdt" \
			"$fw/modem/images/default/hardware_config.json" "$fw/hwcfg.bin"); then
		if [ "$(cat "$base/.rf-cfg" 2>/dev/null)" != "$rf" ] || [ ! -s "$fw/rf_cfg.bin" ]; then
			if [ -f "$img/$rf.gz" ]; then
				gunzip -c "$img/$rf.gz" > "$fw/rf_cfg.bin.new"
			elif [ -f "$img/$rf" ] && [ "$(head -c 2 "$img/$rf" | od -An -tx1 | tr -d ' ')" = 1f8b ]; then
				gunzip -c "$img/$rf" > "$fw/rf_cfg.bin.new"
			else
				cp "$img/$rf" "$fw/rf_cfg.bin.new"
			fi && mv "$fw/rf_cfg.bin.new" "$fw/rf_cfg.bin" && echo "$rf" > "$base/.rf-cfg" &&
				log "RF table $rf staged" || log "RF table $rf missing"
		fi
	else
		log "no hardware configuration resolved; hwcfg.bin and rf_cfg.bin not staged"
	fi
fi

# efs: the NV files; persist: the CP signature
if mount_ro efs efs; then
	n=0
	for f in nv_normal.bin nv_protected.bin nv_user.bin; do
		[ -f "$mnt/efs/$f" ] && cp "$mnt/efs/$f" "$fw/efs/$f" && n=$((n + 1))
	done
	[ $n -gt 0 ] && log "$n NV files staged" || log "efs holds no NV files: $(ls "$mnt/efs" | tr '\n' ' ')"
fi
if mount_ro persist persist; then
	if [ -f "$mnt/persist/modem/cpsha" ]; then
		cp "$mnt/persist/modem/cpsha" "$fw/persist/modem/cpsha" && log "CP signature staged"
	else
		log "persist holds no modem/cpsha: $(ls "$mnt/persist" | tr '\n' ' ')"
	fi
fi
if mount_ro modem_userdata modem_userdata; then
	if [ -f "$mnt/modem_userdata/replay/dds.bin" ]; then
		python3 "$base/luma-fold-modem.py" replay "$mnt/modem_userdata/replay/dds.bin" "$fw/replay_region.bin"
	else
		log "no replay data: $(ls "$mnt/modem_userdata" "$mnt/modem_userdata/replay" 2>/dev/null | tr '\n' ' ')"
	fi
fi
# vendor: the carrier configuration the CP pulls over RFS once it is sent a
# manifest, and the UE capability files it asks for over GEMS. vendor_<slot>
# lives inside super; its extents come from the LP metadata, mapped read-only
# and mounted ro,norecovery, and copied again only when they change.
stage_vendor() {
	local sup ext stamp dev off len map=
	sup=$(part super) || return 1
	blockdev --setro "$sup" || { log "super: cannot set read-only"; return 1; }
	ext=$(python3 "$base/luma-fold-modem-lp.py" "$sup" "vendor$slot") ||
		{ log "vendor$slot: not found in super"; return 1; }
	stamp=$(echo "$ext" | tr '\n' ' ')
	if [ "$stamp" = "$(cat "$base/.vendor-stamp" 2>/dev/null)" ] &&
	   [ -s "$fw/carrierconfig/cfg.db" ] && [ -d "$fw/uecapconfig" ] && [ -d "$fw/vendor-firmware" ]; then
		return 0
	fi
	if [ "$(echo "$ext" | wc -l)" = 1 ]; then
		read -r off len <<< "$ext"
		dev=$(losetup --read-only --find --show -o "$off" --sizelimit "$len" "$sup") ||
			{ log "vendor$slot: no read-only loop device"; return 1; }
		echo "$dev" >> "$mnt/loops"
	else
		dmsetup create luma-fold-vendor --readonly --table "$(echo "$ext" |
			awk -v d="$(readlink -f "$sup")" '{ printf "%d %d linear %s %d\n", s, $2 / 512, d, $1 / 512; s += $2 / 512 }')" ||
			{ log "vendor$slot: no read-only mapping"; return 1; }
		dev=/dev/mapper/luma-fold-vendor
		map=1
	fi
	mkdir -p "$mnt/vendor"
	if mount -o ro,norecovery,nodev,nosuid,noexec "$dev" "$mnt/vendor"; then
		if [ -d "$mnt/vendor/firmware/carrierconfig" ] && [ -d "$mnt/vendor/firmware/uecapconfig" ]; then
			rm -rf "$fw/carrierconfig.new" "$fw/uecapconfig.new"
			if cp -a "$mnt/vendor/firmware/carrierconfig" "$fw/carrierconfig.new" &&
			   cp -a "$mnt/vendor/firmware/uecapconfig" "$fw/uecapconfig.new"; then
				rm -rf "$fw/carrierconfig" "$fw/uecapconfig"
				mv "$fw/carrierconfig.new" "$fw/carrierconfig"
				mv "$fw/uecapconfig.new" "$fw/uecapconfig"
				# The CP asks the GIPC file service for more than UE capabilities
				# (PLMN mapping, the carrier's own file), by bare name: keep
				# every other .binarypb under vendor/firmware, relative paths
				# kept, so the daemon can find them by name.
				rm -rf "$fw/vendor-firmware.new"; mkdir -p "$fw/vendor-firmware.new"
				(cd "$mnt/vendor/firmware" && find . -path ./carrierconfig -prune -o -path ./uecapconfig -prune -o -type f -name '*.binarypb' -print 2>/dev/null) |
				while read -r f; do
					mkdir -p "$fw/vendor-firmware.new/$(dirname "$f")"
					cp -a "$mnt/vendor/firmware/$f" "$fw/vendor-firmware.new/$f"
				done
				rm -rf "$fw/vendor-firmware"; mv "$fw/vendor-firmware.new" "$fw/vendor-firmware"
				echo "$stamp" > "$base/.vendor-stamp"
				log "carrier config $(cat "$fw/carrierconfig/release-label" 2>/dev/null) and UE capability files staged"
				log "vendor/firmware: $(ls "$mnt/vendor/firmware" | tr '\n' ' ')"
				log "binarypb files: uecapconfig $(find "$fw/uecapconfig" -name '*.binarypb' | wc -l), carrierconfig $(find "$fw/carrierconfig" -name '*.binarypb' | wc -l), elsewhere $(find "$fw/vendor-firmware" -type f | wc -l) ($(cd "$fw/vendor-firmware" && find . -type f | sed 's,/[^/]*$,,' | sort -u | tr '\n' ' '))"
				log "ap_plmn_mapping.binarypb: $(find "$fw" -name ap_plmn_mapping.binarypb | sed "s,$fw/,," | tr '\n' ' ')- carrier files: $(find "$fw" -name 'VZW_*.binarypb' | sed "s,$fw/,,; s/[0-9]\{9,\}/<digits>/g" | tr '\n' ' ')-"
			else
				log "vendor$slot: copy failed"
			fi
		else
			log "vendor$slot holds no firmware/carrierconfig or uecapconfig"
		fi
		umount "$mnt/vendor"
	else
		log "vendor$slot: mount failed"
	fi
	[ -n "$map" ] && dmsetup remove luma-fold-vendor
	return 0
}
stage_vendor
cleanup

# Handover block: the driver assembles it from hwcfg.bin, the IMEIs in the
# bootloader's /chosen/config and cpsha. A kernel without the bootloader's
# /chosen has no IMEIs, so stage the fallback block from the same inputs with
# the IMEI fields left zero.
if [ ! -f /sys/firmware/devicetree/base/chosen/config/imei1 ]; then
	if [ -s "$fw/hwcfg.bin" ] && [ -s "$fw/persist/modem/cpsha" ]; then
		python3 "$base/luma-fold-modem.py" handover "$fw/hwcfg.bin" "$fw/persist/modem/cpsha" - "$fw/cp_handover.bin"
		log "no IMEI source on this kernel: the handover block carries none"
	else
		rm -f "$fw/cp_handover.bin"
		log "no hwcfg.bin or CP signature: no handover block"
	fi
fi

# The shared initramfs (85-luma-fold-firmware.sh) points firmware_class at
# $fwroot. Only set it if nothing did; never replace another root.
p=$(cat /sys/module/firmware_class/parameters/path)
if [ -z "$p" ]; then
	printf '%s' "$fwroot" > /sys/module/firmware_class/parameters/path
	log "firmware path was unset; set to $fwroot"
elif [ "$p" != "$fwroot" ]; then
	log "firmware path is $p, not $fwroot: the driver will not find its files"
fi
# sit-proxy first: its sockets exist before the SIT ports do, and it opens the
# ports as they appear, so ModemManager (running since boot, samsung-sit
# plugin) never falls back to the raw node. exynos-modem is wanted by the
# oem1 port's udev rule. The driver's debug lines (park/wake, relink, RFS) go
# to the journal through dynamic debug.
up() { cut -d' ' -f1 /proc/uptime; }
systemctl start sit-proxy.service && log "sit-proxy started at $(up)s ($(ls /run/sit-proxy 2>/dev/null | tr '\n' ' '))" ||
	log "sit-proxy failed to start"
if grep -q '^s5xxx_modem ' /proc/modules; then
	log "driver already loaded"
elif insmod "$base/s5xxx_modem.ko" $(grep -q "luma.fold_debug=1" /proc/cmdline && echo dyndbg=+p); then
	log "driver loaded at $(up)s"
else
	log "driver load failed"
fi
# The IMS configuration database, from the phone's own product partition, in
# its own unit so the staging stays short.
systemd-run --no-block --collect --unit=luma-fold-modem-imsdb /bin/bash "$base/luma-fold-modem-imsdb.sh" >/dev/null 2>&1 ||
	log "could not start the ims.db builder"
# No waiting here: the CP boot (about 3 s to ONLINE) and the daemons are the
# test unit's to watch and report; a probe failure shows up in its first lines.
sleep 1
log "probe: $(dmesg | grep -E 's5xxx-modem' | grep -E 'CP endpoint|error|TOC' | tail -2 | sed 's/.*s5xxx-modem modem: //' | tr '\n' ';') at $(up)s"
