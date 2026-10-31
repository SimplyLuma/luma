#!/bin/sh
# Tensor Pixels: the bootloader counts down the slot's retry_count in devinfo
# on every boot that isn't marked successful and falls back to fastboot when
# it runs out. Android's boot-control HAL marks the boot; Luma has none, so a
# runtime unit marks the booted slot successful once the session has been up
# for two minutes (devinfo v3: ab_data at offset 48, 4 bytes per slot:
# retry_count, flags bit0 unbootable, bit1 successful).
[ -d /sysroot/etc ] || exit 0
mkdir -p /run/luma-fold /run/systemd/system/multi-user.target.wants
cat > /run/luma-fold/boot-success.sh <<"SH"
#!/bin/sh
s=$(sed -n 's/.*androidboot.slot_suffix=_\([ab]\).*/\1/p' /proc/cmdline)
d=$(readlink -f /dev/disk/by-partlabel/devinfo)
[ -n "$s" ] && [ -b "$d" ] && [ "$(head -c 4 $d)" = DEVI ] || exit 0
o=48; [ "$s" = b ] && o=52
f=$(od -An -j $((o + 1)) -N 1 -t u1 $d | tr -d " ")
[ $((f & 2)) -eq 2 ] && [ $((f & 1)) -eq 0 ] && exit 0
nf=$(( (f | 2) & ~1 ))
printf "$(printf '\\%03o\\%03o' 7 $nf)" | dd of=$d bs=1 seek=$o count=2 conv=notrunc,fsync status=none
echo "marked slot $s successful ($(od -An -j $o -N 2 -t x1 $d | tr -d ' '))"
SH
chmod 0755 /run/luma-fold/boot-success.sh
cat > /run/systemd/system/luma-fold-boot-success.timer <<"UNIT"
[Unit]
Description=Mark the booted slot successful
[Timer]
OnBootSec=120
UNIT
cat > /run/systemd/system/luma-fold-boot-success.service <<"UNIT"
[Unit]
Description=Mark the booted slot successful
[Service]
Type=oneshot
ExecStart=/run/luma-fold/boot-success.sh
UNIT
ln -sf ../luma-fold-boot-success.timer /run/systemd/system/multi-user.target.wants/
