#!/usr/bin/env bash
# Pixel 9 (tokay) Luma install: Fold shell6 userdata + luma-tokay boot image.
# userdata.sparse: zstd -d of luma-build:/srv/luma-fold-image/image-shell6/output/*userdata.sparse.zst
# Usage: install-tokay.sh ram    -> flash userdata, RAM-boot the kernel (boot_a untouched)
#        install-tokay.sh flash  -> also write boot_a so Luma boots on its own
set -euo pipefail
S=47091FDAQ000NZ
D=$(cd "$(dirname "$0")" && pwd)
MODE=${1:-ram}
fastboot devices | grep -q "^$S" || { echo "Pixel 9 ($S) not in fastboot"; exit 1; }
[ "$(fastboot -s $S getvar product 2>&1 | awk '/product:/{print $2}')" = tokay ] || { echo "not tokay"; exit 1; }
(cd "$D" && shasum -a 256 -c boot.sha256)
if [ ! -f "$D/.userdata-flashed" ]; then
	fastboot -s $S flash userdata "$D/userdata.sparse"
	touch "$D/.userdata-flashed"
fi
if [ "$MODE" = flash ]; then
	fastboot -s $S flash boot_a "$D/boot.img"
	fastboot -s $S reboot
else
	fastboot -s $S boot "$D/boot.img"
fi
echo "booting; Luma on USB at 172.16.42.1 in ~60 s (login luma / 1234)"
