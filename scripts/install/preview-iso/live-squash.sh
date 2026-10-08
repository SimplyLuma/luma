#!/bin/bash
# Stage 3 (tools container): compress the labelled live root.
set -euo pipefail
L=/w/live
mkdir -p $L/iso/LiveOS $L/iso/images/luma
rm -f $L/iso/LiveOS/squashfs.img
mksquashfs $L/rootfs $L/iso/LiveOS/squashfs.img -comp xz -b 1M -Xdict-size 100% -xattrs -noappend -processors 3
cp $L/boot/vmlinuz $L/boot/initrd.img $L/iso/images/luma/
ls -la $L/iso/LiveOS $L/iso/images/luma
echo SQUASH-DONE
