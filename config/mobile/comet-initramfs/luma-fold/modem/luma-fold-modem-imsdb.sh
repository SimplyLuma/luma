#!/bin/bash
# Fold bring-up: build the IMS configuration database the samsung-sit plugin
# reads (ims.db beside cfg.db) from this phone's own product partition
# (/product/etc/CarrierSettings, Google's carrier protobufs) with
# carrier-settings-tool. product_<slot> lives inside super: its extents come
# from the LP metadata, super is set read-only and the extents are mapped
# read-only. Rebuilt only when the product extents, the tool or the defaults
# change. Nothing is written outside /var/lib/luma-fold.
set -u
base=/var/lib/luma-fold/modem
fw=/var/lib/luma-fold/firmware/google/s5400
mnt=/run/luma-fold-modem-product
log() { echo "luma-fold-modem-imsdb: $*"; }
up() { cut -d' ' -f1 /proc/uptime; }
tool=/var/lib/luma-fold/modem-stack/usr/bin/carrier-settings-tool
[ -x $tool ] || tool=/usr/bin/carrier-settings-tool
[ -x $tool ] || { log "no carrier-settings-tool"; exit 0; }
[ -d $fw/carrierconfig ] || { log "no staged carrierconfig directory"; exit 0; }
slot=$(sed -n 's/.*androidboot\.slot_suffix=\(_[ab]\).*/\1/p' /proc/cmdline); [ -n "$slot" ] || slot=_a
sup=/dev/disk/by-partlabel/super
[ -e $sup ] || { log "no super partition"; exit 0; }
blockdev --setro $sup || { log "super: cannot set read-only"; exit 0; }
ext=$(python3 $base/luma-fold-modem-lp.py $sup product$slot) || { log "product$slot not found in super"; exit 0; }
stamp="$(echo "$ext" | tr '\n' ' ')$(sha256sum $tool $base/aosp-carrier-defaults.txt | cut -c1-16 | tr '\n' ' ')"
if [ "$stamp" = "$(cat $base/.imsdb-stamp 2>/dev/null)" ] && [ -s $fw/carrierconfig/ims.db ]; then
	log "ims.db current ($(stat -c %s $fw/carrierconfig/ims.db) bytes)"
	exit 0
fi
cleanup() {
	mountpoint -q $mnt && umount $mnt
	dmsetup remove luma-fold-product 2>/dev/null
	[ -n "${loopdev:-}" ] && losetup -d $loopdev 2>/dev/null
}
trap cleanup EXIT
loopdev=
if [ "$(echo "$ext" | wc -l)" = 1 ]; then
	read -r off len <<< "$ext"
	loopdev=$(losetup --read-only --find --show -o $off --sizelimit $len $sup) || { log "no read-only loop device"; exit 0; }
	dev=$loopdev
else
	dmsetup create luma-fold-product --readonly --table "$(echo "$ext" |
		awk -v d="$(readlink -f $sup)" '{ printf "%d %d linear %s %d\n", s, $2 / 512, d, $1 / 512; s += $2 / 512 }')" ||
		{ log "no read-only mapping"; exit 0; }
	dev=/dev/mapper/luma-fold-product
fi
mkdir -p $mnt
opts=ro,nodev,nosuid,noexec
[ "$(blkid -o value -s TYPE $dev 2>/dev/null)" = erofs ] || opts=$opts,norecovery
mount -o $opts $dev $mnt || { log "product$slot: mount failed"; exit 0; }
cs=$mnt/etc/CarrierSettings
[ -d $cs ] || { log "product$slot has no etc/CarrierSettings"; exit 0; }
t0=$(up)
if $tool build-db $cs $base/aosp-carrier-defaults.txt $fw/carrierconfig/ims.db.new > /run/luma-fold-modem-imsdb.out 2>&1; then
	mv $fw/carrierconfig/ims.db.new $fw/carrierconfig/ims.db
	chmod 0644 $fw/carrierconfig/ims.db
	echo "$stamp" > $base/.imsdb-stamp
	log "ims.db built from $(ls $cs | wc -l) CarrierSettings files in $(awk -v a=$t0 -v b=$(up) 'BEGIN{printf "%.1f", b-a}')s, $(stat -c %s $fw/carrierconfig/ims.db) bytes"
	python3 - $fw/carrierconfig/ims.db <<'PY'
import sqlite3, sys
db = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
for (t,) in db.execute("select name from sqlite_master where type='table'"):
    n = db.execute("select count(*) from %s" % t).fetchone()[0]
    print("luma-fold-modem-imsdb: table %s: %d rows" % (t, n))
PY
else
	rm -f $fw/carrierconfig/ims.db.new
	log "build-db failed: $(tail -3 /run/luma-fold-modem-imsdb.out | tr '\n' ' ')"
fi
tail -3 /run/luma-fold-modem-imsdb.out | sed 's/^/luma-fold-modem-imsdb: tool: /'
