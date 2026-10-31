#!/bin/bash
# Fold GNSS: copy kepler.bin (and gps.cfg, for reference) from the phone's own
# vendor_<slot> inside super to the firmware root the modem driver reads
# (google/s5400/kepler.bin under /var/lib/luma-fold/firmware). Read-only
# throughout: super is set read-only, vendor is mapped read-only and mounted
# ro,norecovery, and unmapped again.
set -u
fw=/var/lib/luma-fold/firmware/google/s5400
d=/var/lib/luma-fold/gnss
mnt=/run/luma-fold-gnss/vendor
dm=luma-fold-gnss-vendor
log() { echo "luma-gnss: stage: $*"; }
umask 077
slot=$(sed -n 's/.*androidboot\.slot_suffix=\(_[ab]\).*/\1/p' /proc/cmdline)
[ -n "$slot" ] || slot=_a
loop= map=
cleanup() {
	mountpoint -q $mnt && umount $mnt
	[ -n "$map" ] && dmsetup remove $dm 2>/dev/null
	[ -n "$loop" ] && losetup -d "$loop" 2>/dev/null
}
trap cleanup EXIT
# hook 87 writes the geoclue drop-ins from the initramfs, unlabeled
restorecon -F /etc/geoclue/conf.d/*.conf 2>/dev/null
sup=
for i in $(seq 40); do
	[ -e /dev/disk/by-partlabel/super ] && { sup=/dev/disk/by-partlabel/super; break; }
	sleep 0.5
done
[ -n "$sup" ] || { log "no super partition"; exit 0; }
[ -f $d/lp.py ] || { log "no LP parser"; exit 0; }
blockdev --setro "$sup" || { log "super: cannot set read-only"; exit 0; }
ext=$(python3 $d/lp.py "$sup" "vendor$slot") || { log "vendor$slot not found in super"; exit 0; }
mkdir -p $mnt "$fw"
if [ "$(echo "$ext" | wc -l)" = 1 ]; then
	read -r off len <<< "$ext"
	loop=$(losetup --read-only --find --show -o "$off" --sizelimit "$len" "$sup") || { log "no read-only loop"; exit 0; }
	dev=$loop
else
	dmsetup create $dm --readonly --table "$(echo "$ext" |
		awk -v d="$(readlink -f "$sup")" '{ printf "%d %d linear %s %d\n", s, $2 / 512, d, $1 / 512; s += $2 / 512 }')" ||
		{ log "no read-only mapping"; exit 0; }
	map=1; dev=/dev/mapper/$dm
fi
mount -o ro,norecovery,nodev,nosuid,noexec "$dev" $mnt || { log "vendor$slot mount failed"; exit 0; }
if [ -f $mnt/firmware/kepler.bin ]; then
	log "vendor kepler.bin: $(stat -c %s $mnt/firmware/kepler.bin) bytes, sha256 $(sha256sum $mnt/firmware/kepler.bin | cut -c1-16)"
	# the header fields stock gnssd sets from gps.cfg (falls back to the raw image)
	if msg=$(python3 -I $d/luma-fold-gnss-header.py $mnt/firmware/kepler.bin "$fw/kepler.bin.new" 2>&1); then
		log "header: $msg"
	else
		log "header: $msg"
		cp $mnt/firmware/kepler.bin "$fw/kepler.bin.new"
	fi
	mv "$fw/kepler.bin.new" "$fw/kepler.bin" &&
		log "kepler.bin staged ($(stat -c %s "$fw/kepler.bin") bytes, sha256 $(sha256sum "$fw/kepler.bin" | cut -c1-16))"
else
	log "vendor$slot has no firmware/kepler.bin: $(ls $mnt/firmware | grep -i -E 'gnss|kepler|gps' | tr '\n' ' ')"
fi
[ -f $mnt/etc/gnss/gps.cfg ] && cp $mnt/etc/gnss/gps.cfg $d/gps.cfg
exit 0
